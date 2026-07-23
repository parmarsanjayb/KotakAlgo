import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import httpx
from core.bus import event_bus, EventModel
from core.config import settings
from core.logging import get_logger
from market.models import Candle

# Import stateless math helpers
from indicators.math import calc_rsi, calc_vwap, calc_adx

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
            if len(self.candles_history[symbol]) > 50:
                self.candles_history[symbol].pop(0)

            closes = self.candles_history[symbol]
            rsi = 50.0
            if len(closes) >= 15:
                rsi = calc_rsi(closes, 14)

            rec = "WAIT"
            if rsi > 65:
                rec = "BUY"
            elif rsi < 35:
                rec = "SELL"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": float(50.0 + abs(rsi - 50.0) * 2.0),
                    "rsi": rsi,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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

            vwap = calc_vwap(hist["highs"], hist["lows"], hist["closes"], hist["volumes"])
            rec = "WAIT"
            if close > vwap:
                rec = "BUY"
            elif close < vwap:
                rec = "SELL"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 60.0 if close != vwap else 50.0,
                    "vwap": vwap,
                    "close": close,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in VWAPEmployee _on_candle", error=str(e))


class MarketRegimeEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-RGM", "Sideways")

    async def start(self) -> None:
        await super().start()
        # RegimeEngine publishes "market_regime", not "regime_changed".
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
                    "recommendation": regime,
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
            if pe_oi > ce_oi * 1.1:
                rec = "BUY"
            elif ce_oi > pe_oi * 1.1:
                rec = "SELL"
                
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": float(min(100.0, 50.0 + abs(pe_oi - ce_oi) / max(1.0, ce_oi + pe_oi) * 100.0)),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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
            if pcr > 1.2:
                rec = "BUY"
            elif pcr < 0.8:
                rec = "SELL"
                
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": float(min(100.0, 50.0 + abs(pcr - 1.0) * 50.0)),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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
            # Calculate mock net Greeks (Delta bias)
            calls = payload.get("calls", [])
            puts = payload.get("puts", [])
            delta_bias = len(calls) - len(puts)
            
            rec = "WAIT"
            if delta_bias > 2:
                rec = "BUY"
            elif delta_bias < -2:
                rec = "SELL"
                
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 65.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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
            if atm_strike < max_pain:
                rec = "BUY"
            elif atm_strike > max_pain:
                rec = "SELL"
                
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 60.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in MaxPainEmployee _on_chain", error=str(e))


# ── Institutional Department ────────────────────────────────────────────────

class SmartMoneyEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-SME", "WAIT")

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

            # Smart money block: extreme volume with bullish/bearish candle
            rec = "WAIT"
            if volume > 50000:
                rec = "BUY" if close > open_p else "SELL"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 75.0 if rec != "WAIT" else 50.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in SmartMoneyEmployee _on_candle", error=str(e))


class LiquidityEmployee(BaseNewSpecialist):
    def __init__(self) -> None:
        super().__init__("EMP-LQD", "WAIT")

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

            # High volume implies higher liquidity
            volume = float(raw_candle.get("volume", 0.0))
            rec = "WAIT"
            if volume > 10000:
                rec = "BUY"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 60.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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
            rec = "WAIT"
            if close > open_p * 1.002:
                rec = "BUY"
            elif close < open_p * 0.998:
                rec = "SELL"

            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 65.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
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
        # "safety_status" is never published anywhere (SafetyEngine.check_order()
        # emits "safety_blocked" / "safety_check_passed" per order via SafetyManager).
        await event_bus.subscribe("safety_blocked", self._on_safety)
        await event_bus.subscribe("safety_check_passed", self._on_safety)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("safety_blocked", self._on_safety)
        await event_bus.unsubscribe("safety_check_passed", self._on_safety)

    async def _on_safety(self, event: EventModel) -> None:
        try:
            payload = event.payload
            blocked = event.event_type == "safety_blocked"
            symbol = payload.get("order_details", {}).get("symbol", "SYSTEM")
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
        # PortfolioEngine publishes "pnl_updated", not "pnl_update".
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

# NewsAPI.org configuration. The free developer tier allows 100 requests/day, so
# we poll every 15 min (~96/day) with ONE combined query instead of per-symbol.
_NEWSAPI_URL = "https://newsapi.org/v2/everything"
_NEWS_QUERY = ('(nifty OR sensex OR "bank nifty" OR "indian stock market" OR "gift nifty" OR '
               '"sgx nifty" OR "dow jones" OR nasdaq OR "s&p 500" OR "wall street" OR '
               '"crude oil" OR brent OR gold OR silver OR "dollar index" OR "indian rupee" OR '
               'RBI OR "us fed" OR fomc)')
_NEWS_POLL_INTERVAL = 900  # seconds (15 min) — stays under the free-tier daily cap

# Keywords that mark a headline as relevant to each tracked symbol.
_NEWS_SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "NIFTY50":   ["nifty", "sensex", "nse ", "dalal street", "indian stock", "indian market"],
    "BANKNIFTY": ["bank nifty", "banknifty", "banking", "hdfc bank", "icici bank", "sbi", "axis bank", "kotak bank"],
    "CRUDEOIL":  ["crude", "oil price", "brent", "wti", "opec"],
    "GOLD":      ["gold"],
    "SILVER":    ["silver"],
}

# Global cues that move the Indian market at/before the open. Tracked alongside
# the local symbols so the News employee reports an overnight/pre-market bias.
_GLOBAL_CUES: Dict[str, List[str]] = {
    "GIFT Nifty":     ["gift nifty", "sgx nifty"],
    "US Markets":     ["dow jones", "nasdaq", "s&p 500", "wall street", "us stocks", "wall st"],
    "Crude Oil":      ["crude", "brent", "wti", "opec"],
    "Dollar (DXY)":   ["dollar index", "dxy", "us dollar"],
    "US Bond Yields": ["treasury yield", "10-year yield", "bond yield"],
    "Asian Markets":  ["nikkei", "hang seng", "shanghai", "asian markets", "asian shares"],
}

# Naive lexicon for headline sentiment — enough to bias BUY / SELL / WAIT.
_NEWS_BULLISH = {"surge", "surges", "rally", "rallies", "jump", "jumps", "gain", "gains", "rise", "rises",
                 "soar", "soars", "record", "high", "boost", "bullish", "strong", "upgrade", "outperform",
                 "recover", "recovers", "rebound", "optimism", "positive", "beat", "beats", "profit", "profits", "up"}
_NEWS_BEARISH = {"fall", "falls", "drop", "drops", "plunge", "plunges", "slump", "crash", "crashes", "decline",
                 "declines", "low", "loss", "losses", "weak", "bearish", "downgrade", "cut", "cuts", "fear", "fears",
                 "concern", "concerns", "recession", "tumble", "tumbles", "slip", "slips", "negative", "miss",
                 "misses", "warn", "warning", "selloff", "sell-off", "down"}


def _score_news_sentiment(texts: List[str]) -> float:
    """Return a sentiment score in [-1, 1] from headline/description texts."""
    bull = bear = 0
    for t in texts:
        wset = set(t.lower().replace(",", " ").replace(".", " ").split())
        bull += len(wset & _NEWS_BULLISH)
        bear += len(wset & _NEWS_BEARISH)
    total = bull + bear
    return (bull - bear) / total if total else 0.0


# Only headlines that hit one of these market-moving phrases are important enough
# to push to Telegram — everything else updates the dashboard silently. This is
# what keeps the channel from being spammed with routine news.
_IMPORTANT_PHRASES = [
    # policy / macro
    "rbi", "repo rate", "monetary policy", "mpc", "fomc", "us fed", "federal reserve",
    "rate hike", "rate cut", "inflation", "cpi", "gdp", "union budget", "fiscal",
    # global cues
    "gift nifty", "sgx nifty", "dow jones", "nasdaq", "s&p 500", "wall street",
    "crude", "brent", "opec", "dollar index", "treasury yield", "nikkei", "hang seng",
    # sharp moves / shocks
    "surge", "soar", "record high", "rally", "crash", "plunge", "tumble", "slump",
    "selloff", "sell-off", "meltdown", "circuit", "gap-up", "gap up", "gap-down",
    "ban", "default", "downgrade", "upgrade", "war", "attack", "sanction", "results beat",
    "profit warning", "resigns", "fraud", "hike", "windfall",
]


def _is_important(text: str) -> bool:
    """True when a headline is market-moving enough to alert on Telegram."""
    t = text.lower()
    return any(p in t for p in _IMPORTANT_PHRASES)


class NewsEmployee(BaseNewSpecialist):
    """Reads real market headlines from NewsAPI.org, derives a per-symbol
    sentiment bias, and pushes fresh headlines to Telegram via a 'news_update'
    event. When no API key is configured (or the feed is unavailable) it stays
    in a neutral WAIT state — it never emits a fabricated signal."""

    _SYMBOLS = ["NIFTY50", "BANKNIFTY", "CRUDEOIL", "GOLD", "SILVER"]

    def __init__(self) -> None:
        super().__init__("EMP-NWS", "WAIT")
        self._polling_task: Optional[asyncio.Task] = None
        self._seen: deque = deque(maxlen=500)  # URLs already pushed to Telegram (bounded)
        self._warned_no_key = False

    async def start(self) -> None:
        await super().start()
        self._polling_task = asyncio.create_task(self._poll_news_loop())

    async def stop(self) -> None:
        await super().stop()
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

    async def _poll_news_loop(self) -> None:
        while self._running:
            try:
                await self._fetch_and_process()
            except Exception as e:
                logger.error("Error in NewsEmployee news poll", error=str(e))
            await asyncio.sleep(_NEWS_POLL_INTERVAL)

    async def _fetch_and_process(self) -> None:
        api_key = settings.NEWSAPI_KEY
        if not api_key:
            if not self._warned_no_key:
                logger.warning("NEWSAPI_KEY not set — NewsEmployee idle (no fabricated signals). "
                               "Add NEWSAPI_KEY to .env to enable real news.")
                self._warned_no_key = True
            async with self._lock:
                for sym in self._SYMBOLS:
                    self.latest_results[sym] = {
                        "recommendation": "WAIT",
                        "confidence": 0.0,
                        "headline": "News source not configured (NEWSAPI_KEY missing).",
                        "source": None,
                        "sentiment_score": 0.0,
                        "article_count": 0,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
            await self._record("WAIT", 0.0)
            return

        articles = await self._fetch_articles(api_key)
        if articles is None:
            return  # transient error already logged — keep previous results

        strongest_rec, strongest_score = "WAIT", 0.0
        async with self._lock:
            for sym in self._SYMBOLS:
                kws = _NEWS_SYMBOL_KEYWORDS[sym]
                matched = [
                    a for a in articles
                    if any(k in ((a.get("title") or "") + " " + (a.get("description") or "")).lower() for k in kws)
                ]
                texts = [((a.get("title") or "") + " " + (a.get("description") or "")) for a in matched]
                score = _score_news_sentiment(texts)
                rec = "BUY" if score > 0.15 else "SELL" if score < -0.15 else "WAIT"
                confidence = round(min(95.0, 50.0 + abs(score) * 45.0), 1) if matched else 50.0
                top = matched[0] if matched else None
                self.latest_results[sym] = {
                    "recommendation": rec,
                    "confidence": confidence,
                    "headline": top.get("title") if top else "No recent headlines matched.",
                    "source": (top.get("source") or {}).get("name") if top else None,
                    "url": top.get("url") if top else None,
                    "sentiment_score": round(score, 3),
                    "article_count": len(matched),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if abs(score) > abs(strongest_score):
                    strongest_rec, strongest_score = rec, score

            # Global cues that drive the Indian open — GIFT Nifty, US markets,
            # crude, dollar index, US yields, Asian markets. Reported as an
            # overnight/pre-market bias (BULLISH / BEARISH / NEUTRAL).
            for cue, kws in _GLOBAL_CUES.items():
                matched = [
                    a for a in articles
                    if any(k in ((a.get("title") or "") + " " + (a.get("description") or "")).lower() for k in kws)
                ]
                if not matched:
                    continue
                gscore = _score_news_sentiment([((a.get("title") or "") + " " + (a.get("description") or "")) for a in matched])
                self.latest_results[cue] = {
                    "recommendation": "BULLISH" if gscore > 0.15 else "BEARISH" if gscore < -0.15 else "NEUTRAL",
                    "confidence": round(min(95.0, 50.0 + abs(gscore) * 45.0), 1),
                    "headline": matched[0].get("title"),
                    "source": (matched[0].get("source") or {}).get("name"),
                    "url": matched[0].get("url"),
                    "sentiment_score": round(gscore, 3),
                    "article_count": len(matched),
                    "is_global_cue": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        await self._record(strongest_rec, round(min(95.0, 50.0 + abs(strongest_score) * 45.0), 1))
        await self._publish_new_headlines(articles)

        # Share the raw article set so the Calendar / Event-Risk employees can
        # derive their views WITHOUT making their own NewsAPI calls — three
        # independent pollers would blow the free-tier 100 requests/day cap.
        await event_bus.publish(EventModel(
            event_type="market_news_articles",
            source_agent="news_employee",
            payload={"articles": articles},
        ))

    async def _fetch_articles(self, api_key: str) -> Optional[List[Dict[str, Any]]]:
        params = {
            "q": _NEWS_QUERY,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 40,
            "apiKey": api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_NEWSAPI_URL, params=params)
            data = resp.json()
        except Exception as e:
            logger.error("NewsAPI request failed", error=str(e))
            return None
        if data.get("status") != "ok":
            logger.warning("NewsAPI returned error", code=data.get("code"), message=data.get("message"))
            return None
        return data.get("articles", []) or []

    async def _publish_new_headlines(self, articles: List[Dict[str, Any]]) -> None:
        """Emit a 'news_update' event carrying only NEW and IMPORTANT headlines.
        Routine headlines still update the dashboard sentiment silently, but only
        market-moving ones ping Telegram — this is what prevents channel spam."""
        fresh: List[Dict[str, Any]] = []
        for a in articles:
            url = a.get("url")
            if not url or url in self._seen:
                continue
            self._seen.append(url)  # judged once — mark seen even if not important
            text = ((a.get("title") or "") + " " + (a.get("description") or ""))
            if not _is_important(text):
                continue
            tags = [name for name, kws in {**_NEWS_SYMBOL_KEYWORDS, **_GLOBAL_CUES}.items()
                    if any(k in text.lower() for k in kws)]
            fresh.append({
                "title": a.get("title"),
                "source": (a.get("source") or {}).get("name"),
                "url": url,
                "published_at": a.get("publishedAt"),
                "symbols": tags,
            })

        if not fresh:
            return
        await event_bus.publish(EventModel(
            event_type="news_update",
            source_agent="news_employee",
            payload={"headlines": fresh[:5], "new_count": len(fresh)},  # cap Telegram burst
        ))

    async def _record(self, decision: str, confidence: float) -> None:
        from employees.engine import employee_engine
        await employee_engine.manager.record_activity(
            employee_code=self.employee_code,
            decision=decision,
            confidence=confidence,
            execution_time_ms=1.5,
        )


# Scheduled macro-economic events the Calendar employee looks for in the news.
_ECON_EVENTS: Dict[str, List[str]] = {
    "RBI Monetary Policy": ["rbi policy", "rbi monetary", "repo rate", "mpc meeting", "rbi mpc"],
    "US Fed / FOMC":       ["fomc", "fed rate", "federal reserve", "powell", "rate hike", "rate cut"],
    "Inflation (CPI/WPI)": ["cpi", "inflation", "wpi", "retail inflation"],
    "GDP Growth":          ["gdp", "growth rate", "gross domestic"],
    "Union Budget":        ["union budget", "budget 202", "sitharaman", "fiscal deficit"],
    "Earnings Season":     ["q1 results", "q2 results", "q3 results", "q4 results", "quarterly results", "earnings"],
    "PMI / IIP":           ["pmi", "iip", "industrial production", "manufacturing pmi"],
    "US Jobs / NFP":       ["nonfarm", "non-farm", "jobs data", "unemployment rate", "payroll"],
}

# High-impact categories that raise the Event-Risk employee's risk level.
_EVENT_RISK_KEYWORDS: Dict[str, List[str]] = {
    "RBI Policy":     ["rbi policy", "repo rate", "rbi mpc", "rbi monetary"],
    "US Fed / FOMC":  ["fomc", "fed rate", "federal reserve", "rate hike", "rate cut"],
    "Union Budget":   ["union budget", "budget 202", "fiscal deficit"],
    "Geopolitical":   ["war", "attack", "military", "conflict", "sanction", "ceasefire", "border"],
    "Market Shock":   ["crash", "plunge", "selloff", "sell-off", "circuit", "default", "collapse", "meltdown"],
    "Election":       ["election", "poll results", "exit poll"],
    "Inflation/GDP":  ["cpi", "inflation", "gdp"],
}
# Categories serious enough to force HIGH risk on their own.
_EVENT_RISK_SEVERE = {"Geopolitical", "Market Shock"}


def _article_text(a: Dict[str, Any]) -> str:
    return ((a.get("title") or "") + " " + (a.get("description") or "")).lower()


class EconomicCalendarEmployee(BaseNewSpecialist):
    """Surfaces real macro-economic events currently in the news (RBI policy,
    Fed/FOMC, CPI, GDP, Budget, earnings season …). Consumes the shared article
    set published by NewsEmployee — it makes no NewsAPI calls of its own."""

    def __init__(self) -> None:
        super().__init__("EMP-CAL", "WAIT")

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("market_news_articles", self._on_articles)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("market_news_articles", self._on_articles)

    async def _on_articles(self, event: EventModel) -> None:
        try:
            articles = event.payload.get("articles", []) if isinstance(event.payload, dict) else []
            results: Dict[str, Dict[str, Any]] = {}
            top_event, top_mentions = None, 0
            for ev_name, kws in _ECON_EVENTS.items():
                matched = [a for a in articles if any(k in _article_text(a) for k in kws)]
                if not matched:
                    continue
                score = _score_news_sentiment([_article_text(a) for a in matched])
                results[ev_name] = {
                    "recommendation": "WATCH",  # events warrant caution, not a directional call
                    "confidence": round(min(95.0, 50.0 + len(matched) * 8.0), 1),
                    "mentions": len(matched),
                    "latest_headline": matched[0].get("title"),
                    "source": (matched[0].get("source") or {}).get("name"),
                    "sentiment_score": round(score, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if len(matched) > top_mentions:
                    top_event, top_mentions = ev_name, len(matched)

            if not results:
                results["No major events"] = {
                    "recommendation": "WAIT",
                    "confidence": 50.0,
                    "mentions": 0,
                    "latest_headline": "No scheduled macro events detected in current news.",
                    "source": None,
                    "sentiment_score": 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            async with self._lock:
                self.latest_results = results

            from employees.engine import employee_engine
            await employee_engine.manager.record_activity(
                employee_code=self.employee_code,
                decision=("WATCH: " + top_event) if top_event else "WAIT",
                confidence=round(min(95.0, 50.0 + top_mentions * 8.0), 1) if top_event else 50.0,
                execution_time_ms=1.0,
            )
        except Exception as e:
            logger.error("Error in EconomicCalendarEmployee _on_articles", error=str(e))


class EventRiskEmployee(BaseNewSpecialist):
    """Derives a live market Event-Risk level (LOW / MEDIUM / HIGH) from the
    shared news set, and pushes a Telegram alert when risk first turns HIGH.
    Consumes NewsEmployee's articles — no NewsAPI calls of its own."""

    def __init__(self) -> None:
        super().__init__("EMP-EVR", "WAIT")
        self._last_level: Optional[str] = None

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("market_news_articles", self._on_articles)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("market_news_articles", self._on_articles)

    async def _on_articles(self, event: EventModel) -> None:
        try:
            articles = event.payload.get("articles", []) if isinstance(event.payload, dict) else []
            drivers: List[str] = []
            severe_present = False
            for cat, kws in _EVENT_RISK_KEYWORDS.items():
                if any(any(k in _article_text(a) for k in kws) for a in articles):
                    drivers.append(cat)
                    if cat in _EVENT_RISK_SEVERE:
                        severe_present = True

            high_impact = [d for d in drivers if d in _EVENT_RISK_SEVERE
                           or d in ("RBI Policy", "US Fed / FOMC", "Union Budget")]
            if severe_present or len(high_impact) >= 2:
                level, confidence, rec = "HIGH", 85.0, "REDUCE EXPOSURE / WAIT"
            elif drivers:
                level, confidence, rec = "MEDIUM", 60.0, "TRADE WITH CAUTION"
            else:
                level, confidence, rec = "LOW", 25.0, "NORMAL"

            async with self._lock:
                self.latest_results = {
                    "RISK": {
                        "risk_level": level,
                        "recommendation": rec,
                        "confidence": confidence,
                        "drivers": drivers,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                }

            from employees.engine import employee_engine
            await employee_engine.manager.record_activity(
                employee_code=self.employee_code,
                decision=f"{level} RISK",
                confidence=confidence,
                execution_time_ms=1.0,
            )

            # Alert Telegram only on the transition INTO high risk (avoids repeats).
            if level == "HIGH" and self._last_level != "HIGH":
                await event_bus.publish(EventModel(
                    event_type="event_risk_alert",
                    source_agent="event_risk_employee",
                    payload={"risk_level": level, "drivers": drivers, "recommendation": rec},
                ))
            self._last_level = level
        except Exception as e:
            logger.error("Error in EventRiskEmployee _on_articles", error=str(e))


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
            symbol = payload.get("symbol", "NIFTY50")
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
        # "paper_status_changed" is never published; PaperTradingEngine publishes
        # "paper_trade_started" / "paper_trade_stopped" for session lifecycle.
        await event_bus.subscribe("paper_trade_started", self._on_paper_status)
        await event_bus.subscribe("paper_trade_stopped", self._on_paper_status)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("paper_trade_started", self._on_paper_status)
        await event_bus.unsubscribe("paper_trade_stopped", self._on_paper_status)

    async def _on_paper_status(self, event: EventModel) -> None:
        try:
            symbol = "SYSTEM"
            is_active = event.event_type == "paper_trade_started"
            rec = "BUY" if is_active else "WAIT"
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec,
                    "confidence": 80.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in PaperTradingEmployee _on_paper_status", error=str(e))


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


# ── Shared real-analysis helpers for the segment specialists ─────────────────
def _sma(vals: List[float], n: int) -> float:
    """Simple moving average of the last n values."""
    if not vals:
        return 0.0
    window = vals[-n:]
    return sum(window) / len(window)


def _rsi_signal(closes: List[float], rsi_buy: float = 55.0, rsi_sell: float = 45.0):
    """RSI(14)-based BUY/SELL/WAIT with strength-scaled confidence.
    Returns (recommendation, confidence, rsi)."""
    if len(closes) < 15:
        return ("WAIT", 50.0, 50.0)
    rsi = calc_rsi(closes, 14)
    if rsi >= rsi_buy:
        return ("BUY", round(min(90.0, 50.0 + (rsi - 50.0) * 1.6), 1), round(rsi, 1))
    if rsi <= rsi_sell:
        return ("SELL", round(min(90.0, 50.0 + (50.0 - rsi) * 1.6), 1), round(rsi, 1))
    return ("WAIT", 50.0, round(rsi, 1))


class EquityIntradaySpecialistEmployee(BaseNewSpecialist):
    """Intraday equity signal: RSI(14) confirmed by short-vs-medium momentum
    (5-SMA vs 20-SMA). No longer a hardcoded BUY."""
    def __init__(self) -> None:
        super().__init__("EMP-EQI", "WAIT")
        self._hist: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            c = event.payload.get("candle", event.payload)
            if not c.get("complete", False):
                return
            symbol = c.get("symbol", "NIFTY50")
            h = self._hist.setdefault(symbol, [])
            h.append(float(c.get("close", 0.0)))
            if len(h) > 60:
                h.pop(0)
            rec, conf, rsi = _rsi_signal(h)
            # Require momentum agreement (5-SMA vs 20-SMA) to keep intraday signal honest
            if rec == "BUY" and _sma(h, 5) <= _sma(h, 20):
                rec, conf = "WAIT", 50.0
            elif rec == "SELL" and _sma(h, 5) >= _sma(h, 20):
                rec, conf = "WAIT", 50.0
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec, "confidence": conf, "rsi": rsi,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in EquityIntradaySpecialistEmployee _on_candle", error=str(e))


class EquitySwingSpecialistEmployee(BaseNewSpecialist):
    """Swing equity signal: 20-SMA vs 50-SMA trend crossover, confidence scaled
    by the gap between the averages. No longer a hardcoded BUY."""
    def __init__(self) -> None:
        super().__init__("EMP-EQS", "WAIT")
        self._hist: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            c = event.payload.get("candle", event.payload)
            if not c.get("complete", False):
                return
            symbol = c.get("symbol", "NIFTY50")
            h = self._hist.setdefault(symbol, [])
            h.append(float(c.get("close", 0.0)))
            if len(h) > 120:
                h.pop(0)
            if len(h) < 51:
                rec, conf, s20, s50 = "WAIT", 50.0, 0.0, 0.0
            else:
                s20, s50 = _sma(h, 20), _sma(h, 50)
                gap_pct = (abs(s20 - s50) / s50 * 100.0) if s50 else 0.0
                conf = round(min(85.0, 50.0 + gap_pct * 20.0), 1)
                rec = "BUY" if s20 > s50 else "SELL" if s20 < s50 else "WAIT"
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec, "confidence": conf,
                    "sma20": round(s20, 2), "sma50": round(s50, 2),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in EquitySwingSpecialistEmployee _on_candle", error=str(e))


class CommoditySpecialistEmployee(BaseNewSpecialist):
    """Commodity signal: RSI(14) confirmed by 5-vs-20 SMA momentum."""
    def __init__(self) -> None:
        super().__init__("EMP-COM", "WAIT")
        self._hist: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            c = event.payload.get("candle", event.payload)
            if not c.get("complete", False):
                return
            symbol = c.get("symbol", "GOLD")
            h = self._hist.setdefault(symbol, [])
            h.append(float(c.get("close", 0.0)))
            if len(h) > 60:
                h.pop(0)
            rec, conf, rsi = _rsi_signal(h)
            # Commodities trend hard — confirm RSI signal with 10-SMA slope
            if rec == "BUY" and _sma(h, 5) <= _sma(h, 20):
                rec, conf = "WAIT", 50.0
            elif rec == "SELL" and _sma(h, 5) >= _sma(h, 20):
                rec, conf = "WAIT", 50.0
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec, "confidence": conf, "rsi": rsi,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.error("Error in CommoditySpecialistEmployee _on_candle", error=str(e))


class CurrencySpecialistEmployee(BaseNewSpecialist):
    """Currency (USDINR) signal: RSI(14) confirmed by 10-vs-30 SMA trend.
    Currency moves are small, so wider SMAs and tighter RSI bands."""
    def __init__(self) -> None:
        super().__init__("EMP-CUR", "WAIT")
        self._hist: Dict[str, List[float]] = {}

    async def start(self) -> None:
        await super().start()
        await event_bus.subscribe("candle", self._on_candle)

    async def stop(self) -> None:
        await super().stop()
        await event_bus.unsubscribe("candle", self._on_candle)

    async def _on_candle(self, event: EventModel) -> None:
        try:
            c = event.payload.get("candle", event.payload)
            if not c.get("complete", False):
                return
            symbol = c.get("symbol", "USDINR")
            h = self._hist.setdefault(symbol, [])
            h.append(float(c.get("close", 0.0)))
            if len(h) > 60:
                h.pop(0)
            rec, conf, rsi = _rsi_signal(h, rsi_buy=58.0, rsi_sell=42.0)
            if rec == "BUY" and _sma(h, 10) <= _sma(h, 30):
                rec, conf = "WAIT", 50.0
            elif rec == "SELL" and _sma(h, 10) >= _sma(h, 30):
                rec, conf = "WAIT", 50.0
            async with self._lock:
                self.latest_results[symbol] = {
                    "recommendation": rec, "confidence": conf, "rsi": rsi,
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
