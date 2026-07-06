import pytest
import asyncio
from datetime import datetime, timezone
from agents.chief_decision_agent import ChiefDecisionAgent, ApprovalManager, ConflictResolver
from core.bus import event_bus, EventModel
from portfolio.engine import portfolio_engine

# ── Component Validation Tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_manager_checks():
    mgr = ApprovalManager()
    mgr.reset_daily_trades()
    
    # Clean portfolio state
    async with portfolio_engine.positions._lock:
        portfolio_engine.positions._positions.clear()
    portfolio_engine.summary.available_capital = 50000.0
    
    # 1. Successful check
    payload = {
        "symbol": "TCS",
        "overall_confidence": 75.0,
        "risk_status": "ALLOW"
    }
    state, code, desc = await mgr.validate_checks(payload)
    assert state == "APPROVED"
    assert code == "SUCCESS"
    
    # 2. Risk block
    payload_risk = {
        "symbol": "TCS",
        "overall_confidence": 75.0,
        "risk_status": "BLOCK"
    }
    state, code, desc = await mgr.validate_checks(payload_risk)
    assert state == "BLOCKED"
    assert code == "RISK_REJECTION"
    
    # 3. Low confidence
    payload_conf = {
        "symbol": "TCS",
        "overall_confidence": 30.0,
        "risk_status": "ALLOW"
    }
    state, code, desc = await mgr.validate_checks(payload_conf)
    assert state == "REJECTED"
    assert code == "CONFIDENCE_TOO_LOW"


def test_conflict_resolver():
    resolver = ConflictResolver()
    assert resolver.resolve(85.0, "ALLOW") == "APPROVED"
    assert resolver.resolve(90.0, "BLOCK") == "REJECTED"
    assert resolver.resolve(40.0, "ALLOW") == "REJECTED"
    assert resolver.resolve(60.0, "ALLOW") == "MANUAL_REVIEW"


# ── Integration Tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chief_decision_agent_integration():
    event_bus.start()
    
    agent = ChiefDecisionAgent()
    await agent.start()
    
    # Clear queues
    agent.approved_queue.clear()
    agent.rejected_queue.clear()
    agent.blocked_queue.clear()
    agent.coordinator.reset_daily_trades()
    
    # Setup mock margin
    portfolio_engine.summary.available_capital = 100000.0
    async with portfolio_engine.positions._lock:
        portfolio_engine.positions._positions.clear()

    # Mock employee engine state for RELIANCE
    from employees import employee_engine
    employee_engine.trend_intelligence.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.volume_intelligence.latest_results["RELIANCE"] = {"confirmation_status": "CONFIRM", "confidence": 80.0}
    employee_engine.risk_emp.latest_results["SYSTEM"] = {"recommendation": "BUY", "confidence": 90.0}

    employee_engine.vwap_emp.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.momentum.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.liquidity.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.oi_emp.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.pcr_emp.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.greeks.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}
    employee_engine.option_flow.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 80.0}

    employee_engine.news_emp.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 70.0}
    employee_engine.calendar.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 70.0}
    employee_engine.event_risk.latest_results["RELIANCE"] = {"recommendation": "BUY", "confidence": 70.0}

    # Track events
    approved_events = []
    async def cb(evt: EventModel):
        approved_events.append(evt)
        
    await event_bus.subscribe("trade_approved", cb)

    # Publish decision score event
    await event_bus.publish(EventModel(
        source_agent="decision_scoring_engine",
        event_type="decision_score",
        payload={
            "symbol": "RELIANCE",
            "overall_confidence": 85.0,
            "risk_status": "ALLOW",
            "recommended_strategy": "ema_crossover",
            "side": "BUY",
            "quantity": 10.0,
            "price": 2500.0
        }
    ))

    # Wait for processing
    for _ in range(20):
        if len(agent.approved_queue) >= 1:
            break
        await asyncio.sleep(0.05)

    assert len(agent.approved_queue) == 1
    assert agent.approved_queue[0]["symbol"] == "RELIANCE"
    assert agent.approved_queue[0]["status"] == "APPROVED"
    
    # Verify published event on bus
    for _ in range(20):
        if len(approved_events) >= 1:
            break
        await asyncio.sleep(0.05)
        
    assert len(approved_events) == 1
    assert approved_events[0].payload["symbol"] == "RELIANCE"
    assert approved_events[0].payload["status"] == "APPROVED"
    
    await agent.stop()
    await event_bus.unsubscribe("trade_approved", cb)
    await event_bus.stop()


def test_chief_decision_agent_ceo_logic():
    agent = ChiefDecisionAgent()
    from employees import employee_engine
    
    # Test case 1: Mandatory Trend Fail
    employee_engine.trend_intelligence.latest_results["TCS"] = {"recommendation": "SELL", "confidence": 80.0}  # Sell trend on Buy request
    employee_engine.volume_intelligence.latest_results["TCS"] = {"confirmation_status": "CONFIRM", "confidence": 80.0}
    employee_engine.risk_emp.latest_results["SYSTEM"] = {"recommendation": "BUY", "confidence": 90.0}

    decision = agent._evaluate_ceo_decision("TCS", "BUY", "ALLOW")
    assert decision["mandatory_passed"] is False
    assert "Trend Employee reject" in decision["reason"]

    # Test case 2: Weighted calculations
    # Trend, Volume, Risk pass
    employee_engine.trend_intelligence.latest_results["TCS"] = {"recommendation": "BUY", "confidence": 90.0}
    employee_engine.volume_intelligence.latest_results["TCS"] = {"confirmation_status": "CONFIRM", "confidence": 80.0}
    employee_engine.risk_emp.latest_results["SYSTEM"] = {"recommendation": "BUY", "confidence": 90.0}

    # All weighted employees recommend BUY
    for emp in [employee_engine.vwap_emp, employee_engine.momentum, employee_engine.liquidity, 
                employee_engine.oi_emp, employee_engine.pcr_emp, employee_engine.greeks, employee_engine.option_flow]:
        emp.latest_results["TCS"] = {"recommendation": "BUY", "confidence": 80.0}

    # Advisory employees
    employee_engine.news_emp.latest_results["TCS"] = {"recommendation": "BUY", "confidence": 70.0}
    employee_engine.calendar.latest_results["TCS"] = {"recommendation": "BUY", "confidence": 70.0}
    employee_engine.event_risk.latest_results["TCS"] = {"recommendation": "BUY", "confidence": 70.0}

    decision2 = agent._evaluate_ceo_decision("TCS", "BUY", "ALLOW")
    assert decision2["mandatory_passed"] is True
    # Weighted score = sum(weight * 80.0) = 80.0. News/Calendar add +10%. Clamped at 90.0% confidence.
    assert decision2["confidence"] == 90.0
    assert decision2["risk"] == "LOW"
    assert decision2["consensus"] == 100.0  # 10/10 employees agree

