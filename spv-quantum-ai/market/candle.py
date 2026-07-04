import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from market.models import Candle, MarketData, Timeframe, TIMEFRAME_SECONDS
from market.cache import DataCacheManager

class CandleManager:
    """
    Builds OHLCV candles for every supported timeframe from incoming ticks.
    Fires on_candle_close callback when a bar is complete and updates the cache.
    """

    def __init__(
        self,
        cache: DataCacheManager,
        on_candle_close: Callable[[Candle], Any],
    ) -> None:
        self._cache = cache
        self._on_candle_close = on_candle_close
        # symbol → timeframe → in-progress bar dict
        self._open_bars: Dict[str, Dict[Timeframe, Dict[str, Any]]] = {}

    async def process_tick(self, tick: MarketData) -> None:
        """Ingests one tick and updates all timeframe candle builders."""
        sym = tick.symbol
        ts  = int(tick.timestamp.timestamp())

        if sym not in self._open_bars:
            self._open_bars[sym] = {}

        for tf, dur in TIMEFRAME_SECONDS.items():
            bar_start = ts - (ts % dur)

            bars = self._open_bars[sym]

            if tf not in bars:
                bars[tf] = self._new_bar(sym, tf, bar_start, tick)
                continue

            curr = bars[tf]

            if bar_start > curr["start"]:
                # Close current bar, publish, cache
                closed = self._to_candle(curr)
                closed.complete = True
                await self._cache.update_candle(closed)
                asyncio.create_task(self._on_candle_close(closed))
                # Start new bar
                bars[tf] = self._new_bar(sym, tf, bar_start, tick)
            else:
                # Update running bar
                curr["high"]   = max(curr["high"],  tick.ltp)
                curr["low"]    = min(curr["low"],   tick.ltp)
                curr["close"]  = tick.ltp
                curr["volume"] += tick.volume
                if curr["volume"] > 0:
                    curr["vwap"] = (curr["vwap_num"] + tick.ltp * tick.volume) / curr["volume"]
                curr["vwap_num"] += tick.ltp * tick.volume

                # Update live (incomplete) candle in cache
                live = self._to_candle(curr)
                live.complete = False
                await self._cache.update_candle(live)

    @staticmethod
    def _new_bar(symbol: str, tf: Timeframe, start: int, tick: MarketData) -> Dict[str, Any]:
        return {
            "symbol":   symbol,
            "tf":       tf,
            "start":    start,
            "open":     tick.ltp,
            "high":     tick.ltp,
            "low":      tick.ltp,
            "close":    tick.ltp,
            "volume":   tick.volume,
            "vwap":     tick.ltp,
            "vwap_num": tick.ltp * tick.volume,
        }

    @staticmethod
    def _to_candle(bar: Dict[str, Any]) -> Candle:
        return Candle(
            symbol    = bar["symbol"],
            timeframe = bar["tf"],
            timestamp = datetime.fromtimestamp(bar["start"], timezone.utc),
            open      = round(bar["open"],   4),
            high      = round(bar["high"],   4),
            low       = round(bar["low"],    4),
            close     = round(bar["close"],  4),
            volume    = round(bar["volume"], 4),
            vwap      = round(bar["vwap"],   4),
        )
