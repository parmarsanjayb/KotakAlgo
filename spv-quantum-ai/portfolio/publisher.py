from core.bus import event_bus, EventModel
from core.logging import get_logger
from portfolio.models import (
    Position, PortfolioSummary, PortfolioUpdatedEvent, PositionOpenedEvent,
    PositionUpdatedEvent, PositionClosedEvent, PnLUpdatedEvent, ExposureUpdatedEvent
)

logger = get_logger("portfolio_publisher")

class PortfolioPublisher:
    """
    Publishes portfolio state updates onto the Event Bus.
    """
    async def publish_portfolio_updated(self, summary: PortfolioSummary) -> None:
        evt = PortfolioUpdatedEvent(summary=summary)
        await event_bus.publish(EventModel(
            event_type="portfolio_summary_updated",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))

    async def publish_position_opened(self, position: Position) -> None:
        evt = PositionOpenedEvent(position=position)
        await event_bus.publish(EventModel(
            event_type="position_opened",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))

    async def publish_position_updated(self, position: Position) -> None:
        evt = PositionUpdatedEvent(position=position)
        await event_bus.publish(EventModel(
            event_type="position_updated",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))

    async def publish_position_closed(self, position: Position) -> None:
        evt = PositionClosedEvent(position=position)
        await event_bus.publish(EventModel(
            event_type="position_closed",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))

    async def publish_pnl_updated(self, realized: float, unrealized: float, mtm: float) -> None:
        evt = PnLUpdatedEvent(realized_pnl=realized, unrealized_pnl=unrealized, mtm=mtm)
        await event_bus.publish(EventModel(
            event_type="pnl_updated",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))

    async def publish_exposure_updated(self, exposure: float, segment_exp: dict) -> None:
        evt = ExposureUpdatedEvent(portfolio_exposure=exposure, segment_exposure=segment_exp)
        await event_bus.publish(EventModel(
            event_type="exposure_updated",
            source_agent="portfolio_engine",
            payload=evt.model_dump()
        ))
