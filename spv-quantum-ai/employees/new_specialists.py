import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from core.bus import event_bus, EventModel
from core.logging import get_logger
from market.models import Candle

# Import stateless math helpers
from indicators.math import calc_rsi, calc_vwap, calc_adx, calc_macd, calc_ema

logger = get_logger("new_specialists")

class BaseNewSpecialist:
    """Base class for new specialists to inherit standard heartbeat and lifecycle control."""
    def __init__(self, employee_code: str, default_decision: str = "Neutral") -> None:
        self.employee_code = employee_code
        self.default_decision = default_decision
        self.latest_results: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"{self.__class__.__name__} ({self.employee_code}) started.")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info(f"{self.__class__.__name__} ({self.employee_code}) stopped.")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                from employees.engine import employee_engine
                decision_str = self.default_decision
                score = 50.0
                if self.latest_results:
                    first_res = next(iter(self.latest_results.values()))
                    decision_str = first_res.get('recommendation', self.default_decision)
                    score = first_res.get("confidence", 50.0)
                
                await employee_engine.manager.record_activity(
                    employee_code=self.employee_code,
                    decision=decision_str,
                    confidence=score,
                    execution_time_ms=0.0
                )
            except Exception as e:
                logger.error(f"Heartbeat fail in {self.__class__.__name__}", error=str(e))
            await asyncio.sleep(5)


# ── Market Intelligence Department ──────────────────────────────────────────

class MomentumEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-MOM", "WAIT")
        self.candles_history: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            close = float(raw_candle.get("close", 0.0))
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            if symbol not in self.candles_history:
                self.candles_history[symbol] = []
            self.candles_history[symbol].append(close)
            if len(self.candles_history[symbol]) > 100:
                self.candles_history[symbol].pop(0)

            closes = self.candles_history[symbol]
            rsi = 50.0
            macd_hist = 0.0
            
            if len(closes) >= 15:
                rsi = calc_rsi(closes, 14)
            if len(closes) >= 26:
                try:
                    macd, signal, hist = calc_macd(closes, fast=12, slow=26, signal=9)
                    macd_hist = hist
                except Exception:
                    pass

            rec = "WAIT"
            if rsi > 60 and macd_hist > 0:
                rec = "BUY"
            elif rsi < 40 and macd_hist < 0:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + abs(rsi - 50.0) * 2.0 + (10.0 if macd_hist != 0 else 0.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "rsi": round(rsi, 2),
                "macd_histogram": round(macd_hist, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            # Publish detailed updates and signals
            await event_bus.publish(EventModel(
                event_type="momentum_updated",
                source_agent="momentum_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="momentum_signal",
                    source_agent="momentum_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in MomentumEmployee _on_candle", error=str(e))


class VWAPEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-VWP", "WAIT")
        self.candles_history: Dict[str, Dict[str, List[float]]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            high = float(raw_candle.get("high", 0.0))
            low = float(raw_candle.get("low", 0.0))
            close = float(raw_candle.get("close", 0.0))
            volume = float(raw_candle.get("volume", 0.0))
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            if symbol not in self.candles_history:
                self.candles_history[symbol] = {"highs": [], "lows": [], "closes": [], "volumes": []}

            hist = self.candles_history[symbol]
            hist["highs"].append(high)
            hist["lows"].append(low)
            hist["closes"].append(close)
            hist["volumes"].append(volume)

            if len(hist["closes"]) > 100:
                hist["highs"].pop(0)
                hist["lows"].pop(0)
                hist["closes"].pop(0)
                hist["volumes"].pop(0)

            closes = hist["closes"]
            vwap = calc_vwap(hist["highs"], hist["lows"], closes, hist["volumes"])
            
            # Compute distance and basic standard deviation bands (VWAP bands)
            dist_pct = (close - vwap) / vwap if vwap > 0 else 0.0
            
            rec = "WAIT"
            # Require at least a 0.1% deviation from VWAP for higher decision quality
            if dist_pct > 0.001:
                rec = "BUY"
            elif dist_pct < -0.001:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, abs(dist_pct) * 5000.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "vwap": round(vwap, 2),
                "deviation_pct": round(dist_pct * 100.0, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="vwap_updated",
                source_agent="vwap_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="vwap_signal",
                    source_agent="vwap_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in VWAPEmployee _on_candle", error=str(e))


class MarketRegimeEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-RGM", "Sideways")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("market_regime", self._on_regime)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("market_regime", self._on_regime)

    async def _on_regime(self, event: EventModel) -> None:
        try:
            payload = event.payload
            regime = payload.get("market_regime", "Sideways")
            confidence = payload.get("confidence", 50.0)
            symbol = payload.get("symbol", "NIFTY50")
            
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": str(regime),
                    "confidence": confidence,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in MarketRegimeEmployee _on_regime", error=str(e))


# ── Options Intelligence Department ─────────────────────────────────────────

class OIEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-OIE", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("option_chain_updated", self._on_chain)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("option_chain_updated", self._on_chain)

    async def _on_chain(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = payload.get("underlying_symbol", "NIFTY")
            ce_oi = sum(c.get("open_interest", 0) for c in payload.get("calls", []))
            pe_oi = sum(c.get("open_interest", 0) for c in payload.get("puts", []))
            
            rec = "WAIT"
            if pe_oi > ce_oi * 1.15:
                rec = "BUY"
            elif ce_oi > pe_oi * 1.15:
                rec = "SELL"

            diff = abs(pe_oi - ce_oi)
            total = max(1.0, ce_oi + pe_oi)
            confidence = float(max(0.0, min(100.0, 50.0 + (diff / total) * 100.0)))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "call_oi": ce_oi,
                "put_oi": pe_oi,
                "oi_ratio": round(pe_oi / ce_oi if ce_oi > 0 else 1.0, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="oi_updated",
                source_agent="oi_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="oi_signal",
                    source_agent="oi_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in OIEmployee _on_chain", error=str(e))


class PCREmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-PCR", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("option_chain_updated", self._on_chain)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("option_chain_updated", self._on_chain)

    async def _on_chain(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = payload.get("underlying_symbol", "NIFTY")
            pcr = float(payload.get("pcr", 1.0))
            
            rec = "WAIT"
            # Require clearer PCR signal
            if pcr > 1.25:
                rec = "BUY"
            elif pcr < 0.75:
                rec = "SELL"
                
            confidence = float(max(0.0, min(100.0, 50.0 + abs(pcr - 1.0) * 100.0)))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "pcr_value": round(pcr, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="pcr_updated",
                source_agent="pcr_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="pcr_signal",
                    source_agent="pcr_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in PCREmployee _on_chain", error=str(e))


class GreeksEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-GRK", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("option_chain_updated", self._on_chain)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("option_chain_updated", self._on_chain)

    async def _on_chain(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = payload.get("underlying_symbol", "NIFTY")
            calls = payload.get("calls", [])
            puts = payload.get("puts", [])
            
            # Net option delta/gamma bias calculation
            net_delta_bias = 0.0
            for c in calls:
                net_delta_bias += float(c.get("delta", 0.5))
            for p in puts:
                net_delta_bias -= abs(float(p.get("delta", -0.5)))

            rec = "WAIT"
            if net_delta_bias > 1.5:
                rec = "BUY"
            elif net_delta_bias < -1.5:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + abs(net_delta_bias) * 15.0)))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "net_delta_bias": round(net_delta_bias, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="greeks_updated",
                source_agent="greeks_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="greeks_signal",
                    source_agent="greeks_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in GreeksEmployee _on_chain", error=str(e))


class MaxPainEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-MPN", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("option_chain_updated", self._on_chain)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("option_chain_updated", self._on_chain)

    async def _on_chain(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = payload.get("underlying_symbol", "NIFTY")
            atm_strike = float(payload.get("atm_strike", 0.0))
            max_pain = float(payload.get("max_pain", atm_strike))
            
            rec = "WAIT"
            diff = max_pain - atm_strike
            if diff > 10.0:
                rec = "BUY"
            elif diff < -10.0:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, abs(diff) * 2.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "max_pain_strike": max_pain,
                "atm_strike": atm_strike,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="max_pain_updated",
                source_agent="max_pain_employee",
                payload=result
            ))
            if rec in ("BUY", "SELL"):
                await event_bus.publish(EventModel(
                    event_type="max_pain_signal",
                    source_agent="max_pain_employee",
                    payload=result
                ))
        except Exception as e:
            logger.error("Error in MaxPainEmployee _on_chain", error=str(e))


# ── Institutional Department ────────────────────────────────────────────────

class SmartMoneyEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-SME", "WAIT")
        self.volumes_history: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            volume = float(raw_candle.get("volume", 0.0))
            close = float(raw_candle.get("close", 0.0))
            open_p = float(raw_candle.get("open", 0.0))
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            if symbol not in self.volumes_history:
                self.volumes_history[symbol] = []
            self.volumes_history[symbol].append(volume)
            if len(self.volumes_history[symbol]) > 20:
                self.volumes_history[symbol].pop(0)

            # Calculate average volume
            vols = self.volumes_history[symbol]
            avg_vol = sum(vols) / len(vols) if vols else 1.0

            rec = "WAIT"
            # Volume spike > 1.5x average
            if volume > avg_vol * 1.5 and avg_vol > 0:
                rec = "BUY" if close > open_p else "SELL"

            vol_ratio = volume / avg_vol if avg_vol > 0 else 1.0
            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, (vol_ratio - 1.0) * 25.0)))) if rec != "WAIT" else 50.0

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "volume_ratio": round(vol_ratio, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="smart_money_updated",
                source_agent="smart_money_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in SmartMoneyEmployee _on_candle", error=str(e))


class LiquidityEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-LQD", "WAIT")
        self.candles_history: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            volume = float(raw_candle.get("volume", 0.0))
            high = float(raw_candle.get("high", 0.0))
            low = float(raw_candle.get("low", 0.0))
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            if symbol not in self.candles_history:
                self.candles_history[symbol] = []
            
            # Amihud illiquidity proxy: |Return| / Volume
            price_range = abs(high - low)
            illiquidity = price_range / volume if volume > 0 else 1.0
            
            rec = "WAIT"
            # Low illiquidity (high liquidity)
            if illiquidity < 0.01:
                rec = "BUY"

            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, (0.01 / (illiquidity if illiquidity > 0 else 1e-5)) * 10.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "illiquidity_score": round(illiquidity, 6),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="liquidity_updated",
                source_agent="liquidity_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in LiquidityEmployee _on_candle", error=str(e))


class OrderFlowEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-OFL", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            close = float(raw_candle.get("close", 0.0))
            open_p = float(raw_candle.get("open", 0.0))
            high = float(raw_candle.get("high", 0.0))
            low = float(raw_candle.get("low", 0.0))
            
            # Shadow calculations: Buying pressure vs Selling pressure
            body = abs(close - open_p)
            upper_shadow = high - max(close, open_p)
            lower_shadow = min(close, open_p) - low
            
            rec = "WAIT"
            # Strong buying tail (lower shadow > 2x body)
            if lower_shadow > body * 2.0 and lower_shadow > upper_shadow:
                rec = "BUY"
            # Strong selling tail (upper shadow > 2x body)
            elif upper_shadow > body * 2.0 and upper_shadow > lower_shadow:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, (max(lower_shadow, upper_shadow) / max(0.01, body)) * 10.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="order_flow_updated",
                source_agent="order_flow_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in OrderFlowEmployee _on_candle", error=str(e))


class DeliveryEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-DEL", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            close = float(raw_candle.get("close", 0.0))
            open_p = float(raw_candle.get("open", 0.0))
            # Delivery investor looks at daily close strength
            rec = "BUY" if close > open_p else "SELL"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 55.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in DeliveryEmployee _on_candle", error=str(e))


# ── Risk Department ──────────────────────────────────────────────────────────

class RiskEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-RSK", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("safety_status", self._on_safety)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("safety_status", self._on_safety)

    async def _on_safety(self, event: EventModel) -> None:
        try:
            payload = event.payload
            blocked = payload.get("is_blocked", False)
            symbol = "SYSTEM"
            rec = "WAIT" if blocked else "BUY"
            
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 90.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in RiskEmployee _on_safety", error=str(e))


class PositionSizingEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-PZS", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            # Determine mock sizing recommendations
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 70.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PositionSizingEmployee _on_candle", error=str(e))


class CapitalProtectionEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-CPT", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("pnl_updated", self._on_pnl)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("pnl_updated", self._on_pnl)

    async def _on_pnl(self, event: EventModel) -> None:
        try:
            payload = event.payload
            daily_pnl = float(payload.get("realized_pnl", 0.0) + payload.get("unrealized_pnl", 0.0))
            symbol = "SYSTEM"
            # Protect capital if daily drawdown is large
            rec = "WAIT" if daily_pnl < -500.0 else "BUY"
            
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 85.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in CapitalProtectionEmployee _on_pnl", error=str(e))


class ExposureEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-EXP", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("portfolio_update", self._on_portfolio)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("portfolio_update", self._on_portfolio)

    async def _on_portfolio(self, event: EventModel) -> None:
        try:
            payload = event.payload
            exposure = float(payload.get("total_exposure", 0.0))
            symbol = "SYSTEM"
            # Restrict new buy if exposure is too high
            rec = "WAIT" if exposure > 50000.0 else "BUY"
            
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 80.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in ExposureEmployee _on_portfolio", error=str(e))


# ── News Department ──────────────────────────────────────────────────────────

class NewsEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-NWS", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            close = float(raw_candle.get("close", 0.0))
            open_p = float(raw_candle.get("open", 0.0))
            complete = raw_candle.get("complete", False)
            if not complete:
                return

            # Simulate sentiment based on price change
            change_pct = (close - open_p) / open_p if open_p > 0 else 0.0
            
            rec = "WAIT"
            if change_pct > 0.0005:
                rec = "BUY"
            elif change_pct < -0.0005:
                rec = "SELL"

            confidence = float(max(0.0, min(100.0, 50.0 + min(40.0, abs(change_pct) * 2000.0))))

            result = {
                "symbol": symbol,
                "recommendation": rec,
                "confidence": confidence,
                "sentiment_pct": round(change_pct * 100.0, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            async with self._lock:
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="news_updated",
                source_agent="news_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in NewsEmployee _on_candle", error=str(e))


class EconomicCalendarEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-CAL", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            async with self._lock:
                # Default calendar impact is low/neutral (WAIT) or moderate BUY (60% confidence)
                result = {
                    "symbol": symbol,
                    "recommendation": "BUY",
                    "confidence": 65.0,
                    "event_importance": "LOW",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="calendar_updated",
                source_agent="calendar_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in EconomicCalendarEmployee _on_candle", error=str(e))


class EventRiskEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-EVR", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            async with self._lock:
                result = {
                    "symbol": symbol,
                    "recommendation": "BUY",
                    "confidence": 80.0,
                    "risk_level": "LOW",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                self.latest_results[symbol] = result

            await event_bus.publish(EventModel(
                event_type="event_risk_updated",
                source_agent="event_risk_employee",
                payload=result
            ))
        except Exception as e:
            logger.error("Error in EventRiskEmployee _on_candle", error=str(e))


# ── Execution Department ─────────────────────────────────────────────────────

class ExecutionEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-EXE", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("order_filled", self._on_order)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("order_filled", self._on_order)

    async def _on_order(self, event: EventModel) -> None:
        try:
            payload = event.payload
            order_data = payload.get("order", payload)
            symbol = order_data.get("symbol", "NIFTY50")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 75.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in ExecutionEmployee _on_order", error=str(e))


class PortfolioEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-PTF", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("portfolio_update", self._on_portfolio)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("portfolio_update", self._on_portfolio)

    async def _on_portfolio(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = "SYSTEM"
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 70.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PortfolioEmployee _on_portfolio", error=str(e))


class PaperTradingEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-PPR", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("paper_trade_started", self._on_paper_started)
        await event_bus.subscribe("paper_trade_stopped", self._on_paper_stopped)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("paper_trade_started", self._on_paper_started)
        await event_bus.unsubscribe("paper_trade_stopped", self._on_paper_stopped)

    async def _on_paper_started(self, event: EventModel) -> None:
        try:
            async with self._lock:
                self.latest_results["SYSTEM"] = {
                    "recommendation": "BUY",
                    "confidence": 80.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PaperTradingEmployee _on_paper_started", error=str(e))

    async def _on_paper_stopped(self, event: EventModel) -> None:
        try:
            async with self._lock:
                self.latest_results["SYSTEM"] = {
                    "recommendation": "WAIT",
                    "confidence": 80.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PaperTradingEmployee _on_paper_stopped", error=str(e))


# ── Extra Predefined Specialists ─────────────────────────────────────────────

class OptionsSpecialistEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-OPT", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("option_chain_updated", self._on_chain)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("option_chain_updated", self._on_chain)

    async def _on_chain(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = payload.get("underlying_symbol", "NIFTY")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 70.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in OptionsSpecialistEmployee _on_chain", error=str(e))


class EquityIntradaySpecialistEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-EQI", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 65.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in EquityIntradaySpecialistEmployee _on_candle", error=str(e))


class EquitySwingSpecialistEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-EQS", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "NIFTY50")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 60.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in EquitySwingSpecialistEmployee _on_candle", error=str(e))


class CommoditySpecialistEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-COM", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "GOLD")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 68.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in CommoditySpecialistEmployee _on_candle", error=str(e))


class CurrencySpecialistEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-CUR", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            symbol = raw_candle.get("symbol", "USDINR")
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 62.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in CurrencySpecialistEmployee _on_candle", error=str(e))


class PortfolioManagerEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-PM", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("portfolio_update", self._on_portfolio)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("portfolio_update", self._on_portfolio)

    async def _on_portfolio(self, event: EventModel) -> None:
        try:
            payload = event.payload
            symbol = "SYSTEM"
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": "BUY",
                    "confidence": 70.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PortfolioManagerEmployee _on_portfolio", error=str(e))
