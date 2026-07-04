import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from core.bus import event_bus, EventModel
from core.logging import get_logger
from brokers.manager import broker_manager

from paper.models import PaperTradingConfig, PaperTradingState
from paper.publisher import PaperTradingPublisher

logger = get_logger("paper_trading_engine")

class PaperTradingEngine:
    """
    Enterprise Paper Trading Engine.
    Executes the complete trading pipeline exactly like Live Trading.
    Simulates orders on PaperBroker and tracks virtual portfolio balance and statistics in real time.
    """
    def __init__(self) -> None:
        self.publisher = PaperTradingPublisher()
        self.state = PaperTradingState(session_id="", is_running=False)
        self.config = PaperTradingConfig()
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("PaperTradingEngine initialized.")

    async def stop(self) -> None:
        self._running = False
        await self.stop_session()

    async def start_session(self, config: PaperTradingConfig) -> str:
        """Starts a live paper trading session."""
        if self.state.is_running:
            return self.state.session_id

        session_id = f"PPS-{uuid.uuid4().hex[:8]}"
        self.config = config
        self.state = PaperTradingState(
            session_id=session_id,
            is_running=True,
            virtual_capital=config.initial_capital,
            virtual_pnl=0.0
        )

        # Configure Active Broker to PaperBroker
        await broker_manager.load("paper_broker")
        broker = broker_manager.get_active()
        if hasattr(broker, "_positions"):
            broker._positions.clear()
            broker._orders.clear()
            broker._trades.clear()
            broker._balance = config.initial_capital
            broker._used_margin = 0.0
            broker._partial_fill_rate = 0.0
            broker._rejection_rate = 0.0

        # Subscriptions
        await event_bus.subscribe("order_submitted", self._on_order_submitted)
        await event_bus.subscribe("order_filled", self._on_order_filled)
        await event_bus.subscribe("trade_closed", self._on_trade_closed)

        await self.publisher.publish_started(session_id, config)
        logger.info(f"Paper trading session {session_id} started successfully.")
        return session_id

    async def stop_session(self) -> None:
        """Stops the active paper trading session."""
        if not self.state.is_running:
            return

        session_id = self.state.session_id
        self.state.is_running = False

        # Unsubscribe
        await event_bus.unsubscribe("order_submitted", self._on_order_submitted)
        await event_bus.unsubscribe("order_filled", self._on_order_filled)
        await event_bus.unsubscribe("trade_closed", self._on_trade_closed)

        await self.publisher.publish_stopped(session_id)
        logger.info(f"Paper trading session {session_id} stopped.")

    # ── Event Callbacks ───────────────────────────────────────────────────────

    async def _on_order_submitted(self, event: EventModel) -> None:
        try:
            payload = event.payload
            order_data = payload.get("order", payload)
            
            await self.publisher.publish_order_placed(
                session_id=self.state.session_id,
                order_id=order_data.get("order_id", "UNKNOWN"),
                symbol=order_data.get("symbol", "UNKNOWN"),
                side=order_data.get("side", "BUY"),
                quantity=float(order_data.get("quantity", 0.0)),
                price=float(order_data.get("price", 0.0))
            )
        except Exception as e:
            logger.error(f"Error handling order submitted: {e}")

    async def _on_order_filled(self, event: EventModel) -> None:
        try:
            payload = event.payload
            order_data = payload.get("order", payload)
            
            await self.publisher.publish_order_filled(
                session_id=self.state.session_id,
                order_id=order_data.get("order_id", "UNKNOWN"),
                symbol=order_data.get("symbol", "UNKNOWN"),
                side=order_data.get("side", "BUY"),
                quantity=float(order_data.get("filled_quantity") or order_data.get("quantity") or 0.0),
                price=float(order_data.get("avg_fill_price") or order_data.get("price") or 0.0),
                latency=float(order_data.get("broker_latency_ms", 0.0))
            )
        except Exception as e:
            logger.error(f"Error handling order filled: {e}")

    async def _on_trade_closed(self, event: EventModel) -> None:
        try:
            payload = event.payload
            trade_data = payload.get("trade", payload)
            
            pnl = float(trade_data.get("net_pnl") or trade_data.get("realized_pnl", 0.0))
            duration = float(trade_data.get("holding_duration", 0.0))
            symbol = trade_data.get("symbol", "UNKNOWN")

            self.state.trades_executed += 1
            self.state.virtual_pnl += pnl
            self.state.virtual_capital += pnl
            
            # Recalculate Win Rate
            from analytics.engine import performance_analytics_engine
            stats = await performance_analytics_engine.recalculate_metrics()
            self.state.win_rate = stats.win_rate

            await self.publisher.publish_trade_closed(
                session_id=self.state.session_id,
                symbol=symbol,
                pnl=pnl,
                duration=duration
            )
        except Exception as e:
            logger.error(f"Error handling trade closed: {e}")

    # ── Dashboard metrics lookup ──────────────────────────────────────────────

    async def get_dashboard_status(self) -> Dict[str, Any]:
        from portfolio.engine import portfolio_engine
        open_positions = len(await portfolio_engine.positions.get_open_positions())
        
        return {
            "session_id": self.state.session_id,
            "is_running": self.state.is_running,
            "virtual_capital": round(self.state.virtual_capital, 2),
            "virtual_pnl": round(self.state.virtual_pnl, 2),
            "trades_executed": self.state.trades_executed,
            "win_rate": self.state.win_rate,
            "current_positions": open_positions
        }

# Singleton
paper_trading_engine = PaperTradingEngine()
