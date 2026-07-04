import pytest
import asyncio
from datetime import datetime, timezone
from journal.models import TradeRecord
from journal.repository import TradeHistoryRepository
from analytics.engine import performance_analytics_engine
from core.bus import event_bus, EventModel

@pytest.mark.asyncio
async def test_performance_analytics_calculation():
    event_bus.start()
    
    # 1. Populate mock trades in repository
    repo = TradeHistoryRepository()
    TradeHistoryRepository._in_memory_journal.clear()
    
    t1 = TradeRecord(
        order_id="ORD-101", symbol="SBIN", segment="Equity",
        entry_price=600.0, exit_price=610.0, quantity=100.0,
        commission=20.0, charges=5.0, taxes=5.0, slippage=10.0,
        realized_pnl=1000.0, holding_duration=3600.0,
        strategy_name="rsi_strategy", timestamp=datetime(2026, 7, 4, 10, 0, 0, tzinfo=timezone.utc)
    )
    t2 = TradeRecord(
        order_id="ORD-102", symbol="SBIN", segment="Equity",
        entry_price=610.0, exit_price=605.0, quantity=100.0,
        commission=20.0, charges=5.0, taxes=5.0, slippage=10.0,
        realized_pnl=-500.0, holding_duration=1800.0,
        strategy_name="rsi_strategy", timestamp=datetime(2026, 7, 4, 11, 0, 0, tzinfo=timezone.utc)
    )
    
    await repo.save_trade_record(t1)
    await repo.save_trade_record(t2)

    # 2. Track published updates on Event Bus
    published = []
    async def cb(evt: EventModel):
        published.append(evt)
        
    await event_bus.subscribe("performance_updated", cb)

    # 3. Recalculate
    metrics = await performance_analytics_engine.recalculate_metrics()
    
    # Assertions
    # Gross Profit = 1000.0
    # Net Profit = (1000 - 40) + (-500 - 40) = 960 - 540 = 420.0
    assert metrics.gross_profit == 1000.0
    assert metrics.net_profit == 420.0
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate == 50.0
    assert metrics.profit_factor == 2.0  # 1000 / 500
    
    # Verify event published
    for _ in range(20):
        if len(published) >= 1:
            break
        await asyncio.sleep(0.05)
        
    assert len(published) >= 1
    assert published[0].payload["metrics"]["net_profit"] == 420.0

    # 4. Generate Report
    report = await performance_analytics_engine.generate_report("Portfolio")
    assert len(report.equity_curve) == 3  # [0.0, 960.0, 420.0]
    assert report.equity_curve[1] == 960.0
    assert report.equity_curve[2] == 420.0
    assert report.drawdown_curve[2] == 540.0  # drawdown at index 2 (peak 960 - current 420)
    
    await event_bus.unsubscribe("performance_updated", cb)
    await event_bus.stop()
