import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from core.bus import event_bus, EventModel
from core.logging import get_logger
from market.models import Timeframe, Candle

logger = get_logger("volume_intelligence")

class VolumeIntelligenceEmployee:
    """
    Volume Intelligence Employee.
    Analyzes trading volume (spikes, RVOL, trends, breakout confirmations) to validate signals.
    Does NOT execute trades. Provides validation checks before order executions.
    """
    def __init__(self) -> None:
        # (symbol, timeframe) -> list of candle dicts
        self.candles_history: Dict[tuple, List[Dict[str, Any]]] = {}
        # symbol -> latest volume analysis details
        self.latest_results: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_history = 100
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("candle", self._on_candle_event)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("VolumeIntelligenceEmployee started and subscribed to candle events.")

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
        logger.info("VolumeIntelligenceEmployee stopped.")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                from employees.engine import employee_engine
                # Safely get latest metrics/status
                rvol_str = "Neutral"
                score = 50.0
                if self.latest_results:
                    # Pick arbitrary symbol to check status
                    first_res = next(iter(self.latest_results.values()))
                    rvol_str = f"RVOL: {first_res.get('rvol', 1.0):.1f}"
                    score = first_res.get("confidence", 50.0)
                
                await employee_engine.manager.record_activity(
                    employee_code="EMP-VOL",
                    decision=rvol_str,
                    confidence=score,
                    execution_time_ms=0.0
                )
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _on_candle_event(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            candle = Candle(**raw_candle)
            
            # Only analyze completed bars
            if candle.complete:
                await self.analyze_volume(candle)
        except Exception as e:
            logger.error("Error processing candle in VolumeIntelligenceEmployee", error=str(e))

    async def analyze_volume(self, candle: Candle) -> Dict[str, Any]:
        async with self._lock:
            symbol = candle.symbol
            timeframe = candle.timeframe
            key = (symbol, timeframe)

            if key not in self.candles_history:
                self.candles_history[key] = []

            # Append bar data
            self.candles_history[key].append({
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "timestamp": candle.timestamp
            })

            # Limit history length
            if len(self.candles_history[key]) > self._max_history:
                self.candles_history[key].pop(0)

            history = self.candles_history[key]
            n_bars = len(history)

            # Heuristics defaults
            rvol = 1.0
            avg_volume = candle.volume
            volume_spike = False
            breakout_confirmed = False
            fake_breakout = False
            volume_trend = "NEUTRAL"
            buy_volume = candle.volume / 2.0
            sell_volume = candle.volume / 2.0
            confidence = 50.0
            confirmation = "NEUTRAL"

            # Perform calculations if history is sufficient
            if n_bars >= 5:
                # 1. Average Volume of previous candles (lookback 20 or up to history - 1 length)
                previous_candles = history[:-1]
                lookback = min(20, len(previous_candles))
                volumes = [b["volume"] for b in previous_candles[-lookback:]]
                avg_volume = sum(volumes) / lookback if lookback > 0 else candle.volume

                # 2. RVOL (Relative Volume)
                rvol = candle.volume / avg_volume if avg_volume > 0 else 1.0

                # 3. Volume Spike Detection
                volume_spike = rvol > 2.0

                # 4. Buy/Sell Volume estimation based on candle close location
                if candle.high != candle.low:
                    buy_ratio = (candle.close - candle.low) / (candle.high - candle.low)
                    buy_volume = candle.volume * buy_ratio
                    sell_volume = candle.volume * (1.0 - buy_ratio)
                else:
                    buy_volume = candle.volume / 2.0
                    sell_volume = candle.volume / 2.0

                # 5. Volume Trend Analysis (compare short SMA 5 with long SMA 20/lookback)
                short_lookback = min(5, n_bars)
                short_volumes = [b["volume"] for b in history[-short_lookback:]]
                short_avg = sum(short_volumes) / short_lookback
                
                if short_avg > avg_volume * 1.15:
                    volume_trend = "UPWARD"
                elif short_avg < avg_volume * 0.85:
                    volume_trend = "DOWNWARD"
                else:
                    volume_trend = "NEUTRAL"

                # 6. Breakout Volume Confirmation & Fake Breakout Detection
                # Heuristic: Check if price moves strongly (bullish or bearish candle body size > 1% of open price)
                body_pct = abs(candle.close - candle.open) / candle.open if candle.open > 0 else 0.0
                is_strong_move = body_pct > 0.0075  # 0.75% move
                
                if is_strong_move:
                    if rvol > 1.5:
                        breakout_confirmed = True
                        confirmation = "CONFIRM"
                    elif rvol < 0.8:
                        fake_breakout = True
                        confirmation = "REJECT"

                # 7. Confidence Score Calculations (0 - 100)
                # Base is 50. Adjust according to signals
                score_adj = 0.0
                if volume_spike:
                    score_adj += 15.0
                if volume_trend == "UPWARD" and candle.close > candle.open:
                    score_adj += 15.0
                elif volume_trend == "DOWNWARD" and candle.close < candle.open:
                    score_adj += 15.0
                
                if breakout_confirmed:
                    score_adj += 20.0
                elif fake_breakout:
                    score_adj -= 35.0

                confidence = max(0.0, min(100.0, 50.0 + score_adj))

                # If confidence is exceptionally high and confirms the move, promote to CONFIRM
                if confidence > 75.0 and confirmation == "NEUTRAL":
                    confirmation = "CONFIRM"
                # If confidence drops very low or fake breakout triggers, make it REJECT
                elif confidence < 35.0 or fake_breakout:
                    confirmation = "REJECT"

            # Volume Score calculation
            volume_score = min(100.0, rvol * 50.0)

            result = {
                "symbol": symbol,
                "timeframe": timeframe.value if hasattr(timeframe, "value") else str(timeframe),
                "volume_score": round(volume_score, 2),
                "rvol": round(rvol, 4),
                "volume_trend": volume_trend,
                "confirmation_status": confirmation,
                "confidence": round(confidence, 2),
                "avg_volume": round(avg_volume, 2),
                "volume_spike": volume_spike,
                "breakout_confirmed": breakout_confirmed,
                "fake_breakout": fake_breakout,
                "buy_volume": round(buy_volume, 2),
                "sell_volume": round(sell_volume, 2),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            self.latest_results[symbol] = result

            # Publish event to Event Bus
            await event_bus.publish(EventModel(
                event_type="volume_intelligence_update",
                source_agent="volume_intelligence_employee",
                payload=result
            ))

            return result

    async def check_confirmation(self, symbol: str) -> str:
        """Returns CONFIRM, REJECT, or NEUTRAL status for the given symbol."""
        async with self._lock:
            res = self.latest_results.get(symbol)
            if not res:
                return "NEUTRAL"
            return res.get("confirmation_status", "NEUTRAL")
