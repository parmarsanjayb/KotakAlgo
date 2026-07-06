import pytest
import asyncio
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from market.models import Timeframe, Candle
from employees.models import EmployeeState, EmployeeType
from employees import employee_engine

@pytest.fixture(autouse=True)
def reset_employee_engine():
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()
    yield
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()

@pytest.mark.asyncio
async def test_new_specialists_initialization_and_lifecycle():
    await employee_engine.start()
    
    try:
        # Give heartbeat loops a fraction of a second to run
        await asyncio.sleep(0.2)
        
        # Verify that all new employees are loaded and registered
        expected_codes = [
            "EMP-MOM", "EMP-VWP", "EMP-RGM", "EMP-OIE", "EMP-PCR", "EMP-GRK", "EMP-MPN",
            "EMP-SME", "EMP-LQD", "EMP-OFL", "EMP-DEL", "EMP-RSK", "EMP-PZS", "EMP-CPT",
            "EMP-EXP", "EMP-NWS", "EMP-CAL", "EMP-EVR", "EMP-EXE", "EMP-PTF", "EMP-PPR",
            "EMP-OPT", "EMP-EQI", "EMP-EQS", "EMP-COM", "EMP-CUR", "EMP-PM"
        ]
        
        for code in expected_codes:
            profile = employee_engine.manager.get_profile(code)
            assert profile is not None, f"Employee {code} not registered"
            assert profile.is_active is True, f"Employee {code} is not active"
            assert profile.health_status == "HEALTHY", f"Employee {code} is not healthy"
            
    finally:
        await employee_engine.stop()

@pytest.mark.asyncio
async def test_momentum_employee_analysis():
    event_bus.start()
    # Verify MomentumEmployee calculates RSI and reacts
    await employee_engine.start()
    try:
        mom_emp = employee_engine.momentum
        
        # Push 20 candles
        for i in range(20):
            candle = Candle(
                symbol="NIFTY50",
                timeframe=Timeframe.M1,
                open=100.0,
                high=102.0,
                low=98.0,
                close=100.0 + (i * 2.0), # Rising RSI
                volume=1000.0,
                complete=True,
                timestamp=datetime.now(timezone.utc)
            )
            await event_bus.publish(EventModel(
                event_type="candle",
                source_agent="market_data_engine",
                payload=candle.model_dump()
            ))
            
        await asyncio.sleep(0.05)
        res = mom_emp.latest_results.get("NIFTY50")
        assert res is not None
        assert res["rsi"] > 50.0
    finally:
        await employee_engine.stop()
        await event_bus.stop()

@pytest.mark.asyncio
async def test_vwap_employee_analysis():
    event_bus.start()
    await employee_engine.start()
    try:
        vwp_emp = employee_engine.vwap_emp
        
        # Publish some candles
        for i in range(5):
            candle = Candle(
                symbol="NIFTY50",
                timeframe=Timeframe.M1,
                open=100.0,
                high=105.0,
                low=95.0,
                close=102.0,
                volume=1000.0,
                complete=True,
                timestamp=datetime.now(timezone.utc)
            )
            await event_bus.publish(EventModel(
                event_type="candle",
                source_agent="market_data_engine",
                payload=candle.model_dump()
            ))
            
        await asyncio.sleep(0.05)
        res = vwp_emp.latest_results.get("NIFTY50")
        assert res is not None
        assert res["vwap"] > 0.0
    finally:
        await employee_engine.stop()
        await event_bus.stop()
