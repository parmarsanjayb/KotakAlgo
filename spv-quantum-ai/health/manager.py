import asyncio
from typing import Dict, Any, Optional
from health.models import ServiceStatus
from health.logging import LoggingManager
from health.metrics import MetricsManager
from health.alert import AlertManager
from health.heartbeat import HeartbeatManager
from health.monitor import ServiceMonitor
from health.recovery import RecoveryManager
from core.logging import get_logger

logger = get_logger("health_manager")

class HealthManager:
    """Central manager coordinating system monitors, logs, metrics, alerts, and recovery procedures."""
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.logging_mgr = LoggingManager()
        self.metrics_mgr = MetricsManager()
        self.alert_mgr = AlertManager(config)
        self.heartbeat_mgr = HeartbeatManager()
        self.monitor = ServiceMonitor()
        self.recovery = RecoveryManager()

        self.monitor_task: Optional[asyncio.Task] = None
        self._running = False
        self.uptime_start = 0.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        import time
        self.uptime_start = time.time()
        self.monitor_task = asyncio.create_task(self._run_monitoring_loop())
        logger.info("HealthManager monitoring sub-systems started.")

    async def stop(self) -> None:
        self._running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        logger.info("HealthManager monitoring sub-systems stopped.")

    async def _run_monitoring_loop(self) -> None:
        check_interval = float(self.config.get("health_check_interval_sec", 5.0))
        
        while self._running:
            try:
                await asyncio.sleep(check_interval)
                # 1. Compile system and queue metrics
                sys_metrics = self.metrics_mgr.get_system_metrics()
                queue_metrics = await self.metrics_mgr.get_queue_metrics()

                # 2. Check thresholds/alerts
                await self.alert_mgr.check_thresholds(sys_metrics, queue_metrics)

                # 3. Check heartbeats
                service_statuses = self.heartbeat_mgr.check_heartbeats(
                    timeout_sec=float(self.config.get("heartbeat_timeout_sec", 15.0))
                )

                # 4. Handle recovery for failed services
                for svc, status in service_statuses.items():
                    if status == ServiceStatus.FAILED:
                        logger.error(f"Service failure detected: {svc} status is FAILED.")
                        await self.alert_mgr.trigger_critical_alert("service_failure", f"{svc} heartbeat timeout")
                        # Trigger automated recovery
                        asyncio.create_task(self.recovery.attempt_recovery(svc, "Heartbeat timeout"))

                # 5. Log performance stats to structured files
                self.logging_mgr.log(
                    "performance",
                    "Periodic performance check",
                    cpu=sys_metrics["cpu_usage_pct"],
                    memory=sys_metrics["memory_usage_pct"],
                    event_queue=queue_metrics["event_bus_queue_size"],
                    execution_queue=queue_metrics["execution_queue_size"]
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in health monitoring loop", error=str(e))
