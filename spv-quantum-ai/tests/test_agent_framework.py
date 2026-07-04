import pytest
import asyncio
from core.bus import event_bus, EventModel
from core.agent import BaseAgent, AgentResultModel
from agents.registry import agent_registry
from agents.manager import AgentManager

def test_registry_discovery() -> None:
    """Verifies that agents/ directory scanning automatically registers our agent classes with snake_case."""
    discovered = agent_registry.discover_agents()
    assert "market_agent" in discovered
    assert "risk_agent" in discovered
    assert "execution_agent" in discovered
    assert "telegram_notifier" in discovered

@pytest.mark.asyncio
async def test_dynamic_event_mapping() -> None:
    """Verifies that publishing to priority queue enqueues and processes events correctly."""
    event_bus.start()
    received = []

    async def cb(event: EventModel) -> None:
        received.append(event)

    # Subscribe to market_data
    await event_bus.subscribe("market_data", cb)

    # Publish dictionary -> should dynamically map to EventModel
    await event_bus.publish("market_data", "test_sender", {"price": 12.34})
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].event_type == "market_data"
    assert received[0].source_agent == "test_sender"
    assert received[0].payload == {"price": 12.34}

    # Cleanup
    await event_bus.unsubscribe("market_data", cb)
    await event_bus.stop()

@pytest.mark.asyncio
async def test_agent_middleware_telemetry() -> None:
    """Verifies that receiving events logs metrics, updates execution times, and stores AgentResultModel decisions."""
    from agents.risk_agent import RiskAgent
    agent = RiskAgent()
    await agent.initialize()
    agent.status = "RUNNING"

    # Mock order request event
    event = EventModel(
        source_agent="strategy_agent",
        event_type="order_request",
        payload={"symbol": "ETHUSD", "quantity": 1.5, "price": 3000.0}
    )

    # Trigger receive
    await agent.receive_event(event)

    # Verify telemetry results from middleware logging
    assert agent.execution_time > 0.0
    assert agent.last_decision is not None
    assert isinstance(agent.last_decision, AgentResultModel)
    assert agent.last_decision.signal == "BUY"
    assert agent.confidence_score == 100.0
    assert len(agent.logs) > 0
    assert "Executed Event type 'order_request'" in agent.logs[-1]

@pytest.mark.asyncio
async def test_manager_health_restart() -> None:
    """Verifies supervisor daemon successfully restarts agents entering FAILED states."""
    manager = AgentManager()
    manager.load_agents()

    assert "risk_agent" in manager.active_agents
    initial_risk = manager.active_agents["risk_agent"]

    # Start all agents
    await manager.start_all()
    assert initial_risk.status == "RUNNING"

    # Simulate crash state
    initial_risk.status = "FAILED"

    # Fire supervisor audit
    await manager.audit_health_and_supervise()

    # Assert hot-swap happened
    restarted_risk = manager.active_agents["risk_agent"]
    assert restarted_risk is not initial_risk  # Reference swap check
    assert restarted_risk.status == "RUNNING"
    
    # Check if healthy
    assert await restarted_risk.health_check() == "HEALTHY"

    # Shutdown
    await manager.stop_all()
