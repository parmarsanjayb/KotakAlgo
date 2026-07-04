from core.bus import event_bus, EventModel
from core.logging import get_logger
from paper.models import (
    PaperTradingConfig, PaperTradingState, PaperTradeStartedEvent,
    PaperOrderPlacedEvent, PaperOrderFilledEvent, PaperTradeClosedEvent, PaperTradingStoppedEvent
)

logger = get_logger("paper_publisher")

class PaperTradingPublisher:
    """
    Publishes paper trading lifecycle and order execution events onto the Event Bus.
    """
    async def publish_started(self, session_id: str, config: PaperTradingConfig) -> None:
        evt = PaperTradeStartedEvent(session_id=session_id, config=config)
        await event_bus.publish(EventModel(
            event_type="paper_trade_started",
            source_agent="paper_trading_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_order_placed(self, session_id: str, order_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
        evt = PaperOrderPlacedEvent(session_id=session_id, order_id=order_id, symbol=symbol, side=side, quantity=quantity, price=price)
        await event_bus.publish(EventModel(
            event_type="paper_order_placed",
            source_agent="paper_trading_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_order_filled(self, session_id: str, order_id: str, symbol: str, side: str, quantity: float, price: float, latency: float) -> None:
        evt = PaperOrderFilledEvent(session_id=session_id, order_id=order_id, symbol=symbol, side=side, quantity=quantity, price=price, latency_ms=latency)
        await event_bus.publish(EventModel(
            event_type="paper_order_filled",
            source_agent="paper_trading_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_trade_closed(self, session_id: str, symbol: str, pnl: float, duration: float) -> None:
        evt = PaperTradeClosedEvent(session_id=session_id, symbol=symbol, pnl=pnl, duration=duration)
        await event_bus.publish(EventModel(
            event_type="paper_trade_closed",
            source_agent="paper_trading_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_stopped(self, session_id: str) -> None:
        evt = PaperTradingStoppedEvent(session_id=session_id)
        await event_bus.publish(EventModel(
            event_type="paper_trade_stopped",
            source_agent="paper_trading_engine",
            payload=evt.model_dump(mode="json")
        ))
