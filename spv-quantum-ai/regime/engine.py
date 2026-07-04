import asyncio
from typing import Any, Dict, Optional
from market.models import Timeframe, Candle
from regime.models import MarketRegime, RegimeResult
from regime.classifier import RegimeClassifier
from regime.cache import RegimeCache
from regime.publisher import RegimePublisher
from indicators.cache import IndicatorCache
from market.cache import DataCacheManager
from core.bus import event_bus, EventModel
from core.logging import get_logger

logger = get_logger("regime_engine")

# Rolling ATR / volume averages window
_AVG_WINDOW = 20


class MarketRegimeEngine:
    """
    Subscribes to indicator_update and candle events.
    Builds a feature vector per symbol/timeframe and calls RegimeClassifier.
    Publishes MarketRegimeEvent via RegimePublisher.
    NEVER generates BUY/SELL/ENTRY/EXIT signals.
    """

    def __init__(
        self,
        indicator_cache: IndicatorCache,
        market_cache:    DataCacheManager,
    ) -> None:
        self._ind_cache    = indicator_cache
        self._mkt_cache    = market_cache
        self._classifier   = RegimeClassifier()
        self._regime_cache = RegimeCache()
        self._publisher    = RegimePublisher(self._regime_cache)
        self._running      = False

        # Rolling history: (symbol, tf) → list of scalar values
        self._atr_history: Dict[tuple, list]    = {}
        self._vol_history: Dict[tuple, list]    = {}

    @property
    def cache(self) -> RegimeCache:
        return self._regime_cache

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("candle", self._on_candle)
        logger.info("MarketRegimeEngine started.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("candle", self._on_candle)
        logger.info("MarketRegimeEngine stopped.")

    # ── Event handler ─────────────────────────────────────────────────────────

    async def _on_candle(self, event: EventModel) -> None:
        try:
            raw    = event.payload.get("candle", event.payload)
            candle = Candle(**raw)
            if not candle.complete:
                return
            await self._process(candle.symbol, candle.timeframe)
        except Exception as e:
            logger.error("Regime engine candle error", error=str(e))

    # ── Feature extraction + classification ───────────────────────────────────

    async def _process(self, symbol: str, tf: Timeframe) -> None:
        features = await self._build_features(symbol, tf)
        result   = self._classifier.classify(symbol, tf, features)
        await self._publisher.publish(result)

    async def _build_features(self, symbol: str, tf: Timeframe) -> Dict[str, Any]:
        """Gathers all available indicator and market-data values into a feature dict."""
        f: Dict[str, Any] = {}

        # ── Tick / session data ───────────────────────────────────────────────
        tick = await self._mkt_cache.get_tick(symbol)
        if tick:
            f["ltp"]          = tick.ltp
            f["prev_close"]   = tick.prev_close
            f["vwap"]         = tick.vwap
            f["volume"]       = tick.volume
            f["session_high"] = await self._mkt_cache.get_session_high(symbol)
            f["session_low"]  = await self._mkt_cache.get_session_low(symbol)

        # ── ATR ───────────────────────────────────────────────────────────────
        atr_r = await self._ind_cache.get_latest(symbol, tf, "ATR")
        if atr_r and isinstance(atr_r.value, (int, float)):
            atr_val = float(atr_r.value)
            f["atr"] = atr_val
            key = (symbol, tf.value)
            self._atr_history.setdefault(key, []).append(atr_val)
            h = self._atr_history[key][-_AVG_WINDOW:]
            f["atr_avg"] = sum(h) / len(h)

        # ── ADX / DI ─────────────────────────────────────────────────────────
        adx_r = await self._ind_cache.get_latest(symbol, tf, "ADX")
        if adx_r and isinstance(adx_r.value, dict):
            f["adx"]    = adx_r.value.get("adx",    0.0)
            f["di_pos"] = adx_r.value.get("di_pos", 0.0)
            f["di_neg"] = adx_r.value.get("di_neg", 0.0)

        # ── EMAs ─────────────────────────────────────────────────────────────
        for name, key in [("EMA_9","ema_9"), ("EMA_20","ema_20"), ("EMA_50","ema_50")]:
            r = await self._ind_cache.get_latest(symbol, tf, name)
            if r and isinstance(r.value, (int, float)):
                f[key] = float(r.value)

        # ── Bollinger Bands ───────────────────────────────────────────────────
        bb_r = await self._ind_cache.get_latest(symbol, tf, "BOLLINGER")
        if bb_r and isinstance(bb_r.value, dict):
            f["bb_upper"] = bb_r.value.get("upper", 0.0)
            f["bb_lower"] = bb_r.value.get("lower", 0.0)
            f["bb_bw"]    = bb_r.value.get("bandwidth", 0.0)

        # ── Momentum ──────────────────────────────────────────────────────────
        mom_r = await self._ind_cache.get_latest(symbol, tf, "MOMENTUM")
        if mom_r and isinstance(mom_r.value, (int, float)):
            f["momentum"] = float(mom_r.value)

        # ── Volume average ────────────────────────────────────────────────────
        vol = f.get("volume", 0.0)
        if vol:
            key = (symbol, tf.value, "vol")
            self._vol_history.setdefault(key, []).append(vol)
            h = self._vol_history[key][-_AVG_WINDOW:]
            f["vol_avg"] = sum(h) / len(h)

        return f

    # ── Manual classify (for testing / dashboard) ─────────────────────────────

    async def classify_now(self, symbol: str, tf: Timeframe) -> Optional[RegimeResult]:
        """Force-classify the current state and return the result."""
        features = await self._build_features(symbol, tf)
        result   = self._classifier.classify(symbol, tf, features)
        await self._publisher.publish(result)
        return result

# Singleton instance
from indicators.engine import indicator_engine as _ie
from market.manager import market_data_manager as _mdm
regime_engine = MarketRegimeEngine(_ie.cache, _mdm.cache)

