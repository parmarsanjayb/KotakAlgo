from core.bus import event_bus, EventModel
from core.logging import get_logger
from backtest.models import (
    BacktestConfig, BacktestProgress, BacktestStartedEvent,
    BacktestProgressEvent, BacktestCompletedEvent
)

logger = get_logger("backtest_publisher")

class BacktestPublisher:
    """
    Publishes backtesting lifecycle events onto the Event Bus.
    """
    async def publish_started(self, backtest_id: str, config: BacktestConfig) -> None:
        evt = BacktestStartedEvent(backtest_id=backtest_id, config=config)
        await event_bus.publish(EventModel(
            event_type="backtest_started",
            source_agent="backtest_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_progress(self, progress: BacktestProgress) -> None:
        evt = BacktestProgressEvent(progress=progress)
        await event_bus.publish(EventModel(
            event_type="backtest_progress",
            source_agent="backtest_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_completed(self, backtest_id: str, progress: BacktestProgress, metrics: dict) -> None:
        evt = BacktestCompletedEvent(backtest_id=backtest_id, progress=progress, metrics=metrics)
        await event_bus.publish(EventModel(
            event_type="backtest_completed",
            source_agent="backtest_engine",
            payload=evt.model_dump(mode="json")
        ))
