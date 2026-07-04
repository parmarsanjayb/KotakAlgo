import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from backtest.models import BacktestConfig, BacktestProgress
from backtest.engine import backtesting_engine
from core.bus import event_bus, EventModel

# ── Loader Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_historical_data_loader():
    from backtest.loader import HistoricalDataLoader
    loader = HistoricalDataLoader()
    
    start = datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 4, 9, 10, 0, tzinfo=timezone.utc)
    
    candles = await loader.load_candles("NIFTY50", "1m", start, end)
    assert len(candles) >= 1
    assert candles[0].symbol == "NIFTY50"
    assert candles[0].complete is True


# ── Engine E2E Simulation Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backtesting_engine_simulation():
    event_bus.start()
    
    # 1. Configure backtest
    start = datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 4, 9, 5, 0, tzinfo=timezone.utc)
    config = BacktestConfig(
        symbols=["BTCUSD"],
        timeframe="1m",
        start_date=start,
        end_date=end,
        initial_capital=100000.0
    )
    
    # Track backtest events
    backtest_events = []
    async def cb(evt: EventModel):
        backtest_events.append(evt)
        
    await event_bus.subscribe("backtest_completed", cb)
    
    # 2. Run simulation
    await backtesting_engine.start()
    backtest_id = await backtesting_engine.run_backtest(config)
    assert backtest_id.startswith("BKT-")
    
    # Wait for completion
    for _ in range(50):
        status_dict = await backtesting_engine.get_dashboard_status()
        if status_dict["status"] in ("COMPLETED", "FAILED"):
            break
        await asyncio.sleep(0.1)
        
    status_dict = await backtesting_engine.get_dashboard_status()
    assert status_dict["status"] == "COMPLETED"
    assert status_dict["progress_pct"] == 100.0
    
    # Verify event delivery
    for _ in range(20):
        if len(backtest_events) >= 1:
            break
        await asyncio.sleep(0.05)
        
    assert len(backtest_events) == 1
    assert backtest_events[0].payload["backtest_id"] == backtest_id
    assert "metrics" in backtest_events[0].payload
    
    await backtesting_engine.stop()
    await event_bus.unsubscribe("backtest_completed", cb)
    await event_bus.stop()
