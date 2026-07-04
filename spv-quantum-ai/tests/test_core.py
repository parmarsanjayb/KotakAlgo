import pytest
import asyncio
from core.config import settings
from core.bus import event_bus, EventModel

def test_settings_load() -> None:
    """Verifies that dynamic yaml settings load successfully."""
    assert settings.yaml_config is not None
    assert settings.yaml_config.get("system", {}).get("name") == "SPV Quantum AI"
    assert settings.yaml_config.get("agents", {}).get("market_agent", {}).get("enabled") is True

@pytest.mark.asyncio
async def test_event_bus_pub_sub() -> None:
    """Verifies that event bus handles subscription, dispatch, and message receipt."""
    event_bus.start()
    received = []

    async def mock_callback(event: EventModel) -> None:
        received.append(event)

    # 1. Subscribe to topic
    await event_bus.subscribe("test_topic", mock_callback)

    # 2. Publish message
    await event_bus.publish("test_topic", "pytest_runner", {"test_key": "test_value"})
    
    # 3. Brief sleep to yield execution to concurrent background tasks
    await asyncio.sleep(0.05)

    # 4. Check results
    assert len(received) == 1
    assert received[0].event_type == "test_topic"
    assert received[0].source_agent == "pytest_runner"
    assert received[0].payload == {"test_key": "test_value"}

    # 5. Cleanup
    await event_bus.unsubscribe("test_topic", mock_callback)
    await event_bus.stop()
