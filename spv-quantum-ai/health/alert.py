from typing import Dict, Any
from core.bus import event_bus, EventModel
from core.logging import get_logger
from health.models import SystemWarningEvent

logger = get_logger("alert_manager")

class AlertManager:
    """Dispatches system warnings, critical alerts, and metrics threshold breaches."""
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.cpu_threshold = float(config.get("cpu_warning_threshold_pct", 85.0))
        self.mem_threshold = float(config.get("memory_warning_threshold_pct", 85.0))
        self.queue_threshold = int(config.get("queue_overflow_threshold", 50))

    async def check_thresholds(self, metrics: Dict[str, Any], queue_metrics: Dict[str, Any]) -> None:
        cpu = metrics.get("cpu_usage_pct", 0.0)
        if cpu >= self.cpu_threshold:
            await self.publish_warning("cpu_usage_pct", cpu, self.cpu_threshold, f"High CPU utilization: {cpu}%")

        mem = metrics.get("memory_usage_pct", 0.0)
        if mem >= self.mem_threshold:
            await self.publish_warning("memory_usage_pct", mem, self.mem_threshold, f"High Memory utilization: {mem}%")

        event_queue = queue_metrics.get("event_bus_queue_size", 0)
        if event_queue >= self.queue_threshold:
            await self.publish_warning("event_bus_queue_size", float(event_queue), float(self.queue_threshold), f"Queue backlog overflow: {event_queue} items")

    async def publish_warning(self, metric: str, val: float, threshold: float, msg: str) -> None:
        logger.warning(f"ALERT WARNING: {msg}")
        evt = SystemWarningEvent(metric=metric, value=val, threshold=threshold, message=msg)
        await event_bus.publish(EventModel(
            event_type="system_warning",
            source_agent="health_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def trigger_critical_alert(self, category: str, message: str) -> None:
        logger.error(f"CRITICAL SYSTEM ALERT | {category.upper()} | {message}")
        await event_bus.publish(EventModel(
            event_type=f"critical_{category}_alert",
            source_agent="health_engine",
            payload={"message": message, "category": category}
        ))
