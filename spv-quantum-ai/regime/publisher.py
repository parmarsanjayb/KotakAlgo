from core.bus import event_bus, EventModel
from regime.models import RegimeResult, MarketRegimeEvent
from regime.cache import RegimeCache
from core.logging import get_logger

logger = get_logger("regime_publisher")


class RegimePublisher:
    """
    Stores regime results in the cache and publishes MarketRegimeEvents
    on the Event Bus. Never generates trading signals.
    """

    def __init__(self, cache: RegimeCache) -> None:
        self._cache = cache

    async def publish(self, result: RegimeResult) -> None:
        # 1. Cache
        await self._cache.store(result)

        # 2. Build typed event
        evt = MarketRegimeEvent(
            symbol             = result.symbol,
            timeframe          = result.timeframe,
            market_regime      = result.market_regime,
            confidence         = round(result.confidence, 2),
            reason             = result.reason,
            supporting_factors = result.supporting_factors,
            timestamp          = result.timestamp,
        )

        # 3. Publish
        await event_bus.publish(EventModel(
            event_type   = "market_regime",
            source_agent = "regime_engine",
            payload      = evt.model_dump(),
            priority     = 2,
        ))

        changed = await self._cache.has_regime_changed(result.symbol, result.timeframe)
        if changed:
            logger.info(
                "Regime changed",
                symbol=result.symbol,
                tf=result.timeframe.value,
                new=result.market_regime.value,
                confidence=result.confidence,
            )
