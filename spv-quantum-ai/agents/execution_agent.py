import time
import asyncio
from typing import Any, Dict, List, Optional
from core.agent import BaseAgent, AgentResultModel
from core.bus import EventModel
from execution.engine import execution_engine
from execution.models import OrderLifecycleStatus

class ExecutionAgent(BaseAgent):
    """
    Receives risk-approved order requests and enqueues them in the Enterprise Execution Engine.
    Tracks execution status and response details.
    """
    def __init__(self) -> None:
        super().__init__(
            name="execution_agent",
            description="Routes risk-approved orders through the Enterprise Execution Engine"
        )

    @property
    def input_event_types(self) -> List[str]:
        return ["order_approved"]

    @property
    def output_event_types(self) -> List[str]:
        return ["order_filled", "portfolio_update", "execution_failed", "risk_alert"]

    async def initialize(self) -> None:
        await execution_engine.start()
        self.log_info("ExecutionAgent initialized and started ExecutionEngine.")

    async def shutdown(self) -> None:
        await execution_engine.stop()
        self.log_info("ExecutionAgent shutdown and stopped ExecutionEngine.")

    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        if event.event_type == "order_approved":
            return await self._execute_order(event.payload)
        return None

    async def _execute_order(self, order_data: Dict[str, Any]) -> AgentResultModel:
        start_time = time.perf_counter()
        
        # Enqueue order via execution engine
        order = await execution_engine.submit_order_request(order_data)
        
        # Await completion for synchronous AgentResultModel response
        # (while retaining underlying queuing/worker loop)
        for _ in range(100):
            if order.status in (
                OrderLifecycleStatus.FILLED, 
                OrderLifecycleStatus.REJECTED, 
                OrderLifecycleStatus.FAILED
            ):
                break
            await asyncio.sleep(0.02)
            
        processing_time = (time.perf_counter() - start_time) * 1000.0
        
        if order.status == OrderLifecycleStatus.FILLED:
            # Trigger portfolio update event
            try:
                from brokers.manager import broker_manager
                broker = broker_manager.get_active()
                balance_resp = await broker.get_balance()
                if balance_resp.success:
                    await self.publish_result("portfolio_update", balance_resp.data)
            except Exception as e:
                self.log_error(f"Failed to publish portfolio update: {e}")
                
            return AgentResultModel(
                agent_name=self.agent_name,
                signal=order.side,
                confidence=100.0,
                reason=f"Order executed successfully via Execution Engine. Status={order.status.value}",
                processing_time=processing_time,
                metadata={"order_id": order.order_id, "status": order.status.value, "avg_price": order.avg_fill_price}
            )
        else:
            reason = order.rejection_reason or f"Execution failed. Status={order.status.value}"
            self.log_error(f"Execution failed: {reason}")
            
            # Publish failed events
            await self.publish_result("risk_alert", {
                "alert_type": "EXECUTION_FAILURE",
                "message": f"Execution failed for {order.symbol}: {reason}",
                "order_details": order_data
            }, priority=1)
            
            return AgentResultModel(
                agent_name=self.agent_name,
                signal="NONE",
                confidence=0.0,
                reason=f"Execution failure: {reason}",
                processing_time=processing_time,
                metadata={"status": order.status.value}
            )
