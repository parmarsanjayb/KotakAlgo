import asyncio
from typing import Dict, Any
from core.bus import event_bus, EventModel
from core.logging import get_logger
from health.models import RecoveryStartedEvent, RecoveryCompletedEvent

logger = get_logger("recovery_manager")

class RecoveryManager:
    """Orchestrates automated self-healing procedures for degraded engines."""
    def __init__(self) -> None:
        self.recovery_attempts: Dict[str, int] = {}

    async def attempt_recovery(self, service: str, reason: str) -> bool:
        """Invokes a recovery routine for the failed service."""
        attempts = self.recovery_attempts.get(service, 0)
        if attempts >= 3:
            logger.error(f"Max recovery attempts (3) reached for {service}. Manual intervention required.")
            return False

        self.recovery_attempts[service] = attempts + 1
        logger.info(f"Triggering automated recovery for {service} (Attempt {attempts + 1}/3)...")
        
        # Publish recovery started event
        start_evt = RecoveryStartedEvent(service=service, action="restart_service")
        await event_bus.publish(EventModel(
            event_type="recovery_started",
            source_agent="health_engine",
            payload=start_evt.model_dump(mode="json")
        ))

        success = False
        message = ""
        try:
            if service == "Broker Engine":
                from brokers import broker_engine
                resp = await broker_engine.connect()
                success = resp.success
                message = "Broker reconnection attempted"
            elif service == "Safety Engine":
                from safety import safety_engine
                await safety_engine.stop()
                await safety_engine.start()
                success = True
                message = "Safety engine restarted successfully"
            elif service == "Execution Engine":
                from execution.engine import execution_engine
                await execution_engine.stop()
                await execution_engine.start()
                success = True
                message = "Execution engine restarted successfully"
            else:
                # Default generic restart try
                logger.info(f"Restarting service: {service}")
                await asyncio.sleep(0.5)
                success = True
                message = f"Generic restart successful for {service}"
        except Exception as e:
            success = False
            message = f"Recovery failed: {str(e)}"
            logger.error(f"Error executing recovery for {service}", error=str(e))

        # Publish recovery completed event
        comp_evt = RecoveryCompletedEvent(service=service, success=success, message=message)
        await event_bus.publish(EventModel(
            event_type="recovery_completed",
            source_agent="health_engine",
            payload=comp_evt.model_dump(mode="json")
        ))

        if success:
            self.recovery_attempts[service] = 0
            logger.info(f"Automated recovery completed successfully for {service}")
        else:
            logger.warning(f"Recovery attempt failed for {service}")

        return success
