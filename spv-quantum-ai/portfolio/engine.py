import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from core.bus import event_bus, EventModel
from core.logging import get_logger
from brokers.manager import broker_manager

from portfolio.models import Position, PositionState, PortfolioSummary
from portfolio.managers import PositionManager, PnLManager, ExposureCalculator
from portfolio.publisher import PortfolioPublisher

logger = get_logger("portfolio_engine")

class PortfolioEngine:
    """
    Portfolio & Position Management Engine.
    The SINGLE SOURCE OF TRUTH for portfolio state.
    Calculates exposures, realized/unrealized P&L, MTM, and tracks margins.
    Does not place trades.
    """
    def __init__(self) -> None:
        self.positions = PositionManager()
        self.pnl_mgr = PnLManager()
        self.exposure_calc = ExposureCalculator()
        self.publisher = PortfolioPublisher()
        
        self.summaries: Dict[str, PortfolioSummary] = {}
        self._running = False
        self._lock = asyncio.Lock()

    @property
    def summary(self) -> PortfolioSummary:
        """Fallback property for backwards compatibility with single-tenant code."""
        return self.get_summary("admin")

    @summary.setter
    def summary(self, val: PortfolioSummary) -> None:
        """Fallback setter."""
        self.summaries["admin"] = val

    def get_summary(self, user_id: str = "admin") -> PortfolioSummary:
        if user_id not in self.summaries:
            self.summaries[user_id] = PortfolioSummary()
        return self.summaries[user_id]

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Subscribe to trades/fills and real-time prices
        await event_bus.subscribe("order_filled", self._on_order_filled)
        await event_bus.subscribe("tick", self._on_tick)
        logger.info("PortfolioEngine started and subscribed to events.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("order_filled", self._on_order_filled)
        await event_bus.unsubscribe("tick", self._on_tick)
        logger.info("PortfolioEngine stopped.")

    async def _on_order_filled(self, event: EventModel) -> None:
        try:
            payload = event.payload
            # Extract order details
            # Can be nested under "order" if published by execution_engine
            order_data = payload.get("order", payload)
            user_id = order_data.get("user_id", "admin")
            
            symbol = order_data.get("symbol", "UNKNOWN")
            side = order_data.get("side", "BUY")
            qty = float(order_data.get("filled_quantity") or order_data.get("quantity") or 0.0)
            price = float(order_data.get("avg_fill_price") or order_data.get("price") or 0.0)
            
            if qty <= 0 or price <= 0:
                return

            # Update position
            pos, action = await self.positions.update_on_fill(symbol, side, qty, price, user_id=user_id)
            if pos and order_data.get("order_id") is not None:
                from charges import charges_engine
                order_id = order_data.get("order_id")
                chg = await charges_engine.calculate_charges(order_id, symbol, side, qty, price)
                pos.realized_pnl -= chg.total_charges
                pos.updated_at = datetime.now(timezone.utc)
                
                if action == "OPENED":
                    await self.publisher.publish_position_opened(pos)
                elif action == "UPDATED":
                    await self.publisher.publish_position_updated(pos)
                elif action == "CLOSED":
                    await self.publisher.publish_position_closed(pos)
            
            # Recalculate portfolio-level summaries
            await self.recalculate_summary(user_id=user_id)
        except Exception as e:
            logger.error("Error processing order filled in PortfolioEngine", error=str(e))

    async def _on_tick(self, event: EventModel) -> None:
        try:
            # "tick" events are published as TickEvent{event_id, tick: MarketData},
            # not a flat {symbol, ltp} dict — reading those keys at the top level
            # always missed, so this handler never actually ran and every
            # position's LTP stayed frozen at its fill price forever.
            tick = event.payload.get("tick", event.payload)
            symbol = tick.get("symbol")
            ltp = float(tick.get("ltp", 0.0))

            if not symbol or ltp <= 0:
                return
                
            updated_positions = await self.positions.update_ltp(symbol, ltp)
            for pos in updated_positions:
                # Recalculate summaries on price update for each user holding the symbol
                await self.recalculate_summary(user_id=pos.user_id)
        except Exception as e:
            logger.error("Error processing tick in PortfolioEngine", error=str(e))

    async def recalculate_summary(self, user_id: str = "admin") -> PortfolioSummary:
        """
        Compiles the capital allocations, positions PNLs, and exposures.
        """
        async with self._lock:
            # 1. Fetch capital details from active broker
            capital_val = 100000.0
            margin_val = 0.0
            broker_name = "paper_broker"
            try:
                broker = broker_manager.get_active()
                broker_name = broker.name
                bal_resp = await broker.get_balance()
                if bal_resp.success and bal_resp.data:
                    capital_val = float(bal_resp.data.get("equity", 100000.0))
                    margin_val = float(bal_resp.data.get("used_margin", 0.0))
            except Exception as e:
                logger.error("Failed to query broker balance in PortfolioEngine", error=str(e))

            # 2. Get all positions
            all_pos = await self.positions.get_all_positions(user_id=user_id)

            # 3. Calculate PNL
            realized, unrealized, mtm = self.pnl_mgr.calculate_pnl(all_pos)

            # 4. Calculate Exposure
            exposure, segment_dist, sector_dist = self.exposure_calc.calculate_exposure(all_pos)

            # 5. Build Summary
            broker_dist = {broker_name: 100.0} if exposure > 0 else {}
            
            summary = PortfolioSummary(
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                mtm=mtm,
                available_capital=capital_val,
                utilized_margin=margin_val,
                portfolio_exposure=exposure,
                segment_distribution=segment_dist,
                sector_distribution=sector_dist,
                broker_distribution=broker_dist
            )
            
            self.summaries[user_id] = summary

            # 6. Publish Events
            await self.publisher.publish_portfolio_updated(summary)
            await self.publisher.publish_pnl_updated(realized, unrealized, mtm)
            await self.publisher.publish_exposure_updated(exposure, segment_dist)

            # Also publish a portfolio_update event that RiskEngine listens to!
            # RiskEngine handles portfolio_update events containing equity & realized_pnl
            await event_bus.publish(EventModel(
                event_type="portfolio_update",
                source_agent="portfolio_engine",
                payload={
                    "user_id": user_id,
                    "equity": capital_val,
                    "realized_pnl": realized,
                    "drawdown_percent": 0.0,  # Calculated dynamically by drawdown_mgr
                    "open_orders_count": len([p for p in all_pos if p.state in (PositionState.OPEN, PositionState.PARTIAL)])
                }
            ))

            return summary

# Singleton
portfolio_engine = PortfolioEngine()
