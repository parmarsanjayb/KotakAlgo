"""
RegimeClassifier – pure classification logic.
Consumes indicator values and market data snapshots.
MUST NOT generate BUY/SELL/ENTRY/EXIT signals.
MUST NOT access brokers or database.
"""
from typing import Any, Dict, Optional, Tuple
from regime.models import MarketRegime, RegimeResult
from market.models import Timeframe


# ── Thresholds (tunable via config in future) ─────────────────────────────────
ADX_TREND_THRESH   = 25.0   # ADX above → trending
ADX_STRONG_THRESH  = 40.0   # ADX above → strong trend
ATR_HIGH_MULT      = 1.5    # ATR > mean * mult → high volatility
ATR_LOW_MULT       = 0.5    # ATR < mean * mult → low volatility
BB_SQUEEZE_PCT     = 1.5    # Bollinger bandwidth < → range/squeeze
BB_EXPAND_PCT      = 5.0    # Bollinger bandwidth > → volatile/breakout
GAP_THRESH_PCT     = 0.5    # Price vs prev_close gap % threshold
MOMENTUM_NEUTRAL   = 0.05   # Momentum near zero → sideways


class RegimeClassifier:
    """
    Stateless classifier. Accepts a feature dict and returns a RegimeResult.
    Classification priority:
      1. Gap Up / Gap Down  (session-open condition)
      2. News Driven        (extremely high ATR + volume spike)
      3. High / Low Volatility (ATR-based)
      4. Breakout / Breakdown (price vs session range + BB)
      5. Reversal           (ADX falling + price crossing MA)
      6. Trending Bullish / Bearish (ADX + EMAs)
      7. Sideways / Range Bound
    """

    def classify(self, symbol: str, timeframe: Timeframe, features: Dict[str, Any]) -> RegimeResult:
        regime, confidence, reason, factors = self._classify(features)
        return RegimeResult(
            symbol             = symbol,
            timeframe          = timeframe,
            market_regime      = regime,
            confidence         = min(100.0, max(0.0, confidence)),
            reason             = reason,
            supporting_factors = factors,
        )

    def _classify(
        self, f: Dict[str, Any]
    ) -> Tuple[MarketRegime, float, str, Dict[str, Any]]:
        ltp        = f.get("ltp",         0.0)
        prev_close = f.get("prev_close",  0.0)
        atr        = f.get("atr",         0.0)
        atr_avg    = f.get("atr_avg",     atr)
        adx        = f.get("adx",         0.0)
        di_pos     = f.get("di_pos",      0.0)
        di_neg     = f.get("di_neg",      0.0)
        vwap       = f.get("vwap",        ltp)
        ema_9      = f.get("ema_9",       ltp)
        ema_20     = f.get("ema_20",      ltp)
        ema_50     = f.get("ema_50",      ltp)
        bb_upper   = f.get("bb_upper",    ltp)
        bb_lower   = f.get("bb_lower",    ltp)
        bb_bw      = f.get("bb_bw",       0.0)
        volume     = f.get("volume",      0.0)
        vol_avg    = f.get("vol_avg",     volume)
        session_h  = f.get("session_high", ltp)
        session_l  = f.get("session_low",  ltp)
        momentum   = f.get("momentum",    0.0)

        factors: Dict[str, Any] = {
            "ltp": ltp, "adx": adx, "atr": atr,
            "di_pos": di_pos, "di_neg": di_neg,
            "ema_9": ema_9, "ema_20": ema_20, "ema_50": ema_50,
            "vwap": vwap, "bb_bw": bb_bw, "momentum": momentum,
        }

        # ── 1. Gap conditions ────────────────────────────────────────────────
        if prev_close > 0:
            gap_pct = (ltp - prev_close) / prev_close * 100
            if gap_pct >= GAP_THRESH_PCT:
                return (MarketRegime.GAP_UP, 75.0 + min(gap_pct * 2, 20.0),
                        f"Gap up {gap_pct:.2f}% vs previous close",
                        {**factors, "gap_pct": round(gap_pct, 4)})
            if gap_pct <= -GAP_THRESH_PCT:
                return (MarketRegime.GAP_DOWN, 75.0 + min(abs(gap_pct) * 2, 20.0),
                        f"Gap down {gap_pct:.2f}% vs previous close",
                        {**factors, "gap_pct": round(gap_pct, 4)})

        # ── 2. News-Driven (extreme ATR + volume spike) ──────────────────────
        atr_spike    = (atr > atr_avg * 2.5) if atr_avg else False
        volume_spike = (volume > vol_avg * 3.0) if vol_avg else False
        if atr_spike and volume_spike:
            return (MarketRegime.NEWS_DRIVEN, 85.0,
                    "Extreme ATR and volume spike – likely news-driven move",
                    {**factors, "atr_spike": True, "volume_spike": True})

        # ── 3. Volatility classification ─────────────────────────────────────
        if atr_avg and atr > atr_avg * ATR_HIGH_MULT:
            return (MarketRegime.HIGH_VOLATILITY,
                    60.0 + min((atr / atr_avg - ATR_HIGH_MULT) * 20, 30.0),
                    f"ATR {atr:.2f} is {atr/atr_avg:.1f}x average – elevated volatility",
                    factors)

        if atr_avg and atr < atr_avg * ATR_LOW_MULT:
            return (MarketRegime.LOW_VOLATILITY,
                    60.0 + min((ATR_LOW_MULT - atr / atr_avg) * 30, 30.0),
                    f"ATR {atr:.2f} is {atr/atr_avg:.1f}x average – compressed volatility",
                    factors)

        # ── 4. Breakout / Breakdown ─────────────────────────────────────────
        if bb_bw > BB_EXPAND_PCT and ltp > session_h * 0.998 and ema_9 > ema_20:
            return (MarketRegime.BREAKOUT, 72.0,
                    "Price at session high with expanding Bollinger Bands",
                    factors)

        if bb_bw > BB_EXPAND_PCT and ltp < session_l * 1.002 and ema_9 < ema_20:
            return (MarketRegime.BREAKDOWN, 72.0,
                    "Price at session low with expanding Bollinger Bands",
                    factors)

        # ── 5. Reversal ──────────────────────────────────────────────────────
        was_trending = adx > ADX_TREND_THRESH
        price_cross  = (ema_9 > ema_20) != (ema_20 > ema_50)
        if was_trending and price_cross and adx < ADX_TREND_THRESH + 5:
            return (MarketRegime.REVERSAL, 60.0,
                    "ADX near threshold with EMA cross – potential reversal",
                    factors)

        # ── 6. Trending Bullish / Bearish ────────────────────────────────────
        if adx > ADX_TREND_THRESH:
            bull = di_pos > di_neg and ema_9 > ema_20 > ema_50 and ltp > vwap
            bear = di_neg > di_pos and ema_9 < ema_20 < ema_50 and ltp < vwap
            conf = 55.0 + min((adx - ADX_TREND_THRESH) * 1.0, 35.0)
            if bull:
                return (MarketRegime.TRENDING_BULLISH, conf,
                        f"ADX={adx:.1f} DI+>{di_neg:.1f} EMAs aligned bullish above VWAP",
                        factors)
            if bear:
                return (MarketRegime.TRENDING_BEARISH, conf,
                        f"ADX={adx:.1f} DI->{di_pos:.1f} EMAs aligned bearish below VWAP",
                        factors)

        # ── 7. Sideways / Range Bound ────────────────────────────────────────
        session_range_pct = (
            (session_h - session_l) / session_l * 100
            if session_l > 0 else 0.0
        )
        if bb_bw < BB_SQUEEZE_PCT or (adx < ADX_TREND_THRESH and session_range_pct < 1.5):
            return (MarketRegime.RANGE_BOUND, 55.0,
                    f"Low ADX={adx:.1f}, session range={session_range_pct:.2f}% – range bound",
                    factors)

        return (MarketRegime.SIDEWAYS, 50.0,
                f"No strong directional pressure. ADX={adx:.1f}",
                factors)
