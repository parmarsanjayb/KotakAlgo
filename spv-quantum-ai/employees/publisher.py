from typing import Any, Dict
from core.bus import event_bus, EventModel
from employees.models import (
    EmployeeActivatedEvent, EmployeePausedEvent,
    EmployeeProfileUpdatedEvent, EmployeeCapitalUpdatedEvent
)

class EmployeePublisher:
    """Delivers employee state transitions and updates to the event bus."""
    async def publish_activated(self, employee_code: str, name: str) -> None:
        evt = EmployeeActivatedEvent(employee_code=employee_code, name=name)
        await event_bus.publish(EventModel(
            event_type="employee_activated",
            source_agent="employee_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_paused(self, employee_code: str, name: str) -> None:
        evt = EmployeePausedEvent(employee_code=employee_code, name=name)
        await event_bus.publish(EventModel(
            event_type="employee_paused",
            source_agent="employee_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_profile_updated(self, employee_code: str, updates: Dict[str, Any]) -> None:
        evt = EmployeeProfileUpdatedEvent(employee_code=employee_code, profile_updates=updates)
        await event_bus.publish(EventModel(
            event_type="employee_profile_updated",
            source_agent="employee_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_capital_updated(self, employee_code: str, capital: float) -> None:
        evt = EmployeeCapitalUpdatedEvent(employee_code=employee_code, allocated_capital=capital)
        await event_bus.publish(EventModel(
            event_type="employee_capital_updated",
            source_agent="employee_engine",
            payload=evt.model_dump(mode="json")
        ))
