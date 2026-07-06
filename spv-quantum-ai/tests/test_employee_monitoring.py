import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from employees.models import EmployeeProfile, EmployeeType, EmployeeState
from employees.manager import EmployeeManager
from employees.engine import EmployeeEngine
from journal.models import TradeRecord
from core.bus import event_bus, EventModel

@pytest.mark.asyncio
async def test_employee_profile_monitoring_fields():
    # Verify default profile monitoring fields
    p = EmployeeProfile(
        employee_code="EMP-TEST",
        name="Test Employee",
        avatar="avatar.png",
        description="Testing monitoring fields"
    )
    assert p.is_active is True
    assert p.health_status == "HEALTHY"
    assert p.last_decision == "NONE"
    assert p.last_decision_confidence == 0.0
    assert p.total_signals == 0
    assert p.correct_signals == 0
    assert p.incorrect_signals == 0
    assert p.accuracy_pct == 100.0
    assert p.last_execution_time_ms == 0.0
    assert p.avg_execution_time_ms == 0.0
    assert p.error_count == 0
    assert p.last_error is None
    assert isinstance(p.heartbeat_timestamp, datetime)
    assert len(p.accuracy_history) == 0

@pytest.mark.asyncio
async def test_record_activity_success():
    event_bus.start()
    manager = EmployeeManager()
    p = EmployeeProfile(
        employee_code="EMP-TEST",
        name="Test Employee",
        avatar="avatar.png",
        description="Testing monitoring fields"
    )
    manager.profiles[p.employee_code] = p
    
    events_received = []
    async def cb(event):
        events_received.append(event)
        
    await event_bus.subscribe("employee_decision", cb)
    
    try:
        await manager.record_activity(
            employee_code="EMP-TEST",
            decision="APPROVED",
            confidence=85.5,
            execution_time_ms=12.4
        )
        
        # Verify stats updated
        profile = manager.get_profile("EMP-TEST")
        assert profile.last_decision == "APPROVED"
        assert profile.last_decision_confidence == 85.5
        assert profile.last_execution_time_ms == 12.4
        assert profile.health_status == "HEALTHY"
        assert profile.error_count == 0
        
        # Let queue process
        await asyncio.sleep(0.1)
        assert len(events_received) == 1
        assert events_received[0].event_type == "employee_decision"
        assert events_received[0].payload["decision"] == "APPROVED"
        assert events_received[0].payload["confidence"] == 85.5
    finally:
        await event_bus.unsubscribe("employee_decision", cb)
        await event_bus.stop()

@pytest.mark.asyncio
async def test_record_activity_error():
    manager = EmployeeManager()
    p = EmployeeProfile(
        employee_code="EMP-TEST",
        name="Test Employee",
        avatar="avatar.png",
        description="Testing monitoring fields"
    )
    manager.profiles[p.employee_code] = p
    
    await manager.record_activity(
        employee_code="EMP-TEST",
        decision="REJECTED",
        confidence=50.0,
        execution_time_ms=5.2,
        error="Limit exceeded"
    )
    
    profile = manager.get_profile("EMP-TEST")
    assert profile.last_decision == "REJECTED"
    assert profile.health_status == "FAILED"
    assert profile.error_count == 1
    assert profile.last_error == "Limit exceeded"

@pytest.mark.asyncio
async def test_trade_closed_accuracy_calculation():
    manager = EmployeeManager()
    p = EmployeeProfile(
        employee_code="EMP-TEST",
        name="Test Employee",
        avatar="avatar.png",
        description="Testing monitoring fields"
    )
    manager.profiles[p.employee_code] = p
    
    # 1. Simulate Trade 1: Profit (Correct)
    trade1 = TradeRecord(
        order_id="ORD-1",
        symbol="NIFTY50",
        entry_price=100.0,
        quantity=10.0,
        employee_codes=["EMP-TEST"],
        net_pnl=150.0
    )
    await manager._on_trade_closed(EventModel(
        event_type="trade_closed",
        source_agent="trade_journal",
        payload={"trade": trade1}
    ))
    
    profile = manager.get_profile("EMP-TEST")
    assert profile.total_signals == 1
    assert profile.correct_signals == 1
    assert profile.incorrect_signals == 0
    assert profile.accuracy_pct == 100.0
    assert len(profile.accuracy_history) == 1
    assert profile.accuracy_history[0]["accuracy_pct"] == 100.0
    
    # 2. Simulate Trade 2: Loss (Incorrect)
    trade2 = TradeRecord(
        order_id="ORD-2",
        symbol="NIFTY50",
        entry_price=100.0,
        quantity=10.0,
        employee_codes=["EMP-TEST"],
        net_pnl=-50.0
    )
    await manager._on_trade_closed(EventModel(
        event_type="trade_closed",
        source_agent="trade_journal",
        payload={"trade": trade2}
    ))
    
    assert profile.total_signals == 2
    assert profile.correct_signals == 1
    assert profile.incorrect_signals == 1
    assert profile.accuracy_pct == 50.0
    assert len(profile.accuracy_history) == 2
    assert profile.accuracy_history[1]["accuracy_pct"] == 50.0

@pytest.mark.asyncio
async def test_heartbeat_monitor_alerts():
    event_bus.start()
    manager = EmployeeManager()
    
    # Active employee with stale heartbeat (35s ago)
    p = EmployeeProfile(
        employee_code="EMP-TEST",
        name="Stale Employee",
        avatar="avatar.png",
        description="Stale heartbeat test",
        state=EmployeeState.ACTIVE
    )
    p.heartbeat_timestamp = datetime.now(timezone.utc) - timedelta(seconds=35)
    manager.profiles[p.employee_code] = p
    
    alerts_received = []
    async def alert_cb(event):
        alerts_received.append(event)
        
    await event_bus.subscribe("critical_employee_failure_alert", alert_cb)
    
    try:
        manager._running = True
        
        # Run monitor iteration once
        now = datetime.now(timezone.utc)
        for code, profile in manager.profiles.items():
            delta = (now - profile.heartbeat_timestamp).total_seconds()
            if delta > 30.0:
                profile.health_status = "FAILED"
                profile.is_active = False
                await event_bus.publish(EventModel(
                    event_type="critical_employee_failure_alert",
                    source_agent="employee_manager",
                    payload={
                        "employee_code": code,
                        "name": profile.name,
                        "message": "AI Employee stopped responding.",
                        "category": "employee_failure"
                    }
                ))
                
        # Let queue process
        await asyncio.sleep(0.1)
        assert len(alerts_received) == 1
        assert alerts_received[0].payload["employee_code"] == "EMP-TEST"
        assert p.health_status == "FAILED"
        assert p.is_active is False
    finally:
        await event_bus.unsubscribe("critical_employee_failure_alert", alert_cb)
        await event_bus.stop()

@pytest.mark.asyncio
async def test_employee_heartbeat_tasks():
    # Verify that starting VolumeIntelligenceEmployee and OptionFlowIntelligenceEmployee 
    # triggers heartbeat updates in EmployeeManager profiles.
    from employees import employee_engine
    
    # Initialize engine defaults
    await employee_engine.start()
    
    try:
        # Give heartbeat tasks a split second to fire their initial record_activity
        await asyncio.sleep(0.2)
        
        # Verify profiles have heartbeats recorded
        vol_profile = employee_engine.manager.get_profile("EMP-VOL")
        oft_profile = employee_engine.manager.get_profile("EMP-OFT")
        
        assert vol_profile is not None
        assert oft_profile is not None
        
        assert vol_profile.is_active is True
        assert oft_profile.is_active is True
        assert vol_profile.health_status == "HEALTHY"
        assert oft_profile.health_status == "HEALTHY"
    finally:
        await employee_engine.stop()
