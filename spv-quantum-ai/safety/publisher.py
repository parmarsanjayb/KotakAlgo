from typing import Any, Dict
from core.bus import event_bus, EventModel
from safety.models import (
    SafetyResponse, SafetyCheckPassedEvent, SafetyBlockedEvent,
    EmergencyTriggeredEvent, HiddenStopTriggeredEvent, TrailingUpdatedEvent
)

class SafetyPublisher:
    """Delivers safety engine events to the centralized event bus."""
    async def publish_passed(self, order_details: Dict[str, Any], response: SafetyResponse) -> None:
        evt = SafetyCheckPassedEvent(order_details=order_details, response=response)
        await event_bus.publish(EventModel(
            event_type="safety_check_passed",
            source_agent="safety_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_blocked(self, order_details: Dict[str, Any], response: SafetyResponse) -> None:
        evt = SafetyBlockedEvent(order_details=order_details, response=response)
        await event_bus.publish(EventModel(
            event_type="safety_blocked",
            source_agent="safety_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_emergency(self, action: str, message: str) -> None:
        evt = EmergencyTriggeredEvent(action=action, message=message)
        await event_bus.publish(EventModel(
            event_type="emergency_triggered",
            source_agent="safety_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_hidden_stop(self, symbol: str, side: str, qty: float, trigger_price: float, exit_price: float, msg: str) -> None:
        evt = HiddenStopTriggeredEvent(
            symbol=symbol, side=side, quantity=qty, trigger_price=trigger_price, exit_price=exit_price, message=msg
        )
        await event_bus.publish(EventModel(
            event_type="hidden_stop_triggered",
            source_agent="safety_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_trailing_updated(self, symbol: str, old_stop: float, new_stop: float, current: float, reason: str) -> None:
        evt = TrailingUpdatedEvent(
            symbol=symbol, old_stop_price=old_stop, new_stop_price=new_stop, current_price=current, reason=reason
        )
        await event_bus.publish(EventModel(
            event_type="trailing_updated",
            source_agent="safety_engine",
            payload=evt.model_dump(mode="json")
        ))
