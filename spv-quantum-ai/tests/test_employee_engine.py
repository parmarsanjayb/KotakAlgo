import pytest
import asyncio
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from employees.models import EmployeeState, EmployeeType
from employees import employee_engine
from employees.registry import EmployeeRegistry
from safety import safety_engine

@pytest.fixture(autouse=True)
def reset_employee_engine():
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()
    yield
    employee_engine.manager.active_code = None
    employee_engine.manager.profiles.clear()

@pytest.mark.asyncio
async def test_employee_registry_and_initialization():
    await employee_engine.start()
    assert employee_engine._running is True
    
    # Check default template retrieval
    opt_specialist = EmployeeRegistry.get_default_profile(
        EmployeeType.OPTIONS_SPECIALIST, "EMP-OPT", "Options Guru"
    )
    assert opt_specialist.employee_code == "EMP-OPT"
    assert opt_specialist.employee_type == EmployeeType.OPTIONS_SPECIALIST
    assert "Options" in opt_specialist.allowed_segments
    
    await employee_engine.stop()
    assert employee_engine._running is False

@pytest.mark.asyncio
async def test_employee_permission_check():
    # Setup test employee
    emp_eq = EmployeeRegistry.get_default_profile(
        EmployeeType.EQUITY_INTRADAY, "EMP-EQ", "Equity Guy"
    )
    await employee_engine.manager.register_employee(emp_eq)
    
    # 1. Allowed order checks
    order_ok = {"symbol": "INFY", "segment": "Equity", "product": "MIS", "employee_code": "EMP-EQ"}
    allowed, reason = await employee_engine.check_allowed_order(order_ok)
    assert allowed is True
    
    # 2. Blocked segment checks
    order_blocked_segment = {"symbol": "NIFTY-OPT", "segment": "Options", "product": "MIS", "employee_code": "EMP-EQ"}
    allowed, reason = await employee_engine.check_allowed_order(order_blocked_segment)
    assert allowed is False
    assert "Segment" in reason

    # 3. Blocked product checks
    order_blocked_product = {"symbol": "INFY", "segment": "Equity", "product": "CNC", "employee_code": "EMP-EQ"}
    allowed, reason = await employee_engine.check_allowed_order(order_blocked_product)
    assert allowed is False
    assert "Product" in reason

@pytest.mark.asyncio
async def test_employee_policy_updates():
    emp_opt = EmployeeRegistry.get_default_profile(
        EmployeeType.OPTIONS_SPECIALIST, "EMP-OPT", "Options Guru"
    )
    emp_opt.max_exposure = 99999.0
    emp_opt.max_daily_loss = 777.0
    await employee_engine.manager.register_employee(emp_opt)
    
    # Activate and check that limits are propagated to Safety Engine config
    success = await employee_engine.activate_employee("EMP-OPT")
    assert success is True
    
    assert safety_engine.config["max_exposure_usd"] == 99999.0
    assert safety_engine.config["daily_loss_guard_usd"] == 777.0

@pytest.mark.asyncio
async def test_employee_performance_calculations():
    event_bus.start()
    
    # Setup test employee
    emp_eq = EmployeeRegistry.get_default_profile(
        EmployeeType.EQUITY_SWING, "EMP-SWING", "Swing Guy"
    )
    emp_eq.trade_history.clear()
    emp_eq.trade_count = 0
    emp_eq.pnl = 0.0
    await employee_engine.manager.register_employee(emp_eq)
    
    # Set as active
    employee_engine.manager.active_code = "EMP-SWING"
    await employee_engine.start()
    
    try:
        # Simulate fill event with positive PnL
        fill_event = EventModel(
            event_type="order_filled",
            source_agent="execution_engine",
            payload={
                "symbol": "TCS",
                "side": "BUY",
                "quantity": 10.0,
                "price": 3200.0,
                "pnl": 500.0,
                "strategy_name": "TrendFollowing"
            }
        )
        await event_bus.publish(fill_event)
        await asyncio.sleep(0.05)
        
        profile = employee_engine.manager.get_profile("EMP-SWING")
        assert profile.trade_count == 1
        assert profile.pnl == 500.0
        assert profile.win_rate == 100.0
        assert profile.consecutive_wins == 1
        assert profile.strategy_pnl["TrendFollowing"] == 500.0
        
    finally:
        await employee_engine.stop()
        await event_bus.stop()
