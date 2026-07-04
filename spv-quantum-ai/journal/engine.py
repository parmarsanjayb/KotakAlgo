import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from core.bus import event_bus, EventModel
from core.logging import get_logger

from journal.models import TradeRecord, DecisionAudit
from journal.repository import TradeHistoryRepository
from journal.publisher import JournalPublisher

logger = get_logger("trade_journal_engine")

class TradeJournalEngine:
    """
    Trade Journal & Audit Engine.
    Coordinates database storage, publishes updates, and calculates PNL performance statistics.
    No trading logic.
    """
    def __init__(self) -> None:
        self.repo = TradeHistoryRepository()
        self.publisher = JournalPublisher()
        self._running = False
        self._active_trades: Dict[str, TradeRecord] = {}  # Tracks symbol -> active TradeRecord
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("order_filled", self._on_order_filled)
        await event_bus.subscribe("decision_score", self._on_decision_score)
        logger.info("TradeJournalEngine started.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("order_filled", self._on_order_filled)
        await event_bus.unsubscribe("decision_score", self._on_decision_score)
        logger.info("TradeJournalEngine stopped.")

    async def _on_order_filled(self, event: EventModel) -> None:
        try:
            payload = event.payload
            order_data = payload.get("order", payload)
            
            symbol = order_data.get("symbol", "UNKNOWN")
            side = order_data.get("side", "BUY").upper()
            qty = float(order_data.get("filled_quantity") or order_data.get("quantity") or 0.0)
            price = float(order_data.get("avg_fill_price") or order_data.get("price") or 0.0)
            order_id = order_data.get("order_id", "UNKNOWN")
            broker_order_id = order_data.get("broker_order_id")
            latency = float(order_data.get("broker_latency_ms", 0.0))

            if qty <= 0 or price <= 0:
                return

            async with self._lock:
                active = self._active_trades.get(symbol)
                
                # Setup details
                segment = "Equity"
                if symbol.endswith("FUT") or "FUT" in symbol:
                    segment = "Futures"
                elif any(x in symbol for x in ["CE", "PE", "OPT"]):
                    segment = "Options"

                if not active:
                    from charges import charges_engine
                    # Calculate entry charges
                    entry_chg = await charges_engine.calculate_charges(order_id, symbol, side, qty, price, segment)
                    
                    # New Entry TradeRecord
                    trade = TradeRecord(
                        order_id=order_id,
                        broker_order_id=broker_order_id,
                        symbol=symbol,
                        segment=segment,
                        side=side,
                        quantity=qty,
                        entry_price=price,
                        execution_latency=latency,
                        strategy_name=order_data.get("strategy_name", "trend_strategy"),
                        scanner_name=order_data.get("scanner_name"),
                        market_regime=order_data.get("market_regime"),
                        decision_score=order_data.get("decision_score"),
                        risk_score=order_data.get("risk_score"),
                        entry_cost=round(price * qty, 2),
                        charges=round(entry_chg.brokerage, 4),
                        taxes=round(entry_chg.total_charges - entry_chg.brokerage, 4),
                        total_charges=round(entry_chg.total_charges, 4),
                        breakeven_price=round(entry_chg.breakeven_price, 4),
                        cost_pct=round(entry_chg.cost_pct, 4)
                    )
                    self._active_trades[symbol] = trade
                    
                    # Persist to database
                    entry_id = await self.repo.save_trade_record(trade)
                    await self.publisher.publish_trade_recorded(trade)
                    await self.publisher.publish_journal_updated(entry_id, "trade_record")
                else:
                    # Exit TradeRecord (different side)
                    if active.side != side:
                        active.exit_price = price
                        
                        from charges import charges_engine
                        # Calculate exit charges
                        exit_chg = await charges_engine.calculate_charges(order_id, symbol, side, qty, price, segment)
                        
                        active.exit_cost = round(price * qty, 2)
                        active.charges += round(exit_chg.brokerage, 4)
                        active.taxes += round(exit_chg.total_charges - exit_chg.brokerage, 4)
                        active.total_charges += round(exit_chg.total_charges, 4)
                        
                        # Calculate realized gross P&L
                        if active.side == "BUY":
                            active.gross_pnl = (price - active.entry_price) * qty
                        else:
                            active.gross_pnl = (active.entry_price - price) * qty
                            
                        active.realized_pnl = active.gross_pnl
                        active.net_pnl = active.gross_pnl - active.total_charges
                        
                        total_turnover = (active.entry_price * active.quantity) + (price * qty)
                        active.cost_pct = round((active.total_charges / total_turnover * 100.0) if total_turnover > 0 else 0.0, 4)
                        
                        # Calculate holding duration
                        entry_time = active.timestamp
                        if isinstance(entry_time, str):
                            entry_time = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                        active.holding_duration = (datetime.now(timezone.utc) - entry_time).total_seconds()
                        
                        # Update database
                        await self.repo.update_trade_record(active)
                        await self.publisher.publish_trade_closed(active)
                        await self.publisher.publish_journal_updated(0, "trade_record")
                        
                        # Remove active tracker
                        self._active_trades.pop(symbol, None)
                    else:
                        # Adding to same side
                        new_qty = active.quantity + qty
                        new_avg = ((active.entry_price * active.quantity) + (price * qty)) / new_qty
                        active.entry_price = new_avg
                        active.quantity = new_qty
                        await self.repo.update_trade_record(active)
                        await self.publisher.publish_trade_updated(active)
                        await self.publisher.publish_journal_updated(0, "trade_record")

        except Exception as e:
            logger.error(f"Error recording trade filled: {e}")

    async def _on_decision_score(self, event: EventModel) -> None:
        try:
            payload = event.payload
            score_data = payload.get("decision_score", payload)
            
            symbol = score_data.get("symbol", "UNKNOWN")
            confidence = float(score_data.get("overall_confidence", 0.0))
            
            # Record DecisionAudit
            audit = DecisionAudit(
                symbol=symbol,
                decision_confidence=confidence,
                market_analysis_summary=score_data.get("reasoning"),
                indicator_snapshot=score_data.get("component_scores", {}),
                strategy_match=score_data.get("recommended_strategy"),
                risk_validation=score_data.get("risk_status")
            )
            
            entry_id = await self.repo.save_decision_audit(audit)
            await self.publisher.publish_journal_updated(entry_id, "decision_audit")
            
        except Exception as e:
            logger.error(f"Error recording decision score audit: {e}")

    # ── Performance Summaries ─────────────────────────────────────────────────

    async def get_performance_stats(self) -> Dict[str, Any]:
        trades = await self.repo.get_all_trades()
        
        total_pnl = sum(t.realized_pnl for t in trades)
        win_trades = [t for t in trades if t.realized_pnl > 0]
        loss_trades = [t for t in trades if t.realized_pnl < 0]
        
        win_rate = (len(win_trades) / len(trades) * 100.0) if trades else 0.0
        
        # Calculate daily summaries
        today = datetime.now(timezone.utc).date()
        daily_pnl = 0.0
        weekly_pnl = 0.0
        
        for t in trades:
            t_time = t.timestamp
            if isinstance(t_time, str):
                t_time = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
            
            # Daily PNL
            if t_time.date() == today:
                daily_pnl += t.realized_pnl
                
            # Weekly PNL (last 7 days)
            if (datetime.now(timezone.utc) - t_time).days <= 7:
                weekly_pnl += t.realized_pnl

        # Strategy performance
        strat_pnl: Dict[str, float] = {}
        for t in trades:
            name = t.strategy_name or "trend_strategy"
            strat_pnl[name] = strat_pnl.get(name, 0.0) + t.realized_pnl

        return {
            "total_trades": len(trades),
            "win_rate": round(win_rate, 2),
            "total_realized_pnl": round(total_pnl, 2),
            "daily_summary_pnl": round(daily_pnl, 2),
            "weekly_summary_pnl": round(weekly_pnl, 2),
            "monthly_summary_pnl": round(total_pnl, 2),  # SQLite default complete history
            "strategy_performance": strat_pnl
        }

# Singleton
trade_journal_engine = TradeJournalEngine()
