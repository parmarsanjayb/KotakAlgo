from core.bus import event_bus, EventModel
from core.logging import get_logger
from execution.models import (
    ExecutionOrder, OrderSubmittedEvent, OrderFilledEvent,
    OrderRejectedEvent, OrderCancelledEvent, ExecutionFailedEvent
)

logger = get_logger("execution_publisher")

class ExecutionPublisher:
    """
    Publishes execution lifecycle events onto the Event Bus.
    """
    async def publish_submitted(self, order: ExecutionOrder) -> None:
        evt = OrderSubmittedEvent(order=order)
        await event_bus.publish(EventModel(
            event_type="order_submitted",
            source_agent="execution_engine",
            payload=evt.model_dump()
        ))

    async def publish_filled(self, order: ExecutionOrder) -> None:
        evt = OrderFilledEvent(order=order)
        await event_bus.publish(EventModel(
            event_type="order_filled",
            source_agent="execution_engine",
            payload=evt.model_dump()
        ))

    async def publish_rejected(self, order: ExecutionOrder, reason: str) -> None:
        evt = OrderRejectedEvent(order=order, reason=reason)
        await event_bus.publish(EventModel(
            event_type="order_rejected",
            source_agent="execution_engine",
            payload=evt.model_dump()
        ))

    async def publish_cancelled(self, order: ExecutionOrder) -> None:
        evt = OrderCancelledEvent(order=order)
        await event_bus.publish(EventModel(
            event_type="order_cancelled",
            source_agent="execution_engine",
            payload=evt.model_dump()
        ))

    async def publish_failed(self, order: ExecutionOrder, error_msg: str) -> None:
        evt = ExecutionFailedEvent(order=order, error_message=error_msg)
        await event_bus.publish(EventModel(
            event_type="execution_failed",
            source_agent="execution_engine",
            payload=evt.model_dump()
        ))
