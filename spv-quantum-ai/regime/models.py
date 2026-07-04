from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from market.models import Timeframe
import uuid


class MarketRegime(str, Enum):
    TRENDING_BULLISH    = "TRENDING_BULLISH"
    TRENDING_BEARISH    = "TRENDING_BEARISH"
    SIDEWAYS            = "SIDEWAYS"
    RANGE_BOUND         = "RANGE_BOUND"
    HIGH_VOLATILITY     = "HIGH_VOLATILITY"
    LOW_VOLATILITY      = "LOW_VOLATILITY"
    BREAKOUT            = "BREAKOUT"
    BREAKDOWN           = "BREAKDOWN"
    REVERSAL            = "REVERSAL"
    NEWS_DRIVEN         = "NEWS_DRIVEN"
    GAP_UP              = "GAP_UP"
    GAP_DOWN            = "GAP_DOWN"
    UNKNOWN             = "UNKNOWN"


class RegimeResult(BaseModel):
    """Internal classification result produced by the RegimeClassifier."""
    symbol:             str
    timeframe:          Timeframe
    market_regime:      MarketRegime
    confidence:         float           # 0.0 – 100.0
    reason:             str
    supporting_factors: Dict[str, Any]  = Field(default_factory=dict)
    timestamp:          datetime        = Field(default_factory=lambda: datetime.now(timezone.utc))


class MarketRegimeEvent(BaseModel):
    """Canonical event published on the Event Bus for every regime update."""
    event_id:           str      = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp:          datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol:             str
    timeframe:          Timeframe
    market_regime:      MarketRegime
    confidence:         float
    reason:             str
    supporting_factors: Dict[str, Any] = Field(default_factory=dict)
