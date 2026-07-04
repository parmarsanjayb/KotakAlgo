from indicators.cache import IndicatorCache
from indicators.publisher import IndicatorPublisher
from indicators.manager import IndicatorManager
from core.bus import event_bus, EventModel
from market.models import Candle
from core.logging import get_logger

logger = get_logger("indicator_engine")

class IndicatorEngine:
    """
    Top-level orchestrator for the Indicator Intelligence module.
    Subscribes to CandleEvents from the Market Data Engine and fans out
    calculations to the IndicatorManager.
    Never produces BUY/SELL. Never talks to brokers or the DB.
    """

    def __init__(self) -> None:
        self.cache     = IndicatorCache()
        self.publisher = IndicatorPublisher(self.cache)
        self.manager   = IndicatorManager(self.publisher)
        self._running  = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("candle", self._on_candle_event)
        logger.info("IndicatorEngine started. Subscribed to candle events.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("candle", self._on_candle_event)
        logger.info("IndicatorEngine stopped.")

    async def _on_candle_event(self, event: EventModel) -> None:
        """Receives CandleEvent payloads and dispatches to IndicatorManager."""
        try:
            payload = event.payload
            # CandleEvent wraps candle under payload["candle"]
            raw_candle = payload.get("candle", payload)
            candle = Candle(**raw_candle)
            if candle.complete:
                await self.manager.on_candle(candle)
        except Exception as e:
            logger.error("Error processing candle event in IndicatorEngine", error=str(e))


# Module-level singleton
indicator_engine = IndicatorEngine()
