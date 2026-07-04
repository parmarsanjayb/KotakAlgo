from core.bus import event_bus, EventModel
from core.logging import get_logger
from analytics.models import (
    PerformanceMetrics, PerformanceReport, PerformanceUpdatedEvent,
    DailyReportGeneratedEvent, MonthlyReportGeneratedEvent
)

logger = get_logger("analytics_publisher")

class PerformancePublisher:
    """
    Publishes performance statistics and report events onto the Event Bus.
    """
    async def publish_updated(self, metrics: PerformanceMetrics) -> None:
        evt = PerformanceUpdatedEvent(metrics=metrics)
        await event_bus.publish(EventModel(
            event_type="performance_updated",
            source_agent="performance_analytics_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_daily_report(self, report: PerformanceReport) -> None:
        evt = DailyReportGeneratedEvent(report=report)
        await event_bus.publish(EventModel(
            event_type="daily_report_generated",
            source_agent="performance_analytics_engine",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_monthly_report(self, report: PerformanceReport) -> None:
        evt = MonthlyReportGeneratedEvent(report=report)
        await event_bus.publish(EventModel(
            event_type="monthly_report_generated",
            source_agent="performance_analytics_engine",
            payload=evt.model_dump(mode="json")
        ))
