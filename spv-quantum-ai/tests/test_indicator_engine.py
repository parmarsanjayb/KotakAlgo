import pytest
import asyncio
from datetime import datetime, timezone
from market.models import Timeframe, Candle
from indicators.math import (
    calc_sma, calc_ema, calc_rsi, calc_macd, calc_atr, calc_adx,
    calc_vwap, calc_supertrend, calc_bollinger, calc_stoch_rsi,
    calc_cci, calc_roc, calc_momentum, calc_pivot_points,
    calc_support_resistance,
)
from indicators.models import IndicatorResult
from indicators.cache import IndicatorCache
from indicators.registry import INDICATOR_REGISTRY, is_registered
from indicators.publisher import IndicatorPublisher
from indicators.manager import IndicatorManager
from indicators.engine import IndicatorEngine
from core.bus import event_bus, EventModel


# ── Math functions ─────────────────────────────────────────────────────────────

def test_sma_accuracy():
    prices = [10.0, 12.0, 14.0, 16.0, 18.0]
    assert calc_sma(prices, 3) == pytest.approx(16.0)
    assert calc_sma(prices, 5) == pytest.approx(14.0)

def test_ema_converges():
    prices = [100.0] * 50 + [200.0]
    ema = calc_ema(prices, 9)
    assert ema > 100.0   # must have moved toward 200

def test_rsi_midpoint():
    # Flat prices → avg_loss == 0 → RS = inf → RSI = 100 (correct per Wilder formula)
    assert calc_rsi([100.0] * 20, 14) == pytest.approx(100.0)

def test_rsi_near_50_alternating():
    # Alternating +1/-1 → equal avg_gain and avg_loss → RSI ≈ 50
    prices = []
    p = 100.0
    for i in range(30):
        p += 1.0 if i % 2 == 0 else -1.0
        prices.append(p)
    rsi = calc_rsi(prices, 14)
    assert 40.0 <= rsi <= 60.0


def test_rsi_overbought():
    # Strongly trending up → RSI near 100
    prices = list(range(1, 30))
    rsi = calc_rsi(prices, 14)
    assert rsi > 80.0

def test_macd_returns_tuple():
    prices = [float(i) + 100 for i in range(50)]
    ml, sl, h = calc_macd(prices)
    assert isinstance(ml, float)
    assert isinstance(sl, float)
    assert isinstance(h, float)

def test_atr_positive():
    h = [105.0 + i for i in range(20)]
    l = [95.0  + i for i in range(20)]
    c = [100.0 + i for i in range(20)]
    atr = calc_atr(h, l, c, 14)
    assert atr >= 0.0

def test_bollinger_bands_spread():
    prices = [100.0] * 10 + [110.0] * 10
    u, m, lo, bw = calc_bollinger(prices, 20, 2.0)
    assert u > m > lo
    assert bw > 0.0

def test_vwap_uniform():
    # Uniform price → VWAP == price
    h = [100.0] * 10; l = [100.0] * 10; c = [100.0] * 10; v = [10.0] * 10
    assert calc_vwap(h, l, c, v) == pytest.approx(100.0)

def test_supertrend_returns_direction():
    prices = [float(100 + i) for i in range(20)]
    st, direction = calc_supertrend(prices, prices, prices)
    assert direction in (1, -1)

def test_cci_zero_on_flat():
    h = l = c = [100.0] * 25
    # Flat price → CCI = 0 (no deviation from mean)
    assert calc_cci(h, l, c, 20) == pytest.approx(0.0)

def test_roc_positive():
    prices = [100.0, 110.0] + [110.0] * 11
    roc = calc_roc(prices, 12)
    # price rose from 100 to 110 over 12 bars → ROC = 10%
    assert roc == pytest.approx(10.0)

def test_momentum_positive():
    prices = [90.0] + [100.0] * 10
    m = calc_momentum(prices, 10)
    assert m == pytest.approx(10.0)

def test_pivot_points_order():
    p, r1, r2, r3, s1, s2, s3 = calc_pivot_points(110.0, 90.0, 100.0)
    assert r3 > r2 > r1 > p > s1 > s2 > s3

def test_support_resistance():
    highs = [float(100 + i) for i in range(25)]
    lows  = [float(90  - i) for i in range(25)]
    res, sup = calc_support_resistance(highs, lows, 20)
    assert res > sup


# ── IndicatorCache ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_indicator_cache_store_and_retrieve():
    cache = IndicatorCache()
    r1 = IndicatorResult(indicator_name="RSI", symbol="BTCUSD",
                         timeframe=Timeframe.M1, value=55.0)
    r2 = IndicatorResult(indicator_name="RSI", symbol="BTCUSD",
                         timeframe=Timeframe.M1, value=60.0)
    await cache.store(r1)
    await cache.store(r2)

    latest = await cache.get_latest("BTCUSD", Timeframe.M1, "RSI")
    assert latest.value == 60.0

    prev = await cache.get_previous("BTCUSD", Timeframe.M1, "RSI")
    assert prev.value == 55.0


@pytest.mark.asyncio
async def test_crossover_detection():
    cache = IndicatorCache()
    # EMA_9 crosses above EMA_20 (golden)
    ema9_prev  = IndicatorResult(indicator_name="EMA_9",  symbol="NIFTY50", timeframe=Timeframe.M5, value=100.0)
    ema20_prev = IndicatorResult(indicator_name="EMA_20", symbol="NIFTY50", timeframe=Timeframe.M5, value=105.0)
    ema9_curr  = IndicatorResult(indicator_name="EMA_9",  symbol="NIFTY50", timeframe=Timeframe.M5, value=110.0)
    ema20_curr = IndicatorResult(indicator_name="EMA_20", symbol="NIFTY50", timeframe=Timeframe.M5, value=108.0)

    await cache.store(ema9_prev);  await cache.store(ema20_prev)
    await cache.store(ema9_curr);  await cache.store(ema20_curr)

    xo = await cache.detect_crossover("NIFTY50", Timeframe.M5, "EMA_9", "EMA_20")
    assert xo == "GOLDEN"


# ── IndicatorRegistry ──────────────────────────────────────────────────────────

def test_all_required_indicators_registered():
    required = [
        "EMA_9", "EMA_20", "EMA_50", "EMA_100", "EMA_200",
        "SMA_20", "SMA_50", "RSI", "MACD", "ATR", "ADX", "VWAP",
        "SUPERTREND", "BOLLINGER", "STOCH_RSI", "CCI", "ROC",
        "MOMENTUM", "PIVOT_POINTS", "S_R"
    ]
    for name in required:
        assert is_registered(name), f"Missing from registry: {name}"


# ── IndicatorManager full-stack ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_indicator_manager_processes_candle():
    event_bus.start()
    published = []

    async def spy(evt: EventModel):
        if evt.event_type == "indicator_update":
            published.append(evt.payload)

    await event_bus.subscribe("indicator_update", spy)

    cache     = IndicatorCache()
    publisher = IndicatorPublisher(cache)
    manager   = IndicatorManager(publisher)

    # Feed 30 closed candles to have enough history for all indicators
    for i in range(30):
        c = Candle(
            symbol="BTCUSD", timeframe=Timeframe.M1,
            timestamp=datetime(2026, 7, 4, 9, i, 0, tzinfo=timezone.utc),
            open=65000.0 + i, high=65010.0 + i,
            low=64990.0 + i, close=65000.0 + i,
            volume=10.0, complete=True,
        )
        await manager.on_candle(c)

    await asyncio.sleep(0.05)

    # Verify multiple indicator events published
    names = {e["indicator_name"] for e in published}
    assert "RSI"        in names
    assert "EMA_9"      in names
    assert "MACD"       in names
    assert "BOLLINGER"  in names
    assert "SUPERTREND" in names
    assert "PIVOT_POINTS" in names

    await event_bus.unsubscribe("indicator_update", spy)
    await event_bus.stop()


# ── IndicatorEngine subscription ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_indicator_engine_start_stop():
    event_bus.start()
    engine = IndicatorEngine()
    await engine.start()
    assert engine._running is True

    # Publish a synthetic CandleEvent (complete=True) with 30 bars of history
    for i in range(30):
        c = Candle(
            symbol="ETHUSD", timeframe=Timeframe.M5,
            timestamp=datetime(2026, 7, 4, 9, i, 0, tzinfo=timezone.utc),
            open=3500.0 + i, high=3510.0 + i,
            low=3490.0 + i,  close=3500.0 + i,
            volume=5.0, complete=True,
        )
        await event_bus.publish(EventModel(
            event_type   = "candle",
            source_agent = "test",
            payload      = {"candle": c.model_dump()},
        ))

    await asyncio.sleep(0.1)

    # Verify cache has RSI for ETHUSD/1m
    rsi_result = await engine.cache.get_latest("ETHUSD", Timeframe.M5, "RSI")
    assert rsi_result is not None
    assert 0.0 <= rsi_result.value <= 100.0

    await engine.stop()
    assert engine._running is False
    await event_bus.stop()
