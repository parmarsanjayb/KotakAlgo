import asyncio
import time
import socket
from typing import Dict, Any, Tuple
from core.logging import get_logger
from brokers import broker_engine

logger = get_logger("service_monitor")

class ServiceMonitor:
    """Monitors connectivity dependencies (Internet, Broker API, Database)."""
    def __init__(self) -> None:
        pass

    async def check_internet(self) -> Tuple[bool, float]:
        """Tests internet reachability by attempting socket connections."""
        t0 = time.perf_counter()
        try:
            # Ping a public DNS server
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: socket.create_connection(("8.8.8.8", 53), timeout=2.0).close()
            )
            latency = (time.perf_counter() - t0) * 1000.0
            return True, latency
        except Exception:
            latency = (time.perf_counter() - t0) * 1000.0
            return False, latency

    async def check_broker(self) -> Tuple[bool, float]:
        """Tests the active broker adapter status and latency."""
        t0 = time.perf_counter()
        try:
            resp = await broker_engine.health_check()
            latency = (time.perf_counter() - t0) * 1000.0
            return resp.success, latency
        except Exception:
            latency = (time.perf_counter() - t0) * 1000.0
            return False, latency

    async def check_database(self) -> Tuple[bool, float]:
        """Checks the connection status to the database."""
        t0 = time.perf_counter()
        try:
            from database.connection import async_session
            from sqlalchemy import text
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            latency = (time.perf_counter() - t0) * 1000.0
            return True, latency
        except Exception:
            latency = (time.perf_counter() - t0) * 1000.0
            return False, latency
