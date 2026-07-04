import pytest
import asyncio
from datetime import datetime, timezone
from paper.models import PaperTradingConfig, PaperTradingState
from paper.engine import paper_trading_engine
from core.bus import event_bus, EventModel

@pytest.mark.asyncio
async def test_paper_trading_lifecycle_and_events():
    event_bus.start()
    
    # 1. Config
    config = PaperTradingConfig(
        initial_capital=500000.0,
        latency_ms=25.0,
        slippage_pct=0.0002
    )

    # 2. Start session
    await paper_trading_engine.start()
    session_id = await paper_trading_engine.start_session(config)
    assert session_id.startswith("PPS-")
    
    status = await paper_trading_engine.get_dashboard_status()
    assert status["is_running"] is True
    assert status["virtual_capital"] == 500000.0
    assert status["virtual_pnl"] == 0.0

    # Track published paper events
    paper_events = []
    async def cb(evt: EventModel):
        if evt.event_type.startswith("paper_"):
            paper_events.append(evt)
            
    await event_bus.subscribe("paper_order_placed", cb)
    await event_bus.subscribe("paper_order_filled", cb)
    await event_bus.subscribe("paper_trade_closed", cb)

    # 3. Simulate order submitted
    await event_bus.publish(EventModel(
        source_agent="execution_engine",
        event_type="order_submitted",
        payload={
            "order_id": "ORD-123",
            "symbol": "INFY",
            "side": "BUY",
            "quantity": 10.0,
            "price": 1500.0
        }
    ))
    
    # Wait for processing
    for _ in range(20):
        if len(paper_events) >= 1:
            break
        await asyncio.sleep(0.05)
        
    assert len(paper_events) >= 1
    assert paper_events[0].event_type == "paper_order_placed"
    assert paper_events[0].payload["order_id"] == "ORD-123"

    # 4. Simulate order filled
    await event_bus.publish(EventModel(
        source_agent="execution_engine",
        event_type="order_filled",
        payload={
            "order_id": "ORD-123",
            "symbol": "INFY",
            "side": "BUY",
            "filled_quantity": 10.0,
            "avg_fill_price": 1500.0,
            "broker_latency_ms": 25.0
        }
    ))
    
    # Wait for processing
    for _ in range(20):
        if len(paper_events) >= 2:
            break
        await asyncio.sleep(0.05)
        
    assert len(paper_events) >= 2
    assert paper_events[1].event_type == "paper_order_filled"
    assert paper_events[1].payload["order_id"] == "ORD-123"
    assert paper_events[1].payload["latency_ms"] == 25.0

    # 5. Simulate trade closed
    from journal.repository import TradeHistoryRepository
    TradeHistoryRepository._in_memory_journal.clear()
    
    await event_bus.publish(EventModel(
        source_agent="trade_journal_engine",
        event_type="trade_closed",
        payload={
            "symbol": "INFY",
            "realized_pnl": 500.0,
            "holding_duration": 120.0
        }
    ))
    
    # Wait for processing
    for _ in range(20):
        if len(paper_events) >= 3:
            break
        await asyncio.sleep(0.05)
        
    assert len(paper_events) >= 3
    assert paper_events[2].event_type == "paper_trade_closed"
    assert paper_events[2].payload["pnl"] == 500.0
    
    # Verify capital update
    status_updated = await paper_trading_engine.get_dashboard_status()
    assert status_updated["virtual_capital"] == 500500.0
    assert status_updated["virtual_pnl"] == 500.0

    # 6. Stop session
    await paper_trading_engine.stop_session()
    status_stopped = await paper_trading_engine.get_dashboard_status()
    assert status_stopped["is_running"] is False
    
    await paper_trading_engine.stop()
    await event_bus.unsubscribe("paper_order_placed", cb)
    await event_bus.unsubscribe("paper_order_filled", cb)
    await event_bus.unsubscribe("paper_trade_closed", cb)
    await event_bus.stop()
