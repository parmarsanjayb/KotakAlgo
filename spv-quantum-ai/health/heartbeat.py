import time
from typing import Dict, Any, List
from health.models import ServiceStatus

class HeartbeatManager:
    """Tracks heartbeat signals from all active system engines."""
    def __init__(self) -> None:
        self.last_heartbeats: Dict[str, float] = {}
        self.registered_services: List[str] = [
            "Market Data Engine",
            "Scanner Engine",
            "Indicator Engine",
            "Strategy Engine",
            "Risk Engine",
            "Chief Decision Agent",
            "Execution Engine",
            "Broker Engine",
            "Paper Trading Engine",
            "Portfolio Engine",
            "Trade Journal",
            "Performance Analytics",
            "Safety Engine"
        ]
        # Initialise with current time
        now = time.time()
        for svc in self.registered_services:
            self.last_heartbeats[svc] = now

    def record_heartbeat(self, service: str) -> None:
        """Called by engines to signal they are operating."""
        self.last_heartbeats[service] = time.time()

    def check_heartbeats(self, timeout_sec: float = 10.0) -> Dict[str, ServiceStatus]:
        """Scans all registered services and marks unresponsive ones as degraded/failed."""
        now = time.time()
        statuses: Dict[str, ServiceStatus] = {}
        for svc in self.registered_services:
            last = self.last_heartbeats.get(svc, 0.0)
            elapsed = now - last
            if elapsed < timeout_sec:
                statuses[svc] = ServiceStatus.HEALTHY
            elif elapsed < timeout_sec * 2:
                statuses[svc] = ServiceStatus.DEGRADED
            else:
                statuses[svc] = ServiceStatus.FAILED
        return statuses
