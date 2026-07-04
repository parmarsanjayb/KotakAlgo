import pytest
import asyncio
from datetime import datetime, timezone
from risk.models import RiskStatus, RiskResponse
from risk.sizing import PositionSizingEngine
from risk.managers import (
    CapitalManager, DrawdownManager, ExposureManager,
    DailyLossManager, TradeLimitManager, PortfolioRiskManager
)
from risk.engine import RiskEngine
from agents.risk_agent import RiskAgent
from core.bus import event_bus, EventModel
from brokers.manager import broker_manager

# ── Position Sizing Engine Tests ──────────────────────────────────────────────

def test_position_sizing_strategies():
    sizer = PositionSizingEngine(default_strategy="fixed_quantity")
    
    # 1. Fixed Quantity
    assert sizer.calculate_size(strategy="fixed_quantity", params={"quantity": 5.0}) == 5.0
    
    # 2. Fixed Capital
    assert sizer.calculate_size(strategy="fixed_capital", params={"capital": 5000.0}, entry_price=100.0) == 50.0
    
    # 3. Percentage Risk
    assert sizer.calculate_size(strategy="percentage_risk", params={"risk_pct": 1.0, "stop_loss_distance": 10.0}, capital_available=100000.0) == 100.0
    
    # 4. ATR Based
    assert sizer.calculate_size(strategy="atr_based", params={"risk_pct": 1.0, "atr_multiplier": 2.5}, capital_available=100000.0, atr=2.0) == 200.0

    # 5. Volatility Based
    assert sizer.calculate_size(strategy="volatility_based", params={"risk_pct": 1.0}, capital_available=100000.0, entry_price=100.0, volatility=0.02) == 500.0


# ── Sub-Manager Tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drawdown_manager():
    config = {"max_drawdown_percent": 10.0}
    dd_mgr = DrawdownManager(config)
    
    # Check initial
    allowed, dd = await dd_mgr.check_drawdown(100000.0)
    assert allowed is True
    assert dd == 0.0
    
    # Drawdown to 95,000 (5% drawdown) -> allowed
    allowed, dd = await dd_mgr.check_drawdown(95000.0)
    assert allowed is True
    assert dd == 5.0
    
    # Drawdown to 89,000 (11% drawdown) -> blocked
    allowed, dd = await dd_mgr.check_drawdown(89000.0)
    assert allowed is False
    assert dd == 11.0


@pytest.mark.asyncio
async def test_daily_loss_manager():
    config = {"daily_loss_limit_usd": 1000.0, "weekly_loss_limit_usd": 5000.0}
    loss_mgr = DailyLossManager(config)
    
    allowed, reason, d_pnl, w_pnl = await loss_mgr.validate_limits()
    assert allowed is True
    
    # Record daily loss of $600 -> allowed
    await loss_mgr.update_pnl(-600.0)
    allowed, reason, d_pnl, w_pnl = await loss_mgr.validate_limits()
    assert allowed is True
    
    # Record another $500 loss (total $1100) -> blocked
    await loss_mgr.update_pnl(-500.0)
    allowed, reason, d_pnl, w_pnl = await loss_mgr.validate_limits()
    assert allowed is False
    assert "Daily loss limit breached" in reason


@pytest.mark.asyncio
async def test_trade_limit_manager():
    config = {"daily_trade_limit": 3, "max_consecutive_losses": 3, "cooldown_period_minutes": 1.0}
    limit_mgr = TradeLimitManager(config)
    
    # Trade 1: loss
    await limit_mgr.record_trade_execution(-100.0)
    allowed, reason = await limit_mgr.validate_trade_limits()
    assert allowed is True
    
    # Trade 2: loss
    await limit_mgr.record_trade_execution(-50.0)
    allowed, reason = await limit_mgr.validate_trade_limits()
    assert allowed is True
    
    # Trade 3: loss -> triggers consecutive loss limit & cooldown
    await limit_mgr.record_trade_execution(-10.0)
    allowed, reason = await limit_mgr.validate_trade_limits()
    assert allowed is False
    assert "consecutive losses" in reason or "Cooldown" in reason


# ── Risk Engine Pipeline Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_engine_pipeline():
    event_bus.start()
    await broker_manager.load("paper_broker")
    
    # Reset broker mock positions and used margin to clear pollution
    broker = broker_manager.get_active()
    if hasattr(broker, "_positions"):
        broker._positions.clear()
        broker._orders.clear()
        broker._trades.clear()
        broker._balance = broker._initial_balance
        broker._used_margin = 0.0
    
    engine = RiskEngine()
    engine.max_position_size_usd = 5000.0
    await engine.start()
    
    # Test Allow: Small order
    small_order = {"symbol": "BTCUSD", "quantity": 1.0, "price": 100.0}
    resp = await engine.validate_order(small_order)
    assert resp.risk_status == RiskStatus.ALLOW
    assert resp.allowed is True
    
    # Test Reduce Position Size: Order exceeds max cost ($5000 limit, cost = $6000)
    large_order = {"symbol": "BTCUSD", "quantity": 6.0, "price": 1000.0}
    resp = await engine.validate_order(large_order)
    assert resp.risk_status == RiskStatus.REDUCE_POSITION
    assert resp.allowed is True
    assert resp.recommended_position_size == 5.0  # 5000 / 1000
    
    # Test Block: drawdown exceeded simulation.
    # To mock this, we stub get_capital_info to return 80,000 equity (20% drawdown of peak 100,000)
    engine.drawdown_mgr.peak_equity = 100000.0
    
    async def mock_get_capital():
        return {"equity": 80000.0, "available_margin": 80000.0, "used_margin": 0.0}
    engine.capital_mgr.get_capital_info = mock_get_capital
    
    resp = await engine.validate_order(small_order)
    assert resp.risk_status == RiskStatus.BLOCK
    assert resp.allowed is False
    
    await engine.stop()
    await event_bus.stop()


# ── Risk Agent Integration Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_agent_integration():
    event_bus.start()
    await broker_manager.load("paper_broker")
    
    # Reset broker mock positions and used margin to clear pollution
    broker = broker_manager.get_active()
    if hasattr(broker, "_positions"):
        broker._positions.clear()
        broker._orders.clear()
        broker._trades.clear()
        broker._balance = broker._initial_balance
        broker._used_margin = 0.0
    
    agent = RiskAgent()
    await agent.initialize()
    agent.status = "RUNNING"
    
    from risk.engine import risk_engine
    risk_engine.max_position_size_usd = 5000.0
    
    # Test risk_agent evaluates ALLOW
    event = EventModel(
        source_agent="strategy_agent",
        event_type="order_request",
        payload={"symbol": "ETHUSD", "quantity": 1.0, "price": 100.0}
    )
    
    approved_orders = []
    async def cb(evt: EventModel):
        approved_orders.append(evt)
        
    await event_bus.subscribe("order_approved", cb)
    
    result = await agent.analyze(event)
    
    # Wait up to 1 second for background dispatch processing
    for _ in range(20):
        if len(approved_orders) >= 1:
            break
        await asyncio.sleep(0.05)
    
    assert result is not None
    assert result.signal == "BUY"
    assert result.metadata["risk_status"] == RiskStatus.ALLOW.value
    assert len(approved_orders) == 1
    assert approved_orders[0].payload["quantity"] == 1.0

    # Test risk_agent evaluates REDUCE_POSITION
    event_large = EventModel(
        source_agent="strategy_agent",
        event_type="order_request",
        payload={"symbol": "ETHUSD", "quantity": 10.0, "price": 1000.0}
    )
    
    result_large = await agent.analyze(event_large)
    
    for _ in range(20):
        if len(approved_orders) >= 2:
            break
        await asyncio.sleep(0.05)
    
    assert result_large is not None
    assert result_large.signal == "BUY"
    assert result_large.metadata["risk_status"] == RiskStatus.REDUCE_POSITION.value
    assert len(approved_orders) == 2
    assert approved_orders[1].payload["quantity"] == 5.0

    await agent.shutdown()
    await event_bus.unsubscribe("order_approved", cb)
    await event_bus.stop()
