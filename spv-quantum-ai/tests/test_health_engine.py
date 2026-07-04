import pytest
import asyncio
import time
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from health.models import ServiceStatus
from health import system_health_engine

@pytest.mark.asyncio
async def test_system_health_engine_lifecycle():
    await system_health_engine.start()
    assert system_health_engine._running is True
    
    # Check that system metrics can be fetched successfully
    metrics = await system_health_engine.get_dashboard_metrics()
    assert metrics["overall_system_health"] in ("HEALTHY", "DEGRADED", "FAILED")
    assert "cpu_usage_pct" in metrics
    assert "memory_usage_pct" in metrics
    assert "latency" in metrics
    
    await system_health_engine.stop()
    assert system_health_engine._running is False

def test_heartbeat_monitoring():
    h_mgr = system_health_engine.manager.heartbeat_mgr
    h_mgr.record_heartbeat("Execution Engine")
    
    # Check that execution engine is marked healthy
    statuses = h_mgr.check_heartbeats(timeout_sec=5.0)
    assert statuses["Execution Engine"] == ServiceStatus.HEALTHY
    
    # Simulate a stale heartbeat
    h_mgr.last_heartbeats["Execution Engine"] = time.time() - 20.0
    statuses = h_mgr.check_heartbeats(timeout_sec=5.0)
    assert statuses["Execution Engine"] == ServiceStatus.FAILED

@pytest.mark.asyncio
async def test_alert_manager_thresholds():
    alert_mgr = system_health_engine.manager.alert_mgr
    
    event_bus.start()
    warnings = []
    async def capture_warning(evt: EventModel):
        warnings.append(evt)
        
    await event_bus.subscribe("system_warning", capture_warning)
    
    try:
        # Trigger CPU warning
        await alert_mgr.check_thresholds(
            metrics={"cpu_usage_pct": 90.0, "memory_usage_pct": 10.0},
            queue_metrics={"event_bus_queue_size": 1, "execution_queue_size": 0}
        )
        await asyncio.sleep(0.05)
        
        assert len(warnings) == 1
        assert warnings[0].payload["metric"] == "cpu_usage_pct"
        assert warnings[0].payload["value"] == 90.0
        
    finally:
        await event_bus.unsubscribe("system_warning", capture_warning)
        await event_bus.stop()

@pytest.mark.asyncio
async def test_recovery_manager_trigger():
    event_bus.start()
    recoveries = []
    async def capture_recovery(evt: EventModel):
        recoveries.append(evt)
        
    await event_bus.subscribe("recovery_started", capture_recovery)
    await event_bus.subscribe("recovery_completed", capture_recovery)
    
    try:
        success = await system_health_engine.manager.recovery.attempt_recovery(
            "Execution Engine", "Heartbeat timeout"
        )
        assert success is True
        
        await asyncio.sleep(0.05)
        assert len(recoveries) == 2
        assert recoveries[0].event_type == "recovery_started"
        assert recoveries[1].event_type == "recovery_completed"
        
    finally:
        await event_bus.unsubscribe("recovery_started", capture_recovery)
        await event_bus.unsubscribe("recovery_completed", capture_recovery)
        await event_bus.stop()

def test_logging_manager_rotation(tmp_path):
    # Use temporary directory for testing log manager file creations
    from health.logging import LoggingManager
    log_mgr = LoggingManager(log_dir=str(tmp_path))
    
    log_mgr.log("trading", "Order executed", level="INFO", order_id="ORD123", price=250.0)
    
    log_file = tmp_path / "trading.log"
    assert log_file.exists()
    
    content = log_file.read_text(encoding="utf-8")
    assert "Order executed" in content
    assert "ORD123" in content
