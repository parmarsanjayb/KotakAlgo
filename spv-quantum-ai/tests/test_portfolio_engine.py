import pytest
import asyncio
from datetime import datetime, timezone
from portfolio.models import Position, PositionState, PortfolioSummary
from portfolio.managers import PositionManager, PnLManager, ExposureCalculator
from portfolio.engine import PortfolioEngine
from core.bus import event_bus, EventModel
from brokers.manager import broker_manager

# ── Managers Calculations Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_position_manager_fills():
    pm = PositionManager()
    
    # 1. Open long position: BUY 10 RELIANCE at 2500
    pos, act = await pm.update_on_fill("RELIANCE", "BUY", 10.0, 2500.0)
    assert act == "OPENED"
    assert pos.symbol == "RELIANCE"
    assert pos.quantity == 10.0
    assert pos.avg_price == 2500.0
    assert pos.side == "BUY"
    assert pos.state == PositionState.OPEN
    
    # 2. Add to position: BUY 5 RELIANCE at 2560
    pos, act = await pm.update_on_fill("RELIANCE", "BUY", 5.0, 2560.0)
    assert act == "UPDATED"
    # new avg = (10*2500 + 5*2560) / 15 = (25000 + 12800) / 15 = 2520.0
    assert pos.quantity == 15.0
    assert pos.avg_price == 2520.0
    
    # 3. Partial exit: SELL 5 RELIANCE at 2600 (Realized PNL: (2600-2520)*5 = 400)
    pos, act = await pm.update_on_fill("RELIANCE", "SELL", 5.0, 2600.0)
    assert act == "UPDATED"
    assert pos.quantity == 10.0
    assert pos.realized_pnl == 400.0
    
    # 4. Full close: SELL 10 RELIANCE at 2550 (Realized PNL: (2550-2520)*10 = 300, total = 700)
    pos, act = await pm.update_on_fill("RELIANCE", "SELL", 10.0, 2550.0)
    assert act == "CLOSED"
    assert pos.quantity == 0.0
    assert pos.realized_pnl == 700.0
    assert pos.state == PositionState.CLOSED


def test_pnl_and_exposure_calculators():
    pnl_mgr = PnLManager()
    exp_calc = ExposureCalculator()
    
    p1 = Position(
        symbol="NIFTY50", segment="Futures", side="BUY",
        quantity=50.0, avg_price=24000.0, ltp=24100.0,
        unrealized_pnl=5000.0, realized_pnl=1000.0, state=PositionState.OPEN
    )
    p2 = Position(
        symbol="BTCUSD", segment="Crypto", side="SELL",
        quantity=1.0, avg_price=60000.0, ltp=59000.0,
        unrealized_pnl=1000.0, realized_pnl=-500.0, state=PositionState.OPEN
    )
    
    # PNL checks
    real, unreal, mtm = pnl_mgr.calculate_pnl([p1, p2])
    assert real == 500.0
    assert unreal == 6000.0
    assert mtm == 6500.0
    
    # Exposure checks
    # p1 exposure = 50 * 24100 = 1,205,000
    # p2 exposure = 1 * 59000 = 59,000
    # total exposure = 1,264,000
    tot, seg_dist, sec_dist = exp_calc.calculate_exposure([p1, p2])
    assert tot == 1264000.0
    assert seg_dist["Futures"] == round((1205000 / 1264000) * 100, 2)
    assert sec_dist["Index"] == round((1205000 / 1264000) * 100, 2)


# ── Engine Events Integration Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_engine_integration():
    event_bus.start()
    await broker_manager.load("paper_broker")
    
    engine = PortfolioEngine()
    await engine.start()
    
    # Listen to published events
    pnl_events = []
    async def cb(evt: EventModel):
        pnl_events.append(evt)
        
    await event_bus.subscribe("pnl_updated", cb)
    
    # 1. Mock Order Filled Event
    await event_bus.publish(EventModel(
        source_agent="execution_engine",
        event_type="order_filled",
        payload={
            "symbol": "RELIANCE",
            "side": "BUY",
            "filled_quantity": 10.0,
            "avg_fill_price": 2500.0
        }
    ))
    
    # Wait for processing
    await asyncio.sleep(0.05)
    
    open_pos = await engine.positions.get_open_positions()
    assert len(open_pos) == 1
    assert open_pos[0].symbol == "RELIANCE"
    assert open_pos[0].quantity == 10.0
    
    # 2. Mock Tick Event (LTP update to 2550 -> Unrealized PNL: 500)
    await event_bus.publish(EventModel(
        source_agent="market_data_engine",
        event_type="tick",
        payload={"symbol": "RELIANCE", "ltp": 2550.0}
    ))
    
    # Wait for processing and recalculation
    for _ in range(20):
        if engine.summary.unrealized_pnl == 500.0:
            break
        await asyncio.sleep(0.05)
        
    assert engine.summary.unrealized_pnl == 500.0
    assert engine.summary.mtm == 500.0
    
    # Verify event delivery
    for _ in range(20):
        if len(pnl_events) >= 2:
            break
        await asyncio.sleep(0.05)
        
    assert len(pnl_events) >= 1
    unrealized_vals = [evt.payload["unrealized_pnl"] for evt in pnl_events]
    assert 500.0 in unrealized_vals
    
    await engine.stop()
    await event_bus.unsubscribe("pnl_updated", cb)
    await event_bus.stop()
