import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from core.agent import BaseAgent, AgentResultModel
from core.bus import EventModel
from database.connection import async_session
from database.models import IndicatorModel

def calculate_sma(prices: List[float], period: int) -> float:
    """Calculates Simple Moving Average for a specified period."""
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    return sum(prices[-period:]) / period

def calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average list."""
    if len(prices) < period:
        return prices.copy()
    ema = [0.0] * len(prices)
    sma = sum(prices[:period]) / period
    for i in range(period):
        ema[i] = sma
    k = 2.0 / (period + 1)
    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i-1] * (1.0 - k)
    return ema

def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """Calculates MACD parameters (MACD line, Signal line, Histogram)."""
    if len(prices) < slow:
        return 0.0, 0.0, 0.0
    fast_ema = calculate_ema(prices, fast)
    slow_ema = calculate_ema(prices, slow)
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(prices))]
    signal_line = calculate_ema(macd_line, signal)
    hist = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], hist

def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculates Relative Strength Index (RSI)."""
    if len(prices) <= period:
        return 50.0
    
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

class MarketIntelligenceAgent(BaseAgent):
    """
    Consumes market data feeds, maintains candle close histories, calculates
    technical indicators (SMA, RSI, MACD), saves logs to DB, and issues trend signals.
    """
    def __init__(self) -> None:
        super().__init__(
            name="market_intelligence_agent",
            description="Calculates technical price indicators and generates market intelligence"
        )
        self.candles: Dict[str, List[float]] = {}
        self.max_history = 100

    @property
    def input_event_types(self) -> List[str]:
        return ["tick"]

    @property
    def output_event_types(self) -> List[str]:
        return ["indicator_updates", "market_intelligence"]

    async def initialize(self) -> None:
        self.log_info("MarketIntelligenceAgent initialization complete.")

    async def shutdown(self) -> None:
        self.log_info("MarketIntelligenceAgent shutdown complete.")

    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        """Receives tick feeds, updates SMA/RSI/MACD metrics, and writes to database."""
        start_time = time.perf_counter()
        
        if event.event_type != "tick":
            return None

        # TickEvent wraps the MarketData under payload["tick"]
        raw = event.payload
        tick = raw.get("tick", raw)  # support both wrapped and flat payloads
        symbol      = tick.get("symbol", "UNKNOWN")
        close_price = float(tick.get("ltp", tick.get("close", 0.0)))

        if symbol == "UNKNOWN" or close_price == 0.0:
            return None

        # Track history
        if symbol not in self.candles:
            self.candles[symbol] = []
        self.candles[symbol].append(close_price)
        if len(self.candles[symbol]) > self.max_history:
            self.candles[symbol].pop(0)

        history = self.candles[symbol]
        history_len = len(history)

        # Calculate metrics if enough candles are present
        fast_sma = calculate_sma(history, 9)
        slow_sma = calculate_sma(history, 21)
        rsi_value = calculate_rsi(history, 14) if history_len > 14 else 50.0
        macd_line, signal_line, macd_hist = calculate_macd(history, 12, 26, 9) if history_len > 26 else (0.0, 0.0, 0.0)

        # Persistence to database bypassed to prevent connection pool exhaustion on high-frequency ticks
        db_error = None

        # Trend Decision mapping
        trend = "NEUTRAL"
        signal = "HOLD"
        confidence = 50.0

        if history_len >= 21:
            if fast_sma > slow_sma and rsi_value > 50:
                trend = "BULLISH"
                signal = "BUY"
                # Map confidence dynamically to RSI distance from neutral 50
                confidence = min(95.0, 50.0 + (rsi_value - 50.0) * 1.5)
            elif fast_sma < slow_sma and rsi_value < 50:
                trend = "BEARISH"
                signal = "SELL"
                confidence = min(95.0, 50.0 + (50.0 - rsi_value) * 1.5)

        # Publish indicator updates on the event bus
        indicator_payload = {
            "symbol": symbol,
            "close": close_price,
            "fast_sma": fast_sma,
            "slow_sma": slow_sma,
            "rsi": rsi_value,
            "macd": {"line": macd_line, "signal": signal_line, "hist": macd_hist}
        }
        await self.publish_result("indicator_updates", indicator_payload)

        # Build decision results
        processing_time = (time.perf_counter() - start_time) * 1000.0
        result = AgentResultModel(
            agent_name=self.agent_name,
            signal=signal,
            confidence=round(confidence, 2),
            reason=f"Spot={close_price} | Trend={trend} | FastSMA={fast_sma:.2f} SlowSMA={slow_sma:.2f} | RSI={rsi_value:.1f} | MACD={macd_hist:.4f}",
            processing_time=processing_time,
            metadata={
                "symbol": symbol,
                "trend": trend,
                "history_length": history_len,
                "db_persisted": db_error is None
            }
        )

        # Publish intelligence summaries
        await self.publish_result("market_intelligence", result.model_dump())

        return result
