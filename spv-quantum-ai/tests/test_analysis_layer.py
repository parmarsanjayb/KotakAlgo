import pytest
import asyncio
from datetime import datetime, timezone
from market.models import Timeframe, Candle
from analysis.models import MarketAnalysisReport, MarketAnalysisEvent
from analysis.cache import AnalysisCache
from analysis.publisher import AnalysisPublisher
from analysis.engine import MarketAnalysisEngine
from agents.market_analyst_agent import MarketAnalystAgent
from core.bus import event_bus, EventModel

# ── Cache & Publisher Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analysis_cache_operations():
    cache = AnalysisCache()
    report = MarketAnalysisReport(
        symbol="BTCUSD",
        timeframe="1m",
        market_bias="BULLISH",
        trend_strength="STRONG",
        momentum="BULLISH",
        volatility="HIGH",
        market_structure="TRENDING_BULLISH",
        support=65000.0,
        resistance=66000.0,
        recommended_strategy="sample_golden_cross",
        confidence=90.0,
        reasoning="Test reasoning"
    )
    
    await cache.store(report)
    
    # Retrieve
    stored = await cache.get_latest("BTCUSD", "1m")
    assert stored is not None
    assert stored.market_bias == "BULLISH"
    assert stored.support == 65000.0
    
    all_latest = await cache.get_all_latest()
    assert len(all_latest) == 1
    assert ("BTCUSD", "1m") in all_latest


@pytest.mark.asyncio
async def test_analysis_publisher_emits_event():
    event_bus.start()
    
    cache = AnalysisCache()
    publisher = AnalysisPublisher(cache)
    
    report = MarketAnalysisReport(
        symbol="NIFTY50",
        timeframe="5m",
        market_bias="NEUTRAL",
        trend_strength="NONE",
        momentum="FLAT",
        volatility="LOW",
        market_structure="SIDEWAYS",
        support=24000.0,
        resistance=24300.0,
        recommended_strategy="Wait & Watch",
        confidence=50.0,
        reasoning="No active signals"
    )
    
    events_received = []
    async def cb(evt: EventModel):
        events_received.append(evt)
        
    await event_bus.subscribe("market_analysis", cb)
    
    await publisher.publish(report)
    await asyncio.sleep(0.05)
    
    assert len(events_received) == 1
    assert events_received[0].event_type == "market_analysis"
    assert events_received[0].payload["report"]["symbol"] == "NIFTY50"
    
    await event_bus.unsubscribe("market_analysis", cb)
    await event_bus.stop()


# ── Engine & Agent Tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_market_analysis_engine_heuristics():
    # Setup caches with mock states
    from indicators.engine import indicator_engine
    from indicators.models import IndicatorResult
    from regime.engine import regime_engine
    from regime.models import RegimeResult, MarketRegime
    from market.manager import market_data_manager
    from market.models import MarketData
    
    symbol = "ETHUSD"
    tf = Timeframe.M1
    
    # Mock Indicators
    await indicator_engine.cache.store(IndicatorResult(
        indicator_name="RSI", symbol=symbol, timeframe=tf, value=65.0 # Bullish momentum
    ))
    await indicator_engine.cache.store(IndicatorResult(
        indicator_name="ADX", symbol=symbol, timeframe=tf, value={"adx": 30.0, "di_pos": 28.0, "di_neg": 15.0} # Strong trend
    ))
    await indicator_engine.cache.store(IndicatorResult(
        indicator_name="ATR", symbol=symbol, timeframe=tf, value=60.0 # Volatility check
    ))
    await indicator_engine.cache.store(IndicatorResult(
        indicator_name="S_R", symbol=symbol, timeframe=tf, value={"support": 3400.0, "resistance": 3600.0}
    ))
    
    # Mock Regime
    await regime_engine.cache.store(RegimeResult(
        symbol=symbol, timeframe=tf, market_regime=MarketRegime.TRENDING_BULLISH, confidence=80.0, reason="EMA cross"
    ))
    
    # Mock Market Data
    tick = MarketData(symbol=symbol, ltp=3500.0, prev_close=3480.0, volume=1000.0)
    await market_data_manager.cache.update_tick(tick)
    
    engine = MarketAnalysisEngine()
    report = await engine.analyze_market(symbol, tf)
    
    assert report.symbol == symbol
    assert report.market_bias == "BULLISH"
    assert report.trend_strength == "STRONG"
    assert report.momentum == "BULLISH"
    assert report.support == 3400.0
    assert report.resistance == 3600.0


@pytest.mark.asyncio
async def test_market_analyst_agent_trigger():
    event_bus.start()
    
    agent = MarketAnalystAgent()
    await agent.initialize()
    agent.status = "RUNNING"
    
    candle = Candle(
        symbol="BTCUSD",
        timeframe=Timeframe.M1,
        timestamp=datetime.now(timezone.utc),
        open=65000.0, high=65100.0, low=64950.0, close=65050.0,
        volume=10.0,
        complete=True
    )
    
    event = EventModel(
        source_agent="market_data_engine",
        event_type="candle",
        payload={"candle": candle.model_dump()}
    )
    
    result = await agent.analyze(event)
    assert result is not None
    assert result.agent_name == "market_analyst_agent"
    assert "market_bias" in result.metadata
    assert result.metadata["symbol"] == "BTCUSD"
    
    await agent.shutdown()
    await event_bus.stop()
