from core.bus import event_bus, EventModel
from indicators.models import IndicatorResult, IndicatorEvent
from indicators.cache import IndicatorCache
from core.logging import get_logger

logger = get_logger("indicator_publisher")

class IndicatorPublisher:
    """
    Converts IndicatorResult objects into IndicatorEvents and publishes
    them on the Event Bus. Also stores results in the IndicatorCache.
    Never generates signals or trading decisions.
    """

    def __init__(self, cache: IndicatorCache) -> None:
        self._cache = cache

    async def publish(self, result: IndicatorResult) -> None:
        """Caches the result and publishes an IndicatorEvent on the bus."""
        # 1. Store in cache
        await self._cache.store(result)

        # 2. Build event
        evt = IndicatorEvent(
            indicator_name = result.indicator_name,
            symbol         = result.symbol,
            timeframe      = result.timeframe,
            timestamp      = result.timestamp,
            value          = result.value,
            metadata       = {
                **result.metadata,
                "calc_time_ms": result.calc_time_ms,
            }
        )

        # 3. Publish
        await event_bus.publish(EventModel(
            event_type   = "indicator_update",
            source_agent = "indicator_engine",
            payload      = evt.model_dump(),
        ))
