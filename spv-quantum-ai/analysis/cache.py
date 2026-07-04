import asyncio
from typing import Dict, Optional
from analysis.models import MarketAnalysisReport

class AnalysisCache:
    """
    Thread-safe in-memory store for the latest market analysis reports.
    """
    def __init__(self) -> None:
        # (symbol, timeframe) -> MarketAnalysisReport
        self._cache: Dict[tuple, MarketAnalysisReport] = {}
        self._lock = asyncio.Lock()

    async def store(self, report: MarketAnalysisReport) -> None:
        async with self._lock:
            key = (report.symbol, report.timeframe)
            self._cache[key] = report

    async def get_latest(self, symbol: str, timeframe: str) -> Optional[MarketAnalysisReport]:
        async with self._lock:
            return self._cache.get((symbol, timeframe))

    async def get_all_latest(self) -> Dict[tuple, MarketAnalysisReport]:
        async with self._lock:
            return self._cache.copy()
