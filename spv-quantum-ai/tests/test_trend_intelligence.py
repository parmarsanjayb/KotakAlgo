import pytest
import asyncio
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from market.models import Timeframe, Candle
from employees.models import EmployeeState, EmployeeType
from employees import employee_engine
from employees.trend_intelligence import TrendIntelligenceEmployee

@pytest.fixture(autouse=True)
def reset_employee_engine():
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()
    yield
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()

@pytest.mark.asyncio
async def test_trend_intelligence_employee_initialization():
    emp = TrendIntelligenceEmployee()
    assert emp._running is False
    assert emp._heartbeat_task is None
    assert not emp.latest_results

@pytest.mark.asyncio
async def test_trend_intelligence_employee_lifecycle():
    emp = TrendIntelligenceEmployee()
    await emp.start()
    assert emp._running is True
    assert emp._heartbeat_task is not None
    
    await emp.stop()
    assert emp._running is False
    assert emp._heartbeat_task is None

@pytest.mark.asyncio
async def test_trend_intelligence_analysis_neutral():
    emp = TrendIntelligenceEmployee()
    await emp.start()
    
    # Send less than 20 candles
    candle = Candle(
        symbol="NIFTY50",
        timeframe=Timeframe.M1,
        open=100.0,
        high=105.0,
        low=95.0,
        close=100.0,
        volume=1000.0,
        complete=True,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await emp.analyze_trend(candle)
    assert res["trend"] == "NO TRADE"
    assert res["confidence"] == 0.0
    assert res["recommendation"] == "WAIT"
    
    await emp.stop()

@pytest.mark.asyncio
async def test_trend_intelligence_bullish_trends():
    emp = TrendIntelligenceEmployee()
    await emp.start()
    
    # Seed 35 candles rising upwards
    for i in range(35):
        price = 100.0 + i * 2.0
        candle = Candle(
            symbol="NIFTY50",
            timeframe=Timeframe.M1,
            open=price - 1.0,
            high=price + 3.0,
            low=price - 2.0,
            close=price,
            volume=1000.0 + i * 10,
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        res = await emp.analyze_trend(candle)
        
    # Latest result should have positive trend (BULLISH or STRONG BULLISH)
    assert "BULLISH" in res["trend"]
    assert res["recommendation"] == "BUY"
    assert res["confidence"] > 50.0
    
    await emp.stop()

@pytest.mark.asyncio
async def test_trend_intelligence_bearish_trends():
    emp = TrendIntelligenceEmployee()
    await emp.start()
    
    # Seed 35 candles falling downwards
    for i in range(35):
        price = 200.0 - i * 2.0
        candle = Candle(
            symbol="NIFTY50",
            timeframe=Timeframe.M1,
            open=price + 1.0,
            high=price + 2.0,
            low=price - 3.0,
            close=price,
            volume=1000.0 + i * 10,
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        res = await emp.analyze_trend(candle)
        
    assert "BEARISH" in res["trend"]
    assert res["recommendation"] == "SELL"
    assert res["confidence"] > 50.0
    
    await emp.stop()

@pytest.mark.asyncio
async def test_trend_intelligence_event_bus_publishing():
    event_bus.start()
    emp = TrendIntelligenceEmployee()
    await emp.start()
    
    updates = []
    signals = []
    
    async def trend_update_cb(event):
        updates.append(event)
        
    async def trend_signal_cb(event):
        signals.append(event)
        
    await event_bus.subscribe("trend_updated", trend_update_cb)
    await event_bus.subscribe("trend_signal", trend_signal_cb)
    
    try:
        # Seed rising candles
        for i in range(25):
            price = 100.0 + i * 2.0
            candle = Candle(
                symbol="NIFTY50",
                timeframe=Timeframe.M1,
                open=price - 1.0,
                high=price + 2.0,
                low=price - 2.0,
                close=price,
                volume=1000.0,
                complete=True,
                timestamp=datetime.now(timezone.utc)
            )
            await emp.analyze_trend(candle)
            
        await asyncio.sleep(0.05)
        
        # We must have trend_updated events published
        assert len(updates) > 0
        assert updates[-1].payload["symbol"] == "NIFTY50"
        
        # We must have trend_signal event since recommendation shifted from WAIT to BUY
        assert len(signals) > 0
        assert signals[0].payload["signal_type"] == "BUY"
        
    finally:
        await event_bus.unsubscribe("trend_updated", trend_update_cb)
        await event_bus.unsubscribe("trend_signal", trend_signal_cb)
        await emp.stop()
        await event_bus.stop()

@pytest.mark.asyncio
async def test_employee_engine_trend_integration():
    # Verify registration at startup
    await employee_engine.start()
    try:
        # Should have EMP-TRD profile registered
        profile = employee_engine.manager.get_profile("EMP-TRD")
        assert profile is not None
        assert profile.name == "Default Trend Intelligence Specialist"
        assert profile.employee_type == EmployeeType.TREND_INTELLIGENCE
        
        # Verify the heartbeat loop is active and recorded activity
        await asyncio.sleep(0.2)
        assert profile.is_active is True
        assert profile.health_status == "HEALTHY"
    finally:
        await employee_engine.stop()
