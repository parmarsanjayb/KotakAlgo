import asyncio
from typing import Dict, Optional
from market.models import Timeframe
from regime.models import RegimeResult, MarketRegime


class RegimeCache:
    """
    Thread-safe store for the latest regime per symbol/timeframe.
    Retains previous regime for transition detection.
    """

    def __init__(self) -> None:
        # (symbol, timeframe) → [prev, latest]
        self._store: Dict[tuple, list] = {}
        self._lock = asyncio.Lock()

    async def store(self, result: RegimeResult) -> None:
        async with self._lock:
            key = (result.symbol, result.timeframe.value)
            history = self._store.setdefault(key, [])
            history.append(result)
            if len(history) > 2:
                self._store[key] = history[-2:]

    async def get_latest(
        self, symbol: str, timeframe: Timeframe
    ) -> Optional[RegimeResult]:
        async with self._lock:
            h = self._store.get((symbol, timeframe.value), [])
            return h[-1] if h else None

    async def get_previous(
        self, symbol: str, timeframe: Timeframe
    ) -> Optional[RegimeResult]:
        async with self._lock:
            h = self._store.get((symbol, timeframe.value), [])
            return h[-2] if len(h) >= 2 else None

    async def has_regime_changed(
        self, symbol: str, timeframe: Timeframe
    ) -> bool:
        async with self._lock:
            h = self._store.get((symbol, timeframe.value), [])
            if len(h) < 2:
                return False
            return h[-1].market_regime != h[-2].market_regime

    async def get_all_latest(self) -> Dict[tuple, RegimeResult]:
        async with self._lock:
            return {k: v[-1] for k, v in self._store.items() if v}
