from datetime import datetime, timezone
from typing import Any, Dict
from market.models import MarketData
from market.cache import DataCacheManager

class TickDataManager:
    """
    Validates and normalises raw broker tick payloads into canonical MarketData objects.
    Feeds the DataCacheManager. Never calls brokers directly.
    """

    def __init__(self, cache: DataCacheManager) -> None:
        self._cache = cache

    async def process(self, raw: Dict[str, Any]) -> MarketData:
        """
        Accepts a raw dictionary from the WebSocket feed adapter,
        builds a canonical MarketData, updates the cache, and returns it.
        """
        symbol = raw.get("symbol", "UNKNOWN")
        price  = float(raw.get("price", raw.get("ltp", 0.0)))
        volume = float(raw.get("volume", 0.0))

        # Running VWAP approximation: (price * volume) / cumulative volume
        prev_vwap   = await self._cache.get_vwap(symbol)
        prev_volume = await self._cache.get_volume(symbol)
        total_volume = prev_volume + volume
        vwap = (
            (prev_vwap * prev_volume + price * volume) / total_volume
            if total_volume > 0 else price
        )

        tick = MarketData(
            symbol       = symbol,
            timestamp    = datetime.now(timezone.utc),
            ltp          = round(price, 4),
            bid          = round(float(raw.get("bid", price * 0.9998)), 4),
            ask          = round(float(raw.get("ask", price * 1.0002)), 4),
            volume       = round(volume, 4),
            open_interest= round(float(raw.get("open_interest", 0.0)), 2),
            vwap         = round(vwap, 4),
            atp          = round(float(raw.get("atp", price)), 4),
            open         = round(float(raw.get("open",  price)), 4),
            high         = round(float(raw.get("high",  price)), 4),
            low          = round(float(raw.get("low",   price)), 4),
            close        = round(float(raw.get("close", price)), 4),
            prev_close   = round(float(raw.get("prev_close", 0.0)), 4),
        )

        await self._cache.update_tick(tick)
        return tick
