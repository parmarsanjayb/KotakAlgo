from core.bus import event_bus, EventModel
from core.logging import get_logger
from scanner.models import ScanResult, ScannerEvent

logger = get_logger("scanner_publisher")

class ScannerPublisher:
    """
    Publishes matched scanning opportunities onto the Event Bus.
    """
    async def publish(self, result: ScanResult) -> None:
        evt = ScannerEvent(scan_result=result)
        await event_bus.publish(EventModel(
            event_type="scanner_match",
            source_agent="market_scanner_engine",
            payload=evt.model_dump()
        ))
        logger.info(
            f"Scanner OPPORTUNITY: {result.symbol} | Scanner: {result.scanner_name} | Priority: {result.priority}"
        )
