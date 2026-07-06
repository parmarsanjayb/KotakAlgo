import pytest
import asyncio
from datetime import datetime, timezone
from market.models import Candle, Timeframe
from employees.volume_intelligence import VolumeIntelligenceEmployee
from employees.models import EmployeeType
from employees.engine import employee_engine
from core.bus import event_bus, EventModel

@pytest.mark.asyncio
async def test_volume_intelligence_employee_initialization():
    vi = VolumeIntelligenceEmployee()
    assert vi._running is False
    assert len(vi.candles_history) == 0
    assert len(vi.latest_results) == 0

@pytest.mark.asyncio
async def test_volume_intelligence_employee_lifecycle():
    vi = VolumeIntelligenceEmployee()
    await vi.start()
    assert vi._running is True
    await vi.stop()
    assert vi._running is False

@pytest.mark.asyncio
async def test_volume_intelligence_analysis_neutral():
    vi = VolumeIntelligenceEmployee()
    
    # Send 1 candle (insufficient history)
    candle = Candle(
        symbol="BTCUSD",
        timeframe=Timeframe.M1,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.5,
        volume=1000.0,
        complete=True,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await vi.analyze_volume(candle)
    assert res["symbol"] == "BTCUSD"
    assert res["rvol"] == 1.0
    assert res["volume_trend"] == "NEUTRAL"
    assert res["confirmation_status"] == "NEUTRAL"
    assert res["confidence"] == 50.0

@pytest.mark.asyncio
async def test_volume_intelligence_analysis_spike():
    vi = VolumeIntelligenceEmployee()
    
    # Populate history with low volume candles
    for i in range(10):
        candle = Candle(
            symbol="BTCUSD",
            timeframe=Timeframe.M1,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=100.0, # Low constant volume
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        await vi.analyze_volume(candle)
        
    # Send a volume spike candle
    spike_candle = Candle(
        symbol="BTCUSD",
        timeframe=Timeframe.M1,
        open=100.0,
        high=105.0,
        low=100.0,
        close=104.5,
        volume=500.0, # 5x average volume spike
        complete=True,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await vi.analyze_volume(spike_candle)
    assert res["avg_volume"] == 100.0
    assert res["rvol"] == 5.0
    assert res["volume_spike"] is True
    assert res["confirmation_status"] == "CONFIRM"
    assert res["confidence"] > 50.0

@pytest.mark.asyncio
async def test_volume_intelligence_fake_breakout():
    vi = VolumeIntelligenceEmployee()
    
    # Populate history with moderate volume candles
    for i in range(10):
        candle = Candle(
            symbol="BTCUSD",
            timeframe=Timeframe.M1,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=500.0,
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        await vi.analyze_volume(candle)
        
    # Send a strong price breakout candle with very low volume (fake breakout)
    fake_candle = Candle(
        symbol="BTCUSD",
        timeframe=Timeframe.M1,
        open=100.0,
        high=105.0,
        low=100.0,
        close=104.5, # Strong price increase (> 4.5% body size)
        volume=50.0,  # 0.1x average volume
        complete=True,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await vi.analyze_volume(fake_candle)
    assert res["fake_breakout"] is True
    assert res["confirmation_status"] == "REJECT"
    assert res["confidence"] < 35.0

@pytest.mark.asyncio
async def test_employee_engine_volume_rejection():
    # Make sure employee engine is running
    await employee_engine.start()
    
    try:
        vi = employee_engine.volume_intelligence
        
        # Populate history
        for i in range(10):
            candle = Candle(
                symbol="ETHUSD",
                timeframe=Timeframe.M1,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=100.0,
                complete=True,
                timestamp=datetime.now(timezone.utc)
            )
            await vi.analyze_volume(candle)
            
        # Place a fake breakout (which triggers reject status)
        fake_candle = Candle(
            symbol="ETHUSD",
            timeframe=Timeframe.M1,
            open=100.0,
            high=105.0,
            low=100.0,
            close=104.5,
            volume=10.0, # Low volume breakout
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        await vi.analyze_volume(fake_candle)
        
        # Verify status is REJECT
        status = await vi.check_confirmation("ETHUSD")
        assert status == "REJECT"
        
        # Now try to check allowed order for ETHUSD
        allowed, reason = await employee_engine.check_allowed_order({
            "symbol": "ETHUSD",
            "side": "BUY",
            "quantity": 1.0,
            "price": 100.0
        })
        
        assert allowed is False
        assert "REJECTED" in reason
    finally:
        await employee_engine.stop()

@pytest.mark.asyncio
async def test_volume_intelligence_event_bus_publishing():
    event_bus.start()
    vi = VolumeIntelligenceEmployee()
    await vi.start()
    
    events_received = []
    
    async def cb(event: EventModel):
        events_received.append(event)
        
    await event_bus.subscribe("volume_intelligence_update", cb)
    
    try:
        candle = Candle(
            symbol="TESTVOL",
            timeframe=Timeframe.M1,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.5,
            volume=2000.0,
            complete=True,
            timestamp=datetime.now(timezone.utc)
        )
        await vi.analyze_volume(candle)
        
        # Let async queue process
        await asyncio.sleep(0.1)
        
        assert len(events_received) == 1
        assert events_received[0].event_type == "volume_intelligence_update"
        assert events_received[0].payload["symbol"] == "TESTVOL"
    finally:
        await event_bus.unsubscribe("volume_intelligence_update", cb)
        await vi.stop()
        await event_bus.stop()
