from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from market.models import Timeframe
from analysis.models import MarketAnalysisReport
from analysis.cache import AnalysisCache
from analysis.publisher import AnalysisPublisher

from market.manager import market_data_manager
from indicators.engine import indicator_engine
from regime.engine import regime_engine
from risk.engine import risk_engine
from strategies.engine import strategy_engine

class MarketAnalysisEngine:
    """
    Market Analysis Intelligence Layer.
    Orchestrates the retrieval of data across Market Data, Indicators,
    Regime, Risk, and Strategies to compile a MarketAnalysisReport.
    """
    def __init__(self) -> None:
        self.cache = AnalysisCache()
        self.publisher = AnalysisPublisher(self.cache)

    async def analyze_market(
        self, symbol: str, timeframe: Timeframe, strategy_response: Optional[dict] = None
    ) -> MarketAnalysisReport:
        # 1. Fetch Indicators
        rsi = 50.0
        adx = 0.0
        atr = 0.0
        support_val = 0.0
        resistance_val = 0.0
        momentum_val = 0.0
        
        r_rsi = await indicator_engine.cache.get_latest(symbol, timeframe, "RSI")
        if r_rsi and isinstance(r_rsi.value, (int, float)):
            rsi = float(r_rsi.value)
            
        r_adx = await indicator_engine.cache.get_latest(symbol, timeframe, "ADX")
        if r_adx and isinstance(r_adx.value, dict):
            adx = float(r_adx.value.get("adx", 0.0))
            
        r_atr = await indicator_engine.cache.get_latest(symbol, timeframe, "ATR")
        if r_atr and isinstance(r_atr.value, (int, float)):
            atr = float(r_atr.value)
            
        r_sr = await indicator_engine.cache.get_latest(symbol, timeframe, "S_R")
        if r_sr and isinstance(r_sr.value, dict):
            support_val = float(r_sr.value.get("support", 0.0))
            resistance_val = float(r_sr.value.get("resistance", 0.0))
            
        r_mom = await indicator_engine.cache.get_latest(symbol, timeframe, "MOMENTUM")
        if r_mom and isinstance(r_mom.value, (int, float)):
            momentum_val = float(r_mom.value)

        # 2. Fetch Regime
        regime_val = "SIDEWAYS"
        r_reg = await regime_engine.cache.get_latest(symbol, timeframe)
        if r_reg:
            regime_val = r_reg.market_regime.value

        # 3. Evaluate Strategy rules to find recommendation
        rec_strategy = "Wait & Watch"
        matched_conf = 50.0
        matched_reason = "No active strategy rules matched."
        
        if strategy_response:
            rec_strategy = strategy_response.get("strategy_name", "Wait & Watch")
            matched_conf = float(strategy_response.get("confidence", 50.0))
            matched_reason = strategy_response.get("reason", "Strategy match provided.")
        else:
            try:
                strategy_responses = await strategy_engine.evaluate_all(symbol, timeframe, publish_events=False)
                for resp in strategy_responses:
                    if resp.matched:
                        rec_strategy = resp.strategy_name
                        matched_conf = resp.confidence
                        matched_reason = resp.reason
                        break
            except Exception:
                pass

        # 4. Formulate Bias
        bias = "NEUTRAL"
        if "BULLISH" in regime_val or rsi > 55:
            bias = "BULLISH"
        elif "BEARISH" in regime_val or rsi < 45:
            bias = "BEARISH"

        # 5. Formulate Trend Strength
        trend_strength = "NONE"
        if adx > 25.0:
            trend_strength = "STRONG"
        elif adx > 15.0:
            trend_strength = "WEAK"

        # 6. Formulate Momentum
        momentum = "FLAT"
        if rsi > 55.0 or momentum_val > 0:
            momentum = "BULLISH"
        elif rsi < 45.0 or momentum_val < 0:
            momentum = "BEARISH"

        # 7. Formulate Volatility
        volatility = "NORMAL"
        # If ATR exists and we can compare
        tick = await market_data_manager.cache.get_tick(symbol)
        ltp = tick.ltp if tick else 100.0
        if atr > 0:
            atr_pct = (atr / ltp) * 100
            if atr_pct > 1.5:
                volatility = "HIGH"
            elif atr_pct < 0.5:
                volatility = "LOW"

        # Fallback support/resistance if not calculated
        if support_val == 0.0 and tick:
            support_val = tick.low
            resistance_val = tick.high

        reasoning = (
            f"Market bias is {bias} based on {regime_val} regime. "
            f"Trend strength is {trend_strength} (ADX={adx:.1f}), "
            f"Momentum is {momentum} (RSI={rsi:.1f}). "
            f"Strategy matched: {rec_strategy} ({matched_reason})."
        )

        report = MarketAnalysisReport(
            symbol=symbol,
            timeframe=timeframe.value,
            market_bias=bias,
            trend_strength=trend_strength,
            momentum=momentum,
            volatility=volatility,
            market_structure=regime_val,
            support=support_val,
            resistance=resistance_val,
            recommended_strategy=rec_strategy,
            confidence=matched_conf,
            reasoning=reasoning,
            timestamp=datetime.now(timezone.utc)
        )

        # 8. Publish Report
        await self.publisher.publish(report)
        return report

# Singleton
market_analysis_engine = MarketAnalysisEngine()
