import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from market.models import Timeframe
from indicators.models import IndicatorResult

class IndicatorCache:
    """
    Thread-safe in-memory store for the latest and previous indicator values.
    Tracks crossovers automatically whenever new values are stored.
    """

    def __init__(self) -> None:
        # symbol → timeframe → indicator_name → list[-2] (prev, latest)
        self._values: Dict[str, Dict[str, Dict[str, List[IndicatorResult]]]] = {}
        self._lock = asyncio.Lock()

    async def store(self, result: IndicatorResult) -> None:
        """Stores a new indicator result, retaining the previous value."""
        async with self._lock:
            sym = result.symbol
            tf  = result.timeframe.value
            ind = result.indicator_name

            self._values.setdefault(sym, {}).setdefault(tf, {}).setdefault(ind, [])
            history = self._values[sym][tf][ind]
            history.append(result)
            # Keep only latest 2 for crossover detection
            if len(history) > 2:
                self._values[sym][tf][ind] = history[-2:]

    async def get_latest(
        self, symbol: str, timeframe: Timeframe, indicator: str
    ) -> Optional[IndicatorResult]:
        async with self._lock:
            tf = timeframe.value
            history = self._values.get(symbol, {}).get(tf, {}).get(indicator, [])
            return history[-1] if history else None

    async def get_previous(
        self, symbol: str, timeframe: Timeframe, indicator: str
    ) -> Optional[IndicatorResult]:
        async with self._lock:
            tf = timeframe.value
            history = self._values.get(symbol, {}).get(tf, {}).get(indicator, [])
            return history[-2] if len(history) >= 2 else None

    async def detect_crossover(
        self, symbol: str, timeframe: Timeframe,
        fast_indicator: str, slow_indicator: str
    ) -> Optional[str]:
        """
        Returns 'GOLDEN' (fast crossed above slow) or 'DEATH' (fast crossed below slow)
        or None if no crossover occurred between the last two values.
        """
        async with self._lock:
            tf = timeframe.value
            store = self._values.get(symbol, {}).get(tf, {})
            fast_h = store.get(fast_indicator, [])
            slow_h = store.get(slow_indicator, [])
            if len(fast_h) < 2 or len(slow_h) < 2:
                return None
            f_prev, f_curr = fast_h[-2].value, fast_h[-1].value
            s_prev, s_curr = slow_h[-2].value, slow_h[-1].value
            if isinstance(f_prev, (int, float)) and isinstance(s_prev, (int, float)):
                if f_prev <= s_prev and f_curr > s_curr:
                    return "GOLDEN"
                if f_prev >= s_prev and f_curr < s_curr:
                    return "DEATH"
            return None

    async def get_all_latest(
        self, symbol: str, timeframe: Timeframe
    ) -> Dict[str, IndicatorResult]:
        async with self._lock:
            tf = timeframe.value
            store = self._values.get(symbol, {}).get(tf, {})
            return {ind: h[-1] for ind, h in store.items() if h}
