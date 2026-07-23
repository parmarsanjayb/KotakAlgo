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
    Maintains a pool of active brokers keyed by user_id.
    """

    def __init__(self) -> None:
        from brokers.resolver import BrokerResolver
        self._active_broker_name: str = BrokerResolver.resolve_active_name()
        self._pool:   Dict[str, BaseBroker] = {}  # user_id -> BaseBroker
        self._health: Dict[str, dict]       = {}
        self._monitor_task: Optional[asyncio.Task] = None

    # ── Broker loading ───────────────────────────────────────────────────────

    def _load_broker(self, name: str, config_data: Optional[dict] = None) -> BaseBroker:
        """Dynamically imports and instantiates a broker class using BrokerFactory."""
        from brokers.factory import BrokerFactory
        instance = BrokerFactory.create_broker(name, config_data=config_data)
        logger.info("Broker loaded", broker=name)
        return instance

    async def load(self, user_id: str = "admin", broker_name: Optional[str] = None) -> BaseBroker:
        """Loads broker for a specific user into pool and connects it."""
        # Detect if user_id is actually a broker name (for legacy compatibility)
        known_brokers = {"paper_broker", "kotak_neo", "simulated_broker"}
        if user_id in known_brokers:
            broker_name = user_id
            user_id = "admin"

        if user_id in self._pool and self._pool[user_id].is_connected():
            return self._pool[user_id]

        name = broker_name or self._active_broker_name
        
        # Load user broker credentials from DB
        from database.connection import async_session
        from database.models import UserBrokerConfigModel
        from sqlalchemy import select
        
        config_data = {}
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(UserBrokerConfigModel).where(
                        UserBrokerConfigModel.user_id == user_id,
                        UserBrokerConfigModel.is_active == True
                    )
                )
                cfg = result.scalars().first()
                if cfg:
                    name = cfg.broker_name
                    config_data = {
                        "api_key": cfg.api_key,
                        "api_secret": cfg.api_secret,
                        "mpin": cfg.mpin,
                        "totp_secret": cfg.totp_secret_encrypted,
                        "ucc": cfg.ucc
                    }
        except Exception as e:
            logger.error(f"Failed to load broker config for user {user_id}: {e}")

        # Fallback to settings.yaml config if not found in database (e.g. admin tenant)
        if not config_data and name == "kotak_neo":
            kotak_cfg = settings.yaml_config.get("brokers", {}).get("kotak_neo", {})
            config_data = {
                "api_key": kotak_cfg.get("api_key"),
                "mobile_number": kotak_cfg.get("mobile_number"),
                "ucc": kotak_cfg.get("client_id"),
                "mpin": kotak_cfg.get("mpin"),
                "totp_secret": kotak_cfg.get("totp_secret"),
            }

        broker = self._load_broker(name, config_data=config_data)
        broker.user_id = user_id
        
        if not broker.is_connected():
            await broker.connect()
            
        self._pool[user_id] = broker
        logger.info("Broker loaded for user successfully", user_id=user_id, active=name)
        return broker

    # ── Active broker access ─────────────────────────────────────────────────

    def get_active(self, user_id: str = "admin") -> BaseBroker:
        """Returns the currently active broker instance for a specific user."""
        # Legacy compatibility check: if user_id is a broker name, return the instance for "admin"
        known_brokers = {"paper_broker", "kotak_neo", "simulated_broker"}
        if user_id in known_brokers:
            user_id = "admin"

        if user_id not in self._pool:
            # Auto-fallback: instantiate default broker synchronously
            from brokers.factory import BrokerFactory
            config_data = {}
            if self._active_broker_name == "kotak_neo":
                kotak_cfg = settings.yaml_config.get("brokers", {}).get("kotak_neo", {})
                config_data = {
                    "api_key": kotak_cfg.get("api_key"),
                    "mobile_number": kotak_cfg.get("mobile_number"),
                    "ucc": kotak_cfg.get("client_id"),
                    "mpin": kotak_cfg.get("mpin"),
                    "totp_secret": kotak_cfg.get("totp_secret"),
                }
            broker = BrokerFactory.create_broker(self._active_broker_name, config_data=config_data)
            broker.user_id = user_id
            broker._connected = True
            self._pool[user_id] = broker
            logger.info("Broker auto-loaded for user to preserve backward compatibility", user_id=user_id, active=self._active_broker_name)
            
        return self._pool[user_id]

    # ── Broker switching ─────────────────────────────────────────────────────

    async def switch_broker(self, user_id: str, new_broker_name: Optional[str] = None) -> None:
        """
        Hot-switches the active broker for a specific user.
        Supports both switch_broker(user_id, name) and legacy switch_broker(name).
        """
        known_brokers = {"paper_broker", "kotak_neo", "simulated_broker"}
        if user_id in known_brokers or new_broker_name is None:
            new_broker_name = user_id
            user_id = "admin"

        logger.info("Switching broker for user", user=user_id, to=new_broker_name)

        # Disconnect old
        old = self._pool.get(user_id)
        if old and old.is_connected():
            await old.disconnect()

        # Load and connect new
        await self.load(user_id, new_broker_name)
        logger.info("Broker switched successfully for user", user=user_id, active=new_broker_name)

    # ── Reconnect ────────────────────────────────────────────────────────────

    async def reconnect(self, user_id: str = "admin") -> bool:
        """Disconnects and reconnects a specific user's broker. Returns True on success."""
        known_brokers = {"paper_broker", "kotak_neo", "simulated_broker"}
        if user_id in known_brokers:
            user_id = "admin"

        broker = self._pool.get(user_id)
        if not broker:
            logger.warning("Reconnect called for unknown user broker", user=user_id)
            return False
        try:
            if broker.is_connected():
                await broker.disconnect()
            resp = await broker.connect()
            if resp.success:
                logger.info("Broker reconnected for user", user=user_id)
                return True
        except Exception as e:
            logger.error("Broker reconnect failed for user", user=user_id, error=str(e))
        return False

    # ── Health monitoring ────────────────────────────────────────────────────

    async def check_health(self) -> Dict[str, dict]:
        """Pings all loaded brokers and records latency + status."""
        results: Dict[str, dict] = {}
        for name, broker in self._pool.items():
            try:
                resp = await broker.health_check()
                status = {
                    "connected":  resp.success,
                    "latency_ms": resp.latency_ms,
                    "error":      resp.error,
                }
            except Exception as e:
                status = {"connected": False, "latency_ms": -1, "error": str(e)}
            
            results[name] = status
            results[broker.name] = status
            
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
