from datetime import datetime, timezone
from typing import List
from sqlalchemy import select
from database.connection import async_session
from database.models import MarketDataModel

class HistoricalDataManager:
    """
    Reads and writes historical OHLCV candles to the database.
    Supports intraday (1m-4H) and daily, weekly, monthly frequencies.
    All storage is async — never blocks the feed thread.
    """

    SUPPORTED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1H", "4H", "1D", "1W", "1M"}

    async def save(
        self,
        symbol:    str,
        interval:  str,
        timestamp: datetime,
        o: float, h: float, l: float, c: float, v: float,
    ) -> None:
        if interval not in self.SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")
        async with async_session() as session:
            async with session.begin():
                record = MarketDataModel(
                    symbol=symbol, interval=interval, timestamp=timestamp,
                    open=o, high=h, low=l, close=c, volume=v,
                )
                session.add(record)
            await session.commit()

    async def load(
        self,
        symbol:   str,
        interval: str,
        start:    datetime,
        end:      datetime,
    ) -> List[MarketDataModel]:
        """Returns candles sorted ascending by timestamp."""
        async with async_session() as session:
            q = (
                select(MarketDataModel)
                .where(
                    MarketDataModel.symbol   == symbol,
                    MarketDataModel.interval == interval,
                    MarketDataModel.timestamp >= start,
                    MarketDataModel.timestamp <= end,
                )
                .order_by(MarketDataModel.timestamp)
            )
            result = await session.execute(q)
            return list(result.scalars().all())

    async def get_latest(self, symbol: str, interval: str) -> MarketDataModel | None:
        """Returns the most recent stored candle for a symbol/interval pair."""
        async with async_session() as session:
            q = (
                select(MarketDataModel)
                .where(
                    MarketDataModel.symbol   == symbol,
                    MarketDataModel.interval == interval,
                )
                .order_by(MarketDataModel.timestamp.desc())
                .limit(1)
            )
            result = await session.execute(q)
            return result.scalar_one_or_none()
