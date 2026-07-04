import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from execution.models import ExecutionOrder, OrderLifecycleStatus
from core.logging import get_logger

logger = get_logger("order_tracker")

class OrderTracker:
    """
    Tracks order state transitions, caches open/pending/completed/rejected orders,
    and maintains a detailed audit trail.
    """
    def __init__(self) -> None:
        self._orders: Dict[str, ExecutionOrder] = {}
        self._audit_trail: Dict[str, List[tuple[datetime, OrderLifecycleStatus, str]]] = {}
        self._broker_latencies: List[float] = []
        self._lock = asyncio.Lock()

    async def add_order(self, order: ExecutionOrder) -> None:
        async with self._lock:
            self._orders[order.order_id] = order
            self._audit_trail[order.order_id] = [(datetime.now(timezone.utc), order.status, "Order created")]

    async def update_status(self, order_id: str, status: OrderLifecycleStatus, detail: str = "") -> None:
        async with self._lock:
            order = self._orders.get(order_id)
            if order:
                order.status = status
                order.updated_at = datetime.now(timezone.utc)
                self._audit_trail[order_id].append((datetime.now(timezone.utc), status, detail))
                logger.info(f"Order {order_id} transition to {status.value} | Details: {detail}")

    async def record_latency(self, order_id: str, latency_ms: float) -> None:
        async with self._lock:
            order = self._orders.get(order_id)
            if order:
                order.broker_latency_ms = latency_ms
            self._broker_latencies.append(latency_ms)
            if len(self._broker_latencies) > 100:
                self._broker_latencies.pop(0)

    async def get_order(self, order_id: str) -> Optional[ExecutionOrder]:
        async with self._lock:
            return self._orders.get(order_id)

    async def get_orders_by_status(self, statuses: List[OrderLifecycleStatus]) -> List[ExecutionOrder]:
        async with self._lock:
            return [o for o in self._orders.values() if o.status in statuses]

    async def get_open_orders(self) -> List[ExecutionOrder]:
        # OPEN: QUEUED, SENT, ACKNOWLEDGED, PARTIALLY_FILLED
        return await self.get_orders_by_status([
            OrderLifecycleStatus.QUEUED,
            OrderLifecycleStatus.SENT,
            OrderLifecycleStatus.ACKNOWLEDGED,
            OrderLifecycleStatus.PARTIALLY_FILLED
        ])

    async def get_pending_orders(self) -> List[ExecutionOrder]:
        return await self.get_orders_by_status([
            OrderLifecycleStatus.NEW,
            OrderLifecycleStatus.VALIDATED,
            OrderLifecycleStatus.QUEUED
        ])

    async def get_completed_orders(self) -> List[ExecutionOrder]:
        return await self.get_orders_by_status([
            OrderLifecycleStatus.FILLED,
            OrderLifecycleStatus.CANCELLED
        ])

    async def get_rejected_orders(self) -> List[ExecutionOrder]:
        return await self.get_orders_by_status([
            OrderLifecycleStatus.REJECTED,
            OrderLifecycleStatus.FAILED
        ])

    async def get_average_latency_ms(self) -> float:
        async with self._lock:
            if not self._broker_latencies:
                return 0.0
            return sum(self._broker_latencies) / len(self._broker_latencies)

    async def get_audit_trail(self, order_id: str) -> List[tuple[datetime, OrderLifecycleStatus, str]]:
        async with self._lock:
            return self._audit_trail.get(order_id, [])

    async def get_all_orders(self) -> List[ExecutionOrder]:
        async with self._lock:
            return list(self._orders.values())
