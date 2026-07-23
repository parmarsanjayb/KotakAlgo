import asyncio
from typing import List, Optional, Dict
from core.bus import event_bus, EventModel
from core.logging import get_logger
from database.connection import async_session
from database.models import MarketDataModel

logger = get_logger("market_persistence")


class MarketDataPersistence:
    """
    Writes every completed real-tick-derived candle to the market_data table.
    Uses asynchronous batching to prevent database lockups and pool exhaustion.
    """

    def __init__(self) -> None:
        self._running = False
        self._write_queue: List[Dict] = []
        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._batch_write_loop())
        await event_bus.subscribe("candle", self._on_candle)
        logger.info("MarketDataPersistence subscribed to candle events with batching enabled.")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await event_bus.unsubscribe("candle", self._on_candle)
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        # Flush any remaining items in queue
        await self._flush_queue()
        logger.info("MarketDataPersistence stopped.")

    async def _on_candle(self, event: EventModel) -> None:
        candle = event.payload.get("candle", event.payload)
        if not candle.get("complete", True):
            return
        async with self._lock:
            self._write_queue.append({
                "symbol": candle["symbol"],
                "timestamp": candle["timestamp"],
                "interval": candle["timeframe"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
            })

    async def _batch_write_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(5.0)
                await self._flush_queue()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in MarketDataPersistence batch write loop", error=str(e))

    async def _flush_queue(self) -> None:
        to_write = []
        async with self._lock:
            if self._write_queue:
                to_write = self._write_queue.copy()
                self._write_queue.clear()
        
        if not to_write:
            return
            
        try:
            async with async_session() as session:
                async with session.begin():
                    db_items = [
                        MarketDataModel(
                            symbol=c["symbol"],
                            timestamp=c["timestamp"],
                            interval=c["interval"],
                            open=c["open"],
                            high=c["high"],
                            low=c["low"],
                            close=c["close"],
                            volume=c["volume"]
                        )
                        for c in to_write
                    ]
                    session.add_all(db_items)
                await session.commit()
            logger.info(f"Successfully persisted {len(to_write)} candles in batch.")
        except Exception as e:
            logger.error(f"Failed to persist batch of {len(to_write)} candles: {e}")
            # Re-queue failed items at the beginning
            async with self._lock:
                self._write_queue = to_write + self._write_queue


# Module-level singleton
market_data_persistence = MarketDataPersistence()
