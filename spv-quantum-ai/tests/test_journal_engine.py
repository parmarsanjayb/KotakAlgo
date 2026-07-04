import pytest
import asyncio
from datetime import datetime, timezone
from journal.models import TradeRecord, DecisionAudit
from journal.repository import TradeHistoryRepository
from journal.engine import TradeJournalEngine
from core.bus import event_bus, EventModel
from database.connection import init_db

# ── Repository Tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_repository_crud():
    repo = TradeHistoryRepository()
    TradeHistoryRepository._in_memory_journal.clear()
    
    trade = TradeRecord(
        order_id="ORD-100",
        symbol="RELIANCE",
        entry_price=2500.0,
        quantity=10.0,
        strategy_name="trend_strategy"
    )
    
    entry_id = await repo.save_trade_record(trade)
    assert entry_id > 0
    
    # Retrieve
    trades = await repo.get_all_trades()
    assert len(trades) >= 1
    saved = [t for t in trades if t.trade_id == trade.trade_id]
    assert len(saved) == 1
    assert saved[0].symbol == "RELIANCE"
    assert saved[0].entry_price == 2500.0
    
    # Update exit
    trade.exit_price = 2550.0
    trade.realized_pnl = 500.0
    await repo.update_trade_record(trade)
    
    trades_updated = await repo.get_all_trades()
    saved_up = [t for t in trades_updated if t.trade_id == trade.trade_id]
    assert saved_up[0].exit_price == 2550.0
    assert saved_up[0].realized_pnl == 500.0


@pytest.mark.asyncio
async def test_journal_repository_filters():
    repo = TradeHistoryRepository()
    TradeHistoryRepository._in_memory_journal.clear()
    
    t1 = TradeRecord(
        order_id="ORD-200", symbol="BTCUSD", segment="Crypto",
        entry_price=60000.0, quantity=1.0, strategy_name="trend_strategy", realized_pnl=100.0
    )
    t2 = TradeRecord(
        order_id="ORD-201", symbol="NIFTY50", segment="Futures",
        entry_price=24000.0, quantity=50.0, strategy_name="reversal_strategy", realized_pnl=-50.0
    )
    
    await repo.save_trade_record(t1)
    await repo.save_trade_record(t2)
    
    # Filter by segment
    res = await repo.search_trades({"segment": "Futures"})
    assert len(res) >= 1
    assert all(t.segment == "Futures" for t in res)
    
    # Filter by strategy
    res2 = await repo.search_trades({"strategy": "trend_strategy"})
    assert len(res2) >= 1
    assert all(t.strategy_name == "trend_strategy" for t in res2)
    
    # Filter by pnl threshold
    res3 = await repo.search_trades({"pnl_min": 0.0})
    assert len(res3) >= 1
    assert all(t.realized_pnl >= 0.0 for t in res3)


# ── Engine Events Integration Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_journal_engine_integration():
    event_bus.start()
    TradeHistoryRepository._in_memory_journal.clear()
    
    engine = TradeJournalEngine()
    await engine.start()
    
    journal_updates = []
    async def cb(evt: EventModel):
        journal_updates.append(evt)
        
    await event_bus.subscribe("journal_updated", cb)
    
    # 1. Simulate entry order filled
    await event_bus.publish(EventModel(
        source_agent="execution_engine",
        event_type="order_filled",
        payload={
            "symbol": "TCS",
            "side": "BUY",
            "filled_quantity": 5.0,
            "avg_fill_price": 4000.0,
            "order_id": "ORD-TCS-ENTRY",
            "strategy_name": "trend_strategy"
        }
    ))
    
    # Wait for engine loop
    await asyncio.sleep(0.05)
    
    # Verify active entry tracked
    assert "TCS" in engine._active_trades
    assert engine._active_trades["TCS"].quantity == 5.0
    
    # 2. Simulate exit order filled (different side)
    await event_bus.publish(EventModel(
        source_agent="execution_engine",
        event_type="order_filled",
        payload={
            "symbol": "TCS",
            "side": "SELL",
            "filled_quantity": 5.0,
            "avg_fill_price": 4100.0,
            "order_id": "ORD-TCS-EXIT"
        }
    ))
    
    # Wait for processing
    await asyncio.sleep(0.05)
    
    # Verify active entry popped
    assert "TCS" not in engine._active_trades
    
    # 3. Simulate decision score audit
    await event_bus.publish(EventModel(
        source_agent="decision_scoring_engine",
        event_type="decision_score",
        payload={
            "symbol": "TCS",
            "overall_confidence": 85.0,
            "reasoning": "Strong indicators breakout",
            "recommended_strategy": "trend_strategy",
            "risk_status": "ALLOW"
        }
    ))
    
    # Verify journal events published on Event Bus
    for _ in range(20):
        if len(journal_updates) >= 3:
            break
        await asyncio.sleep(0.05)
        
    assert len(journal_updates) >= 3
    types = {evt.payload["entry_type"] for evt in journal_updates}
    assert "trade_record" in types
    assert "decision_audit" in types
    
    # Check stats
    stats = await engine.get_performance_stats()
    assert stats["total_trades"] >= 1
    
    await engine.stop()
    await event_bus.unsubscribe("journal_updated", cb)
    await event_bus.stop()
