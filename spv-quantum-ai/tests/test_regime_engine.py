import pytest
import asyncio
from datetime import datetime, timezone
from market.models import Timeframe, Candle
from regime.models import MarketRegime, RegimeResult, MarketRegimeEvent
from regime.cache import RegimeCache
from regime.classifier import RegimeClassifier
from regime.publisher import RegimePublisher
from regime.engine import MarketRegimeEngine
from indicators.cache import IndicatorCache
from indicators.models import IndicatorResult
from market.cache import DataCacheManager
from market.models import MarketData
from core.bus import event_bus, EventModel


# ── RegimeCache ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regime_cache_store_retrieve():
    cache = RegimeCache()
    r1 = RegimeResult(symbol="BTCUSD", timeframe=Timeframe.M5,
                      market_regime=MarketRegime.SIDEWAYS,
                      confidence=55.0, reason="Test")
    r2 = RegimeResult(symbol="BTCUSD", timeframe=Timeframe.M5,
                      market_regime=MarketRegime.TRENDING_BULLISH,
                      confidence=72.0, reason="ADX high")
    await cache.store(r1)
    await cache.store(r2)

    latest = await cache.get_latest("BTCUSD", Timeframe.M5)
    assert latest.market_regime == MarketRegime.TRENDING_BULLISH

    prev = await cache.get_previous("BTCUSD", Timeframe.M5)
    assert prev.market_regime == MarketRegime.SIDEWAYS

@pytest.mark.asyncio
async def test_regime_cache_change_detection():
    cache = RegimeCache()
    r1 = RegimeResult(symbol="NIFTY50", timeframe=Timeframe.M15,
                      market_regime=MarketRegime.SIDEWAYS, confidence=50.0, reason="")
    r2 = RegimeResult(symbol="NIFTY50", timeframe=Timeframe.M15,
                      market_regime=MarketRegime.BREAKOUT, confidence=72.0, reason="")
    await cache.store(r1)
    assert await cache.has_regime_changed("NIFTY50", Timeframe.M15) is False
    await cache.store(r2)
    assert await cache.has_regime_changed("NIFTY50", Timeframe.M15) is True

@pytest.mark.asyncio
async def test_regime_cache_no_change():
    cache = RegimeCache()
    r = RegimeResult(symbol="ETHUSD", timeframe=Timeframe.H1,
                     market_regime=MarketRegime.TRENDING_BEARISH, confidence=65.0, reason="")
    await cache.store(r)
    await cache.store(r)
    assert await cache.has_regime_changed("ETHUSD", Timeframe.H1) is False


# ── RegimeClassifier ──────────────────────────────────────────────────────────

def test_classifier_gap_up():
    clf = RegimeClassifier()
    r = clf.classify("BTCUSD", Timeframe.M5, {
        "ltp": 65330.0, "prev_close": 65000.0,  # +0.5%
        "atr": 50.0, "atr_avg": 50.0,
        "adx": 20.0, "di_pos": 18.0, "di_neg": 14.0,
        "ema_9": 65200.0, "ema_20": 65100.0, "ema_50": 65000.0,
        "bb_bw": 2.0, "vwap": 65150.0,
    })
    assert r.market_regime == MarketRegime.GAP_UP
    assert r.confidence >= 75.0

def test_classifier_gap_down():
    clf = RegimeClassifier()
    r = clf.classify("BTCUSD", Timeframe.M5, {
        "ltp": 64670.0, "prev_close": 65000.0,  # -0.51%
        "atr": 50.0, "atr_avg": 50.0,
        "adx": 20.0, "di_pos": 14.0, "di_neg": 18.0,
        "ema_9": 64700.0, "ema_20": 64800.0, "ema_50": 64900.0,
        "bb_bw": 2.0, "vwap": 64800.0,
    })
    assert r.market_regime == MarketRegime.GAP_DOWN

def test_classifier_news_driven():
    clf = RegimeClassifier()
    r = clf.classify("NIFTY50", Timeframe.M1, {
        "ltp": 24500.0, "prev_close": 24490.0,  # gap ~0.04% – below threshold
        "atr": 200.0, "atr_avg": 60.0,          # 3.3x avg ATR
        "volume": 900000.0, "vol_avg": 200000.0, # 4.5x avg volume
        "adx": 30.0, "di_pos": 25.0, "di_neg": 15.0,
        "ema_9": 24400.0, "ema_20": 24300.0, "ema_50": 24000.0,
        "bb_bw": 6.0, "vwap": 24350.0,
        "session_high": 24600.0, "session_low": 24100.0,
    })
    assert r.market_regime == MarketRegime.NEWS_DRIVEN
    assert r.confidence >= 80.0

def test_classifier_high_volatility():
    clf = RegimeClassifier()
    r = clf.classify("ETHUSD", Timeframe.M15, {
        "ltp": 3500.0, "prev_close": 3490.0,
        "atr": 120.0, "atr_avg": 70.0,          # 1.7x → high vol
        "adx": 22.0, "di_pos": 18.0, "di_neg": 16.0,
        "ema_9": 3498.0, "ema_20": 3495.0, "ema_50": 3490.0,
        "bb_bw": 4.0, "vwap": 3496.0,
        "session_high": 3520.0, "session_low": 3480.0,
    })
    assert r.market_regime == MarketRegime.HIGH_VOLATILITY

def test_classifier_low_volatility():
    clf = RegimeClassifier()
    r = clf.classify("ETHUSD", Timeframe.M15, {
        "ltp": 3500.0, "prev_close": 3499.0,
        "atr": 10.0, "atr_avg": 70.0,            # 0.14x → low vol
        "adx": 10.0, "di_pos": 12.0, "di_neg": 11.0,
        "ema_9": 3500.0, "ema_20": 3500.0, "ema_50": 3500.0,
        "bb_bw": 1.0, "vwap": 3500.0,
        "session_high": 3505.0, "session_low": 3495.0,
    })
    assert r.market_regime == MarketRegime.LOW_VOLATILITY

def test_classifier_trending_bullish():
    clf = RegimeClassifier()
    r = clf.classify("BTCUSD", Timeframe.H1, {
        "ltp": 68000.0, "prev_close": 67900.0,
        "atr": 300.0, "atr_avg": 300.0,
        "adx": 35.0, "di_pos": 30.0, "di_neg": 15.0,
        "ema_9": 67800.0, "ema_20": 67500.0, "ema_50": 67000.0,
        "bb_bw": 3.0, "vwap": 67700.0,
        "session_high": 68100.0, "session_low": 67200.0,
    })
    assert r.market_regime == MarketRegime.TRENDING_BULLISH
    assert r.confidence > 55.0

def test_classifier_trending_bearish():
    clf = RegimeClassifier()
    r = clf.classify("BTCUSD", Timeframe.H1, {
        "ltp": 63000.0, "prev_close": 63100.0,
        "atr": 300.0, "atr_avg": 300.0,
        "adx": 38.0, "di_pos": 12.0, "di_neg": 32.0,
        "ema_9": 63200.0, "ema_20": 63500.0, "ema_50": 64000.0,
        "bb_bw": 3.5, "vwap": 63400.0,
        "session_high": 63800.0, "session_low": 62900.0,
    })
    assert r.market_regime == MarketRegime.TRENDING_BEARISH

def test_classifier_range_bound():
    clf = RegimeClassifier()
    r = clf.classify("NIFTY50", Timeframe.M30, {
        "ltp": 24200.0, "prev_close": 24210.0,
        "atr": 30.0, "atr_avg": 30.0,
        "adx": 14.0, "di_pos": 15.0, "di_neg": 14.0,
        "ema_9": 24205.0, "ema_20": 24200.0, "ema_50": 24195.0,
        "bb_bw": 1.2, "vwap": 24202.0,
        "session_high": 24240.0, "session_low": 24160.0,
        "momentum": 0.02,
    })
    assert r.market_regime in (MarketRegime.RANGE_BOUND, MarketRegime.SIDEWAYS)

def test_classifier_breakout():
    clf = RegimeClassifier()
    r = clf.classify("NIFTY50", Timeframe.M5, {
        "ltp": 24500.0, "prev_close": 24498.0,  # gap ~0.008% – below threshold
        "atr": 60.0, "atr_avg": 55.0,
        "adx": 22.0, "di_pos": 20.0, "di_neg": 14.0,
        "ema_9": 24490.0, "ema_20": 24450.0, "ema_50": 24300.0,
        "bb_bw": 6.5, "bb_upper": 24510.0, "bb_lower": 24200.0,
        "vwap": 24400.0,
        "session_high": 24502.0, "session_low": 24200.0,
    })
    assert r.market_regime == MarketRegime.BREAKOUT

def test_regime_result_confidence_clamp():
    clf = RegimeClassifier()
    r = clf.classify("BTCUSD", Timeframe.D1, {})
    assert 0.0 <= r.confidence <= 100.0

def test_all_regimes_have_string_values():
    for regime in MarketRegime:
        assert isinstance(regime.value, str)
        assert len(regime.value) > 0


# ── RegimePublisher ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publisher_emits_event():
    event_bus.start()
    received = []

    async def spy(evt: EventModel):
        received.append(evt)

    await event_bus.subscribe("market_regime", spy)

    cache     = RegimeCache()
    publisher = RegimePublisher(cache)
    result    = RegimeResult(
        symbol="BTCUSD", timeframe=Timeframe.M1,
        market_regime=MarketRegime.TRENDING_BULLISH,
        confidence=70.0, reason="Test publish"
    )
    await publisher.publish(result)
    
    for _ in range(20):
        if len(received) >= 1:
            break
        await asyncio.sleep(0.05)

    assert len(received) == 1
    payload = received[0].payload
    assert payload["market_regime"] == MarketRegime.TRENDING_BULLISH.value
    assert payload["confidence"]    == 70.0

    await event_bus.unsubscribe("market_regime", spy)
    await event_bus.stop()


# ── MarketRegimeEngine end-to-end ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regime_engine_processes_candle():
    event_bus.start()
    regime_events = []

    async def spy(evt: EventModel):
        if evt.event_type == "market_regime":
            regime_events.append(evt.payload)

    await event_bus.subscribe("market_regime", spy)

    ind_cache = IndicatorCache()
    mkt_cache = DataCacheManager()

    # Pre-fill indicator cache with values for BTCUSD / M1
    sym = "BTCUSD"; tf = Timeframe.M1
    for name, val in [("ATR", 250.0), ("EMA_9", 65100.0),
                      ("EMA_20", 65050.0), ("EMA_50", 65000.0),
                      ("MOMENTUM", 5.0)]:
        await ind_cache.store(IndicatorResult(
            indicator_name=name, symbol=sym, timeframe=tf, value=val
        ))
    await ind_cache.store(IndicatorResult(
                indicator_name="ADX", symbol=sym, timeframe=tf,
        value={"adx": 32.0, "di_pos": 26.0, "di_neg": 14.0}
    ))
    await ind_cache.store(IndicatorResult(
        indicator_name="BOLLINGER", symbol=sym, timeframe=tf,
        value={"upper": 65300.0, "middle": 65100.0, "lower": 64900.0, "bandwidth": 2.5}
    ))

    tick = MarketData(symbol=sym, ltp=65100.0, prev_close=64800.0,
                      vwap=65050.0, volume=500.0, bid=65098.0, ask=65102.0)
    await mkt_cache.update_tick(tick)

    engine = MarketRegimeEngine(ind_cache, mkt_cache)
    await engine.start()

    # Publish a completed candle event
    candle = Candle(
        symbol=sym, timeframe=tf,
        timestamp=datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc),
        open=65000.0, high=65200.0, low=64950.0, close=65100.0,
        volume=500.0, complete=True,
    )
    await event_bus.publish(EventModel(
        event_type="candle", source_agent="test",
        payload={"candle": candle.model_dump()},
    ))
    
    for _ in range(20):
        if len(regime_events) >= 1:
            break
        await asyncio.sleep(0.05)

    assert len(regime_events) >= 1
    ev = regime_events[0]
    assert "market_regime" in ev
    assert "confidence"    in ev
    assert "reason"        in ev
    assert 0.0 <= ev["confidence"] <= 100.0

    # Verify cache updated
    cached = await engine.cache.get_latest(sym, tf)
    assert cached is not None
    assert isinstance(cached.market_regime, MarketRegime)

    await engine.stop()
    await event_bus.unsubscribe("market_regime", spy)
    await event_bus.stop()


@pytest.mark.asyncio
async def test_regime_engine_classify_now():
    event_bus.start()
    ind_cache = IndicatorCache()
    mkt_cache = DataCacheManager()

    tick = MarketData(symbol="NIFTY50", ltp=24200.0, prev_close=24000.0,
                      vwap=24150.0, volume=1000.0, bid=24198.0, ask=24202.0)
    await mkt_cache.update_tick(tick)

    engine = MarketRegimeEngine(ind_cache, mkt_cache)
    await engine.start()

    result = await engine.classify_now("NIFTY50", Timeframe.M5)
    assert result is not None
    assert isinstance(result.market_regime, MarketRegime)
    assert result.symbol == "NIFTY50"

    await engine.stop()
    await event_bus.stop()
