import pytest
from core.bus import EventModel
from core.agent import AgentResultModel
from agents.registry import agent_registry
from agents.market_intelligence_agent import (
    MarketIntelligenceAgent,
    calculate_sma,
    calculate_rsi,
    calculate_macd
)

def test_registry_has_market_intelligence() -> None:
    """Verifies that the scanning registry discovers the market intelligence agent."""
    discovered = agent_registry.discover_agents()
    assert "market_intelligence_agent" in discovered

def test_indicator_math_utilities() -> None:
    """Verifies moving averages and indicators return mathematically accurate outputs."""
    prices = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0]
    
    # SMA 5 of last 5 elements: [20, 22, 24, 26, 28] = 120 / 5 = 24.0
    sma = calculate_sma(prices, 5)
    assert sma == 24.0

    # RSI flatline
    rsi = calculate_rsi([100.0] * 10, 14)
    assert rsi == 50.0

@pytest.mark.asyncio
async def test_intelligence_agent_analysis() -> None:
    """Verifies agent processes stream candles, aggregates close prices, and returns trend signals."""
    agent = MarketIntelligenceAgent()
    await agent.initialize()
    agent.status = "RUNNING"

    # Push 30 candles to trigger indicators (which require slow period of 26 bars)
    for i in range(30):
        event = EventModel(
            source_agent="market_feed",
            event_type="tick",
            payload={
                "tick": {
                    "symbol": "BTCUSD",
                    "ltp": 60000.0 + i,
                    "volume": 12.5,
                    "bid": 59999.0 + i,
                    "ask": 60001.0 + i,
                    "vwap": 60000.0 + i,
                }
            }
        )
        
        result = await agent.analyze(event)
        
        # Verify signals are emitted once history spans calculation length
        if i >= 26:
            assert result is not None
            assert isinstance(result, AgentResultModel)
            assert result.agent_name == "market_intelligence_agent"
            assert result.signal in ["BUY", "SELL", "HOLD"]
            assert result.confidence > 0.0
            assert result.metadata["history_length"] == i + 1

    await agent.shutdown()
