from core.bus import event_bus, EventModel
from core.logging import get_logger
from scoring.models import DecisionScoreResult, DecisionScoreEvent

logger = get_logger("decision_publisher")

class DecisionPublisher:
    """
    Publishes DecisionScoreEvent onto the Event Bus.
    """
    async def publish(self, result: DecisionScoreResult) -> None:
        evt = DecisionScoreEvent(decision_score=result)
        await event_bus.publish(EventModel(
            event_type="decision_score",
            source_agent="decision_scoring_engine",
            payload=evt.model_dump()
        ))
        logger.info(
            f"Decision Score Published: {result.symbol} ({result.timeframe}) | Conf: {result.overall_confidence}% | Quality: {result.decision_quality.value}"
        )
