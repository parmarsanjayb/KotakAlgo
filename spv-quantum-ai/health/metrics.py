import time
from typing import Dict, Any, List

try:
    import psutil
except ImportError:
    psutil = None

class MetricsManager:
    """Aggregates system-level, queue, latency, and trading statistics."""
    def __init__(self) -> None:
        self.decision_times: List[float] = []
        self.execution_times: List[float] = []
        self.order_count_minute = 0
        self.last_minute_reset = time.time()
        self.trades_today = 0

    def record_decision(self, seconds: float) -> None:
        self.decision_times.append(seconds)
        if len(self.decision_times) > 100:
            self.decision_times.pop(0)

    def record_execution(self, seconds: float) -> None:
        self.execution_times.append(seconds)
        if len(self.execution_times) > 100:
            self.execution_times.pop(0)

    def increment_orders(self) -> None:
        now = time.time()
        if now - self.last_minute_reset >= 60.0:
            self.order_count_minute = 0
            self.last_minute_reset = now
        self.order_count_minute += 1

    def increment_trades(self) -> None:
        self.trades_today += 1

    def get_avg_decision_ms(self) -> float:
        if not self.decision_times:
            return 0.0
        return round((sum(self.decision_times) / len(self.decision_times)) * 1000.0, 2)

    def get_avg_execution_ms(self) -> float:
        if not self.execution_times:
            return 0.0
        return round((sum(self.execution_times) / len(self.execution_times)) * 1000.0, 2)

    def get_system_metrics(self) -> Dict[str, Any]:
        """Queries CPU and Memory utilization."""
        cpu = 0.0
        memory = 0.0
        if psutil:
            try:
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory().percent
            except Exception:
                pass
        else:
            # Fallback simulated metrics
            cpu = 15.0
            memory = 45.0

        return {
            "cpu_usage_pct": cpu,
            "memory_usage_pct": memory
        }

    async def get_queue_metrics(self) -> Dict[str, Any]:
        """Queries the queue sizes of Event Bus and Execution Engine."""
        from core.bus import event_bus
        from execution.engine import execution_engine

        bus_size = event_bus._queue.qsize() if hasattr(event_bus, "_queue") else 0
        exec_size = await execution_engine.queue.get_size()
        
        return {
            "event_bus_queue_size": bus_size,
            "execution_queue_size": exec_size
        }
