import asyncio
import importlib
import time
from typing import Dict, List, Optional

from brokers.base import BaseBroker
from brokers.registry import BROKER_REGISTRY
from core.config import settings
from core.logging import get_logger

logger = get_logger("broker_manager")


class BrokerManager:
    """
    Central controller for broker lifecycle, health, switching, and reconnection.
    Only one broker is active at a time; others are kept in the pool for hot-swap.
    """

    def __init__(self) -> None:
        from brokers.resolver import BrokerResolver
        self._active_broker_name: str = BrokerResolver.resolve_active_name()
        self._pool:   Dict[str, BaseBroker] = {}
        self._health: Dict[str, dict]       = {}
        self._monitor_task: Optional[asyncio.Task] = None

    # ── Broker loading ───────────────────────────────────────────────────────

    def _load_broker(self, name: str) -> BaseBroker:
        """Dynamically imports and instantiates a broker class using BrokerFactory."""
        from brokers.factory import BrokerFactory
        instance = BrokerFactory.create_broker(name)
        logger.info("Broker loaded", broker=name)
        return instance

    async def load(self, broker_name: Optional[str] = None) -> BaseBroker:
        """Loads broker into pool and connects it."""
        name = broker_name or self._active_broker_name
        if name not in self._pool:
            broker = self._load_broker(name)
            self._pool[name] = broker
        broker = self._pool[name]
        if not broker.is_connected():
            await broker.connect()
        return broker

    # ── Active broker access ─────────────────────────────────────────────────

    def get_active(self) -> BaseBroker:
        """Returns the currently active broker instance."""
        if self._active_broker_name not in self._pool:
            raise RuntimeError("Active broker not loaded. Call load() first.")
        return self._pool[self._active_broker_name]

    # ── Broker switching ─────────────────────────────────────────────────────

    async def switch_broker(self, new_broker_name: str) -> None:
        """
        Hot-switches the active broker.
        Disconnects the current broker and connects the new one.
        """
        logger.info("Switching broker", from_=self._active_broker_name, to=new_broker_name)

        # Disconnect old
        old = self._pool.get(self._active_broker_name)
        if old and old.is_connected():
            await old.disconnect()

        # Load and connect new
        await self.load(new_broker_name)
        self._active_broker_name = new_broker_name
        logger.info("Broker switched successfully", active=new_broker_name)

    # ── Reconnect ────────────────────────────────────────────────────────────

    async def reconnect(self, broker_name: Optional[str] = None) -> bool:
        """Disconnects and reconnects a specific broker. Returns True on success."""
        name = broker_name or self._active_broker_name
        broker = self._pool.get(name)
        if not broker:
            logger.warning("Reconnect called for unknown broker", broker=name)
            return False
        try:
            if broker.is_connected():
                await broker.disconnect()
            resp = await broker.connect()
            if resp.success:
                logger.info("Broker reconnected", broker=name)
                return True
        except Exception as e:
            logger.error("Broker reconnect failed", broker=name, error=str(e))
        return False

    # ── Health monitoring ────────────────────────────────────────────────────

    async def check_health(self) -> Dict[str, dict]:
        """Pings all loaded brokers and records latency + status."""
        results: Dict[str, dict] = {}
        for name, broker in self._pool.items():
            try:
                resp = await broker.health_check()
                results[name] = {
                    "connected":  resp.success,
                    "latency_ms": resp.latency_ms,
                    "error":      resp.error,
                }
            except Exception as e:
                results[name] = {"connected": False, "latency_ms": -1, "error": str(e)}
        self._health = results
        return results

    def get_health(self) -> Dict[str, dict]:
        """Returns the last cached health result."""
        return self._health

    async def start_health_monitor(self, interval_sec: int = 30) -> None:
        """Starts background health-check loop."""
        self._monitor_task = asyncio.create_task(
            self._health_loop(interval_sec)
        )
        logger.info("Broker health monitor started", interval_sec=interval_sec)

    async def stop_health_monitor(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        logger.info("Broker health monitor stopped.")

    async def _health_loop(self, interval_sec: int) -> None:
        while True:
            try:
                health = await self.check_health()
                for name, status in health.items():
                    if not status["connected"]:
                        logger.warning("Broker unhealthy, triggering reconnect", broker=name)
                        await self.reconnect(name)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in broker health loop", error=str(e))
            await asyncio.sleep(interval_sec)

    # ── Shutdown all ─────────────────────────────────────────────────────────

    async def shutdown_all(self) -> None:
        """Disconnects all brokers in the pool and stops the monitor."""
        await self.stop_health_monitor()
        for name, broker in self._pool.items():
            if broker.is_connected():
                await broker.disconnect()
                logger.info("Broker disconnected on shutdown", broker=name)
        self._pool.clear()


# Singleton
broker_manager = BrokerManager()
