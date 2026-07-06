import pytest
import asyncio
from datetime import datetime, timezone
from market.models import OptionChain, OptionContract, OptionGreeks
from employees.option_flow import OptionFlowIntelligenceEmployee
from employees.engine import employee_engine
from core.bus import event_bus, EventModel

def create_mock_contract(strike: float, option_type: str, volume: float, oi: float, ltp: float = 100.0) -> OptionContract:
    return OptionContract(
        strike=strike,
        option_type=option_type,
        ltp=ltp,
        bid=ltp - 0.5,
        ask=ltp + 0.5,
        volume=volume,
        open_interest=oi,
        greeks=OptionGreeks(delta=0.5, gamma=0.05, theta=-0.1, vega=0.2, iv=25.0)
    )

@pytest.mark.asyncio
async def test_option_flow_employee_initialization():
    of = OptionFlowIntelligenceEmployee()
    assert of._running is False
    assert len(of.latest_results) == 0

@pytest.mark.asyncio
async def test_option_flow_employee_lifecycle():
    of = OptionFlowIntelligenceEmployee()
    await of.start()
    assert of._running is True
    await of.stop()
    assert of._running is False

@pytest.mark.asyncio
async def test_option_flow_analysis_pcr_bullish():
    of = OptionFlowIntelligenceEmployee()
    
    # 11 strikes around ATM=10000
    contracts = []
    for strike in range(9500, 10600, 100):
        # Bullish: More PE volume and OI than CE
        contracts.append(create_mock_contract(float(strike), "CE", volume=100.0, oi=100.0, ltp=10.0))
        contracts.append(create_mock_contract(float(strike), "PE", volume=300.0, oi=300.0, ltp=10.0))
        
    chain = OptionChain(
        underlying="NIFTY50",
        underlying_price=10000.0,
        expiry="2026-07-30",
        contracts=contracts,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await of.analyze_option_chain(chain)
    assert res["underlying"] == "NIFTY50"
    assert res["atm_strike"] == 10000.0
    assert res["pcr"] == 3.0 # 3300 / 1100
    assert res["option_flow_score"] > 50.0
    assert res["classification"] == "BULLISH"
    assert res["recommendation"] == "BUY CE"

@pytest.mark.asyncio
async def test_option_flow_analysis_bearish():
    of = OptionFlowIntelligenceEmployee()
    
    # Bearish: More CE volume and OI than PE
    contracts = []
    for strike in range(9500, 10600, 100):
        contracts.append(create_mock_contract(float(strike), "CE", volume=300.0, oi=300.0, ltp=10.0))
        contracts.append(create_mock_contract(float(strike), "PE", volume=50.0, oi=50.0, ltp=10.0))
        
    chain = OptionChain(
        underlying="NIFTY50",
        underlying_price=10000.0,
        expiry="2026-07-30",
        contracts=contracts,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await of.analyze_option_chain(chain)
    assert res["pcr"] == round(550.0 / 3300.0, 4)
    assert res["option_flow_score"] < 50.0
    assert res["classification"] == "BEARISH"
    assert res["recommendation"] == "BUY PE"

@pytest.mark.asyncio
async def test_option_flow_trap_detection():
    of = OptionFlowIntelligenceEmployee()
    
    # Trap: PCR is high (> 2.0) but Option Flow Score is low
    # We simulate this by setting PE OI high but CE volume very high and PE volume very low (creating downward bias in flow score)
    contracts = []
    for strike in range(9500, 10600, 100):
        # CE: volume 1000, OI 50
        contracts.append(create_mock_contract(float(strike), "CE", volume=1000.0, oi=50.0, ltp=10.0))
        # PE: volume 50, OI 500
        contracts.append(create_mock_contract(float(strike), "PE", volume=50.0, oi=500.0, ltp=10.0))
        
    chain = OptionChain(
        underlying="NIFTY50",
        underlying_price=10000.0,
        expiry="2026-07-30",
        contracts=contracts,
        timestamp=datetime.now(timezone.utc)
    )
    
    res = await of.analyze_option_chain(chain)
    assert res["pcr"] == 10.0 # PCR = 5500 / 550 = 10.0
    assert res["classification"] == "TRAP"
    assert res["recommendation"] == "NO_TRADE"

@pytest.mark.asyncio
async def test_option_flow_event_bus_publishing():
    event_bus.start()
    of = OptionFlowIntelligenceEmployee()
    await of.start()
    
    events_received = []
    
    async def cb(event: EventModel):
        events_received.append(event)
        
    await event_bus.subscribe("option_flow_updated", cb)
    await event_bus.subscribe("option_flow_signal", cb)
    
    try:
        contracts = []
        for strike in range(9500, 10600, 100):
            contracts.append(create_mock_contract(float(strike), "CE", volume=10.0, oi=10.0, ltp=10.0))
            contracts.append(create_mock_contract(float(strike), "PE", volume=500.0, oi=500.0, ltp=10.0))
            
        chain = OptionChain(
            underlying="PUBLISH_TEST",
            underlying_price=10000.0,
            expiry="2026-07-30",
            contracts=contracts,
            timestamp=datetime.now(timezone.utc)
        )
        await of.analyze_option_chain(chain)
        
        # Let queue process
        await asyncio.sleep(0.1)
        
        # Should receive option_flow_updated and option_flow_signal (since recommendation is BUY CE)
        topics = [e.event_type for e in events_received]
        assert "option_flow_updated" in topics
        assert "option_flow_signal" in topics
    finally:
        await event_bus.unsubscribe("option_flow_updated", cb)
        await event_bus.unsubscribe("option_flow_signal", cb)
        await of.stop()
        await event_bus.stop()
