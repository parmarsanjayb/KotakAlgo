import pytest
import asyncio
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from brokers.models import BrokerResponse, Funds, Order, OrderStatus, OrderSide, OrderType
from brokers.kotak_neo import KotakNeoAdapter

@pytest.mark.asyncio
async def test_kotak_neo_adapter_connect_disconnect():
    adapter = KotakNeoAdapter()
    assert adapter.is_connected() is False

    resp = await adapter.connect()
    assert resp.success is True
    assert adapter.is_connected() is True
    assert adapter.session_mgr.session_status == "CONNECTED"

    resp = await adapter.disconnect()
    assert resp.success is True
    assert adapter.is_connected() is False
    assert adapter.session_mgr.session_status == "DISCONNECTED"

@pytest.mark.asyncio
async def test_kotak_neo_adapter_reconnect_and_events():
    event_bus.start()
    
    events = []
    async def capture_event(evt: EventModel):
        events.append(evt)
        
    await event_bus.subscribe("kotak_connected", capture_event)
    await event_bus.subscribe("kotak_order_placed", capture_event)
    await event_bus.subscribe("kotak_order_filled", capture_event)
    await event_bus.subscribe("kotak_disconnected", capture_event)
    await event_bus.subscribe("kotak_session_expired", capture_event)

    try:
        adapter = KotakNeoAdapter()
        await adapter.connect()
        
        await asyncio.sleep(0.05)
        assert any(e.event_type == "kotak_connected" for e in events)

        # Test place order triggers placement and fill events
        resp = await adapter.place_order(
            symbol="RELIANCE",
            side=OrderSide.BUY,
            quantity=10.0,
            order_type=OrderType.MARKET,
            price=2500.0
        )
        assert resp.success is True
        
        await asyncio.sleep(0.05)
        assert any(e.event_type == "kotak_order_placed" for e in events)
        assert any(e.event_type == "kotak_order_filled" for e in events)

        # Force reconnect simulation
        await adapter.session_mgr.reconnect()
        assert adapter.is_connected() is True

        # Test disconnect
        await adapter.disconnect()
        await asyncio.sleep(0.05)
        assert any(e.event_type == "kotak_disconnected" for e in events)

    finally:
        await event_bus.stop()

def test_kotak_order_manager_status_mapping():
    adapter = KotakNeoAdapter()
    mgr = adapter.order_mgr
    
    assert mgr.map_status("Complete") == OrderStatus.FILLED
    assert mgr.map_status("Open") == OrderStatus.OPEN
    assert mgr.map_status("Trg Pending") == OrderStatus.TRIGGER_PENDING
    assert mgr.map_status("Partially Filled") == OrderStatus.PARTIAL
    assert mgr.map_status("Cancelled") == OrderStatus.CANCELLED
    assert mgr.map_status("Rejected") == OrderStatus.REJECTED
    assert mgr.map_status("InvalidStatus") == OrderStatus.NEW

@pytest.mark.asyncio
async def test_kotak_funds_manager():
    adapter = KotakNeoAdapter()
    await adapter.connect()
    
    resp = await adapter.get_funds()
    assert resp.success is True
    assert resp.data["equity"] == 150000.0
    
    margin_resp = await adapter.get_margin()
    assert margin_resp.success is True
    assert margin_resp.data["available_margin"] == 150000.0
