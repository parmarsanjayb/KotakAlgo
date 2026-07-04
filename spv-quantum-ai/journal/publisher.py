from core.bus import event_bus, EventModel
from core.logging import get_logger
from journal.models import (
    TradeRecord, TradeRecordedEvent, TradeUpdatedEvent, TradeClosedEvent, JournalUpdatedEvent
)

logger = get_logger("journal_publisher")

class JournalPublisher:
    """
    Publishes journal and audit events onto the Event Bus.
    """
    async def publish_trade_recorded(self, trade: TradeRecord) -> None:
        evt = TradeRecordedEvent(trade=trade)
        await event_bus.publish(EventModel(
            event_type="trade_recorded",
            source_agent="trade_journal_engine",
            payload=evt.model_dump()
        ))

    async def publish_trade_updated(self, trade: TradeRecord) -> None:
        evt = TradeUpdatedEvent(trade=trade)
        await event_bus.publish(EventModel(
            event_type="trade_updated",
            source_agent="trade_journal_engine",
            payload=evt.model_dump()
        ))

    async def publish_trade_closed(self, trade: TradeRecord) -> None:
        evt = TradeClosedEvent(trade=trade)
        await event_bus.publish(EventModel(
            event_type="trade_closed",
            source_agent="trade_journal_engine",
            payload=evt.model_dump()
        ))

    async def publish_journal_updated(self, entry_id: int, entry_type: str) -> None:
        evt = JournalUpdatedEvent(entry_id=entry_id, entry_type=entry_type)
        await event_bus.publish(EventModel(
            event_type="journal_updated",
            source_agent="trade_journal_engine",
            payload=evt.model_dump()
        ))
