import asyncio
from typing import Optional
from scanner.engine import market_scanner_engine
from core.logging import get_logger

logger = get_logger("scanner_scheduler")

class ScannerScheduler:
    """
    Background scheduler to periodically trigger scanner runs.
    """
    def __init__(self, interval_sec: float = 5.0) -> None:
        self.interval_sec = interval_sec
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"ScannerScheduler started with interval {self.interval_sec}s.")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ScannerScheduler stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await market_scanner_engine.run_scan()
            except Exception as e:
                logger.error(f"Error during scheduled scanner run: {e}")
            await asyncio.sleep(self.interval_sec)

# Singleton
scanner_scheduler = ScannerScheduler()
