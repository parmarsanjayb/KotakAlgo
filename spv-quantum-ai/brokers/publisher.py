from core.bus import event_bus, EventModel
from core.logging import get_logger
from brokers.models import (
    Order, BrokerConnectedEvent, BrokerDisconnectedEvent,
    BrokerOrderPlacedEvent, BrokerOrderModifiedEvent, BrokerOrderCancelledEvent,
    BrokerHealthChangedEvent
)

logger = get_logger("broker_publisher")

class BrokerPublisher:
    """Publishes broker abstraction events onto the Event Bus."""
    
    async def publish_connected(self, broker: str, message: str = "Connected successfully") -> None:
        evt = BrokerConnectedEvent(broker=broker, message=message)
        await event_bus.publish(EventModel(
            event_type="broker_connected",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.info("Published broker_connected", broker=broker)

    async def publish_disconnected(self, broker: str, message: str = "Disconnected") -> None:
        evt = BrokerDisconnectedEvent(broker=broker, message=message)
        await event_bus.publish(EventModel(
            event_type="broker_disconnected",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.info("Published broker_disconnected", broker=broker)

    async def publish_order_placed(self, broker: str, order: Order) -> None:
        evt = BrokerOrderPlacedEvent(broker=broker, order=order)
        await event_bus.publish(EventModel(
            event_type="broker_order_placed",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published broker_order_placed", broker=broker, order_id=order.order_id)

    async def publish_order_modified(self, broker: str, order: Order) -> None:
        evt = BrokerOrderModifiedEvent(broker=broker, order=order)
        await event_bus.publish(EventModel(
            event_type="broker_order_modified",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published broker_order_modified", broker=broker, order_id=order.order_id)

    async def publish_order_cancelled(self, broker: str, order: Order) -> None:
        evt = BrokerOrderCancelledEvent(broker=broker, order=order)
        await event_bus.publish(EventModel(
            event_type="broker_order_cancelled",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published broker_order_cancelled", broker=broker, order_id=order.order_id)

    async def publish_health_changed(self, broker: str, is_healthy: bool, latency_ms: float, error: str = None) -> None:
        evt = BrokerHealthChangedEvent(broker=broker, is_healthy=is_healthy, latency_ms=latency_ms, error=error)
        await event_bus.publish(EventModel(
            event_type="broker_health_changed",
            source_agent="broker_abstraction",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published broker_health_changed", broker=broker, is_healthy=is_healthy)
