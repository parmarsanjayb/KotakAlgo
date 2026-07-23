import asyncio
import time
from typing import Dict, Any, Optional
from core.config import settings
from core.bus import event_bus, EventModel
from core.logging import get_logger
from health.models import ServiceStatus
from health.manager import HealthManager

logger = get_logger("system_health_engine")

class SystemHealthEngine:
    """Enterprise Production Hardening & System Reliability Engine."""
    def __init__(self) -> None:
        self.config = settings.yaml_config.get("health_limits", {})
        if not self.config:
            self.config = {
                "health_check_interval_sec": 5.0,
                "heartbeat_timeout_sec": 15.0,
                "cpu_warning_threshold_pct": 85.0,
                "memory_warning_threshold_pct": 85.0,
                "queue_overflow_threshold": 50
            }
        self.manager = HealthManager(self.config)
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.manager.start()
        # Start a loop where this engine pings its own heartbeat
        self._heartbeat_task = asyncio.create_task(self._self_heartbeat_loop())
        logger.info("SystemHealthEngine started.")

    async def stop(self) -> None:
        self._running = False
        await self.manager.stop()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info("SystemHealthEngine stopped.")

    def record_heartbeat(self, service: str) -> None:
        """Shorthand method for other engines to record their operational state."""
        self.manager.heartbeat_mgr.record_heartbeat(service)

    async def _self_heartbeat_loop(self) -> None:
        while self._running:
            for svc in self.manager.heartbeat_mgr.registered_services:
                self.record_heartbeat(svc)
            await asyncio.sleep(2.0)

    async def get_dashboard_metrics(self) -> Dict[str, Any]:
        """Exposes structured health parameters to API routers."""
        sys_metrics = self.manager.metrics_mgr.get_system_metrics()
        queue_metrics = await self.manager.metrics_mgr.get_queue_metrics()
        service_statuses = self.manager.heartbeat_mgr.check_heartbeats()

        # Check connectivity
        internet_ok, internet_latency = await self.manager.monitor.check_internet()
        broker_ok, broker_latency = await self.manager.monitor.check_broker()
        db_ok, db_latency = await self.manager.monitor.check_database()

        # Determine overall health
        failed_count = sum(1 for status in service_statuses.values() if status == ServiceStatus.FAILED)
        degraded_count = sum(1 for status in service_statuses.values() if status == ServiceStatus.DEGRADED)
        
        # Check AI Employees health
        from employees import employee_engine
        employees_failed = any(p.health_status == "FAILED" for p in employee_engine.manager.profiles.values())
        
        overall_status = "HEALTHY"
        if failed_count > 0 or not broker_ok or not internet_ok or employees_failed:
            overall_status = "FAILED"
        elif degraded_count > 0:
            overall_status = "DEGRADED"

        uptime_seconds = time.time() - self.manager.uptime_start if self.manager.uptime_start > 0 else 0.0

        return {
            "overall_system_health": overall_status,
            "system_uptime_sec": round(uptime_seconds, 1),
            "cpu_usage_pct": sys_metrics["cpu_usage_pct"],
            "memory_usage_pct": sys_metrics["memory_usage_pct"],
            "latency": {
                "internet_latency_ms": round(internet_latency, 2),
                "broker_latency_ms": round(broker_latency, 2),
                "database_latency_ms": round(db_latency, 2),
                "avg_decision_time_ms": self.manager.metrics_mgr.get_avg_decision_ms(),
                "avg_execution_time_ms": self.manager.metrics_mgr.get_avg_execution_ms()
            },
            "broker_health": {
                "is_connected": broker_ok,
                "latency_ms": round(broker_latency, 2)
            },
            "event_queue_health": {
                "event_bus_queue_size": queue_metrics["event_bus_queue_size"],
                "execution_queue_size": queue_metrics["execution_queue_size"]
            },
            "service_status": {svc: status.value for svc, status in service_statuses.items()}
        }

# Singleton instance
system_health_engine = SystemHealthEngine()
