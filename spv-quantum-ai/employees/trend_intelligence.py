import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
from core.bus import event_bus, EventModel
from core.logging import get_logger
from market.models import Candle

from indicators.math import (
    calc_ema, calc_vwap, calc_supertrend, calc_adx, calc_rsi, calc_macd
)

logger = get_logger("trend_intelligence")

class TrendIntelligenceEmployee:
    """
    Trend Intelligence Employee.
    Analyzes technical indicators (EMAs, VWAP, SuperTrend, ADX, RSI, MACD) to classify market trend.
    Does NOT execute trades. Publishes trend recommendations to the event bus.
    """
    def __init__(self) -> None:
        # (symbol, timeframe) -> list of candle dicts
        self.candles_history: Dict[tuple, List[Dict[str, Any]]] = {}
        # symbol -> latest trend analysis details
        self.latest_results: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_history = 300
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("candle", self._on_candle_event)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("TrendIntelligenceEmployee started and subscribed to candle events.")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        await event_bus.unsubscribe("candle", self._on_candle_event)
        logger.info("TrendIntelligenceEmployee stopped.")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                from employees.engine import employee_engine
                # Safely get latest metrics/status
                trend_str = "Sideways"
                score = 50.0
                if self.latest_results:
                    first_res = next(iter(self.latest_results.values()))
                    trend_str = first_res.get('trend', 'SIDEWAYS')
                    score = first_res.get("confidence", 50.0)
                
                await employee_engine.manager.record_activity(
                    employee_code="EMP-TRD",
                    decision=trend_str,
                    confidence=score,
                    execution_time_ms=0.0
                )
            except Exception as e:
                logger.error("Heartbeat fail in TrendIntelligenceEmployee", error=str(e))
            await asyncio.sleep(5)

    async def _on_candle_event(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            candle = Candle(**raw_candle)
            
            # Only analyze completed bars
            if candle.complete:
                await self.analyze_trend(candle)
        except Exception as e:
            logger.error("Error processing candle in TrendIntelligenceEmployee", error=str(e))

    async def analyze_trend(self, candle: Candle) -> Dict[str, Any]:
        async with self._lock:
            symbol = candle.symbol
            timeframe = candle.timeframe.value if hasattr(candle.timeframe, "value") else str(candle.timeframe)
            key = (symbol, timeframe)
            
            if key not in self.candles_history:
                self.candles_history[key] = []
                
            self.candles_history[key].append(candle.model_dump())
            if len(self.candles_history[key]) > self._max_history:
                self.candles_history[key].pop(0)
                
            candles = self.candles_history[key]
            
            # Require minimum candles for solid indicator calculations (e.g. at least 200 for EMA 200)
            if len(candles) < 20:
                result = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "trend": "NO TRADE",
                    "confidence": 0.0,
                    "trend_strength": "Weak Trend",
                    "momentum_score": 50.0,
                    "direction": "NEUTRAL",
                    "recommendation": "WAIT",
                    "ema_alignment": "NONE",
                    "vwap_status": "NONE",
                    "adx": 0.0,
                    "rsi": 50.0,
                    "macd": {"macd": 0.0, "signal": 0.0, "histogram": 0.0},
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self.latest_results[symbol] = result
                return result

            # Extract price series
            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            volumes = [c["volume"] for c in candles]
            
            # 1. EMAs
            ema9 = calc_ema(closes, 9)
            ema20 = calc_ema(closes, 20)
            ema50 = calc_ema(closes, 50)
            ema200 = calc_ema(closes, 200) if len(closes) >= 200 else calc_ema(closes, 50)
            
            # 2. VWAP
            vwap = calc_vwap(highs, lows, closes, volumes)
            
            # 3. SuperTrend
            supertrend_val, st_direction = calc_supertrend(highs, lows, closes, period=10, multiplier=3.0)
            
            # 4. ADX (requires at least 28 candles, fallback to 0)
            adx_val = 0.0
            di_pos = 0.0
            di_neg = 0.0
            if len(closes) >= 28:
                try:
                    adx_val, di_pos, di_neg = calc_adx(highs, lows, closes, period=14)
                except Exception:
                    pass
            
            # 5. RSI
            rsi_val = calc_rsi(closes, period=14)
            
            # 6. MACD
            macd_val, macd_signal, macd_hist = calc_macd(closes, fast=12, slow=26, signal=9)
            
            # 7. HH/HL & LH/LL structure detection
            peaks = []
            troughs = []
            subset_len = min(20, len(highs))
            for i in range(2, subset_len - 2):
                idx = len(highs) - subset_len + i
                if highs[idx] > highs[idx-1] and highs[idx] > highs[idx-2] and highs[idx] > highs[idx+1] and highs[idx] > highs[idx+2]:
                    peaks.append(highs[idx])
                if lows[idx] < lows[idx-1] and lows[idx] < lows[idx-2] and lows[idx] < lows[idx+1] and lows[idx] < lows[idx+2]:
                    troughs.append(lows[idx])
                    
            hh_hl = False
            lh_ll = False
            if len(peaks) >= 2 and len(troughs) >= 2:
                hh_hl = (peaks[-1] > peaks[-2]) and (troughs[-1] > troughs[-2])
                lh_ll = (peaks[-1] < peaks[-2]) and (troughs[-1] < troughs[-2])
            elif len(peaks) >= 2:
                hh_hl = (peaks[-1] > peaks[-2])
                lh_ll = (peaks[-1] < peaks[-2])

            # 8. Breakout / Breakdown
            breakout = False
            breakdown = False
            if len(closes) > 11:
                prev_10_high = max(highs[-11:-1])
                prev_10_low = min(lows[-11:-1])
                breakout = (closes[-1] > prev_10_high)
                breakdown = (closes[-1] < prev_10_low)

            # Trend Classification Scoring
            score = 0
            
            # EMA alignment rules
            ema_alignment = "NONE"
            if closes[-1] > ema9 > ema20 > ema50 > ema200:
                score += 3
                ema_alignment = "BULLISH"
            elif closes[-1] < ema9 < ema20 < ema50 < ema200:
                score -= 3
                ema_alignment = "BEARISH"
                
            # VWAP Status
            vwap_status = "NONE"
            if closes[-1] > vwap:
                score += 1
                vwap_status = "ABOVE"
            elif closes[-1] < vwap:
                score -= 1
                vwap_status = "BELOW"
                
            # SuperTrend
            if st_direction == 1:
                score += 2
            else:
                score -= 2
                
            # ADX DI cross
            if adx_val > 25:
                if di_pos > di_neg:
                    score += 1
                else:
                    score -= 1
                    
            # RSI
            if rsi_val > 55:
                score += 1
            elif rsi_val < 45:
                score -= 1
                
            # MACD
            if macd_hist > 0:
                score += 1
            elif macd_hist < 0:
                score -= 1

            # Determine final Trend Classification
            if score >= 6:
                trend = "STRONG BULLISH"
                direction = "BULLISH"
                recommendation = "BUY"
                confidence = float(min(100, 65 + score * 4))
            elif score >= 2:
                trend = "BULLISH"
                direction = "BULLISH"
                recommendation = "BUY"
                confidence = float(min(100, 50 + score * 5))
            elif score <= -6:
                trend = "STRONG BEARISH"
                direction = "BEARISH"
                recommendation = "SELL"
                confidence = float(min(100, 65 + abs(score) * 4))
            elif score <= -2:
                trend = "BEARISH"
                direction = "BEARISH"
                recommendation = "SELL"
                confidence = float(min(100, 50 + abs(score) * 5))
            else:
                trend = "SIDEWAYS"
                direction = "NEUTRAL"
                recommendation = "WAIT"
                confidence = float(45.0 + abs(score) * 2)

            # Trend Strength Label
            if adx_val > 40:
                trend_strength = "Extremely Strong Trend"
            elif adx_val > 25:
                trend_strength = "Strong Trend"
            else:
                trend_strength = "Weak Trend"

            # Momentum Score
            momentum_score = float(max(0, min(100, rsi_val + (10 if macd_hist > 0 else -10))))

            result = {
                "symbol": symbol,
                "timeframe": timeframe,
                "trend": trend,
                "confidence": confidence,
                "trend_strength": trend_strength,
                "momentum_score": momentum_score,
                "direction": direction,
                "recommendation": recommendation,
                "ema_alignment": ema_alignment,
                "vwap_status": vwap_status,
                "adx": round(adx_val, 2),
                "rsi": round(rsi_val, 2),
                "macd": {"macd": round(macd_val, 4), "signal": round(macd_signal, 4), "histogram": round(macd_hist, 4)},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Detect shifts for signals/warnings
            prev_result = self.latest_results.get(symbol)
            self.latest_results[symbol] = result

            # Publish events
            await event_bus.publish(EventModel(
                event_type="trend_updated",
                source_agent="trend_intelligence_employee",
                payload=result
            ))

            # If recommendation changed, trigger trend_signal
            if not prev_result or prev_result.get("recommendation") != recommendation:
                if recommendation in ["BUY", "SELL"]:
                    await event_bus.publish(EventModel(
                        event_type="trend_signal",
                        source_agent="trend_intelligence_employee",
                        payload={
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "signal_type": recommendation,
                            "trend": trend,
                            "confidence": confidence,
                            "timestamp": result["timestamp"]
                        }
                    ))

            # Warnings on extreme overbought / oversold
            if rsi_val > 80 or rsi_val < 20:
                await event_bus.publish(EventModel(
                    event_type="trend_warning",
                    source_agent="trend_intelligence_employee",
                    payload={
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "warning": f"Extreme RSI warning: {rsi_val:.1f} (Overbought/Oversold)",
                        "rsi": rsi_val,
                        "adx": adx_val,
                        "timestamp": result["timestamp"]
                    }
                ))
            elif breakout or breakdown:
                await event_bus.publish(EventModel(
                    event_type="trend_warning",
                    source_agent="trend_intelligence_employee",
                    payload={
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "warning": f"Price {'breakout' if breakout else 'breakdown'} detected on {symbol}.",
                        "rsi": rsi_val,
                        "adx": adx_val,
                        "timestamp": result["timestamp"]
                    }
                ))

            return result
