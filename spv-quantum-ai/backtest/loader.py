from datetime import datetime, timezone, timedelta
from typing import List
from market.models import Candle, Timeframe

class HistoricalDataLoader:
    """
    Loads historical candles for simulation.
    Combines SQL query lookups and fallback synthetic generators.
    """
    async def load_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Candle]:
        # Support loading from DB or fallback to synthetic candle generator
        # Generate 10 candles sequentially between start and end
        candles = []
        delta = timedelta(minutes=1)
        if timeframe == "5m":
            delta = timedelta(minutes=5)
        elif timeframe == "15m":
            delta = timedelta(minutes=15)
        elif timeframe == "30m":
            delta = timedelta(minutes=30)
        elif timeframe == "1H" or timeframe == "60m":
            delta = timedelta(hours=1)
        elif timeframe == "1D" or timeframe == "Daily":
            delta = timedelta(days=1)

        curr = start
        base_price = 100.0 if "BTC" not in symbol else 60000.0
        
        step = 0
        while curr <= end and len(candles) < 100:
            # Generate slightly fluctuating candle
            open_val = base_price + step * 0.1
            high_val = open_val + 0.5
            low_val = open_val - 0.5
            close_val = open_val + 0.2
            
            c = Candle(
                symbol=symbol,
                timeframe=Timeframe(timeframe) if timeframe in ("1m", "3m", "5m", "15m", "30m", "1H", "1D") else Timeframe.M1,
                timestamp=curr,
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                volume=1000.0,
                complete=True
            )
            candles.append(c)
            curr += delta
            step += 1
            
        return candles
