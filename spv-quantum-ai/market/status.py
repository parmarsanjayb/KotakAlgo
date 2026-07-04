from market.models import (
    MarketSession, MarketStatusChangedEvent,
    MarketOpenEvent, MarketCloseEvent
)
from core.bus import event_bus, EventModel
from core.logging import get_logger

logger = get_logger("market_status_manager")

class MarketStatusManager:
    """
    Tracks exchange session state and publishes typed events on every transition.
    Transitions: CLOSED → PRE_OPEN → OPEN → CLOSED | HALTED.
    """

    def __init__(self) -> None:
        self._status: MarketSession = MarketSession.CLOSED

    def get_status(self) -> MarketSession:
        return self._status

    async def set_status(self, new_status: MarketSession) -> None:
        if self._status == new_status:
            return
        old = self._status
        self._status = new_status
        logger.info("Market session changed", old=old.value, new=new_status.value)

        # Publish generic status change
        changed_evt = MarketStatusChangedEvent(old_status=old, new_status=new_status)
        await event_bus.publish(EventModel(
            event_type   = "market_status_changed",
            source_agent = "market_status_manager",
            payload      = changed_evt.model_dump(),
            priority     = 1,
        ))

        # Publish specific open / close events
        if new_status == MarketSession.OPEN:
            await event_bus.publish(EventModel(
                event_type   = "market_open",
                source_agent = "market_status_manager",
                payload      = MarketOpenEvent().model_dump(),
                priority     = 1,
            ))
        elif new_status == MarketSession.CLOSED:
            await event_bus.publish(EventModel(
                event_type   = "market_close",
                source_agent = "market_status_manager",
                payload      = MarketCloseEvent().model_dump(),
                priority     = 1,
            ))
