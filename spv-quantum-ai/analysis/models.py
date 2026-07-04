from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import uuid

class MarketAnalysisReport(BaseModel):
    symbol: str
    timeframe: str
    market_bias: str            # BULLISH, BEARISH, NEUTRAL
    trend_strength: str         # STRONG, WEAK, NONE
    momentum: str               # BULLISH, BEARISH, FLAT
    volatility: str             # HIGH, LOW, NORMAL
    market_structure: str       # TRENDING, RANGE_BOUND, BREAKOUT, etc.
    support: float
    resistance: float
    recommended_strategy: str
    confidence: float           # 0.0 - 100.0
    reasoning: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MarketAnalysisEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    report: MarketAnalysisReport
