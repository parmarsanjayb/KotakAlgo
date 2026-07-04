from core.bus import event_bus, EventModel
from core.logging import get_logger
from replay.models import (
    ReplayConfig, ReplayState, ReplayStartedEvent, ReplayPausedEvent,
    ReplayResumedEvent, ReplayStoppedEvent, ReplayCompletedEvent
)

logger = get_logger("replay_publisher")

class ReplayPublisher:
    """
    Publishes market replay control events onto the Event Bus.
    """
    async def publish_started(self, replay_id: str, config: ReplayConfig) -> None:
        evt = ReplayStartedEvent(replay_id=replay_id, config=config)
        await event_bus.publish(EventModel(
            event_type="replay_started",
            source_agent="replay_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_paused(self, replay_id: str, current_index: int) -> None:
        evt = ReplayPausedEvent(replay_id=replay_id, current_index=current_index)
        await event_bus.publish(EventModel(
            event_type="replay_paused",
            source_agent="replay_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_resumed(self, replay_id: str) -> None:
        evt = ReplayResumedEvent(replay_id=replay_id)
        await event_bus.publish(EventModel(
            event_type="replay_resumed",
            source_agent="replay_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_stopped(self, replay_id: str) -> None:
        evt = ReplayStoppedEvent(replay_id=replay_id)
        await event_bus.publish(EventModel(
            event_type="replay_stopped",
            source_agent="replay_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_completed(self, replay_id: str, state: ReplayState) -> None:
        evt = ReplayCompletedEvent(replay_id=replay_id, state=state)
        await event_bus.publish(EventModel(
            event_type="replay_completed",
            source_agent="replay_engine",
            payload=evt.model_dump(mode="json")
        ))
