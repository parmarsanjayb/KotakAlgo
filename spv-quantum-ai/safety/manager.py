from typing import Dict, Any
from safety.models import SafetyResponse, SafetyStatus
from safety.publisher import SafetyPublisher
from safety.guard import TradingGuard
from safety.protection import ProtectionManager
from safety.emergency import EmergencyManager
from core.logging import get_logger

logger = get_logger("safety_manager")

class SafetyManager:
    """Orchestrates system safety checks, guards, trailing stops, and emergency switches."""
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.publisher = SafetyPublisher()
        self.guard = TradingGuard(config)
        self.protection = ProtectionManager(config, self.publisher)
        self.emergency = EmergencyManager(self.publisher)

    async def start(self) -> None:
        await self.protection.start()
        logger.info("SafetyManager sub-systems started.")

    async def stop(self) -> None:
        await self.protection.stop()
        logger.info("SafetyManager sub-systems stopped.")

    async def evaluate_order(self, order_data: Dict[str, Any]) -> SafetyResponse:
        """Evaluates whether the order passes all safety and capital protection guards."""
        # 1. Evaluate emergency status overrides
        if self.emergency.kill_switch_active:
            resp = SafetyResponse(allowed=False, status=SafetyStatus.BLOCKED, reason="Emergency Kill Switch is Active")
            await self.publisher.publish_blocked(order_data, resp)
            return resp

        if self.emergency.trading_paused:
            resp = SafetyResponse(allowed=False, status=SafetyStatus.BLOCKED, reason="Trading is currently paused")
            await self.publisher.publish_blocked(order_data, resp)
            return resp

        if self.emergency.new_entries_disabled:
            resp = SafetyResponse(allowed=False, status=SafetyStatus.BLOCKED, reason="New trade entries are disabled")
            await self.publisher.publish_blocked(order_data, resp)
            return resp

        # 2. Evaluate all pre-trade guards
        allowed, reason = await self.guard.check_all(order_data)
        if not allowed:
            resp = SafetyResponse(allowed=False, status=SafetyStatus.BLOCKED, reason=reason)
            await self.publisher.publish_blocked(order_data, resp)
            return resp

        # 3. Passed
        resp = SafetyResponse(allowed=True, status=SafetyStatus.PASSED, reason="Passed all safety checks")
        await self.publisher.publish_passed(order_data, resp)
        return resp
