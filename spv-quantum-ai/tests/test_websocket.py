import pytest
import asyncio
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from core.bus import event_bus, EventModel
from dashboard.main import app

@pytest.mark.asyncio
async def test_websocket_connection_and_broadcast():
    client = TestClient(app)
    
    # Start event bus (uses currently running asyncio test loop)
    event_bus.start()
    
    try:
        # In TestClient, websocket_connect is a synchronous blocking context manager
        # But it runs the app's lifespans and websocket inside the event loop.
        # We can run it in a thread or directly if it's non-blocking.
        with client.websocket_connect("/ws") as websocket:
            # Subscribe the broadcaster callback
            from dashboard.main import ws_event_broadcaster
            await event_bus.subscribe("test_topic", ws_event_broadcaster)
            
            # Publish a test event
            evt = EventModel(
                event_type="test_topic",
                source_agent="test_agent",
                payload={"message": "hello websocket"}
            )
            await event_bus.publish(evt)
            await asyncio.sleep(0.05)
            
            # Receive data via websocket
            data = websocket.receive_json()
            assert data["topic"] == "test_topic"
            assert data["sender"] == "test_agent"
            assert data["data"]["message"] == "hello websocket"
            
            # Clean up subscription
            await event_bus.unsubscribe("test_topic", ws_event_broadcaster)
            
    finally:
        await event_bus.stop()
