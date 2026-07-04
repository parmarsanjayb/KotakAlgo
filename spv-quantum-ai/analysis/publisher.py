from core.bus import event_bus, EventModel
from core.logging import get_logger
from analysis.models import MarketAnalysisReport, MarketAnalysisEvent
from analysis.cache import AnalysisCache

logger = get_logger("analysis_publisher")

class AnalysisPublisher:
    """
    Saves MarketAnalysisReport to AnalysisCache and publishes
    MarketAnalysisEvent onto the Event Bus.
    """
    def __init__(self, cache: AnalysisCache) -> None:
        self._cache = cache

    async def publish(self, report: MarketAnalysisReport) -> None:
        # 1. Store in cache
        await self._cache.store(report)

        # 2. Build Event
        evt = MarketAnalysisEvent(report=report)

        # 3. Publish
        await event_bus.publish(EventModel(
            event_type="market_analysis",
            source_agent="market_analyst_agent",
            payload=evt.model_dump()
        ))
        
        logger.info(
            f"Market Analysis Published: {report.symbol} ({report.timeframe}) | Bias: {report.market_bias} | Rec Strat: {report.recommended_strategy}"
        )
