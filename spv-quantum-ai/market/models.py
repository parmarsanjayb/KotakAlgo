from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

# ── Enums ────────────────────────────────────────────────────────────────────

class Timeframe(str, Enum):
    M1  = "1m"
    M3  = "3m"
    M5  = "5m"
    M15 = "15m"
    M30 = "30m"
    H1  = "1H"
    H4  = "4H"
    D1  = "1D"

TIMEFRAME_SECONDS: Dict[Timeframe, int] = {
    Timeframe.M1:  60,
    Timeframe.M3:  180,
    Timeframe.M5:  300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1:  3600,
    Timeframe.H4:  14400,
    Timeframe.D1:  86400,
}

class FeedStatus(str, Enum):
    CONNECTED    = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED     = "DEGRADED"
    RECOVERING   = "RECOVERING"

class MarketSession(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN     = "OPEN"
    CLOSED   = "CLOSED"
    HALTED   = "HALTED"

# ── Core MarketData ───────────────────────────────────────────────────────────

class MarketDepthLevel(BaseModel):
    """Interface for Level 2 order book depth."""
    price:    float
    quantity: float
    orders:   int = 0

class MarketDepth(BaseModel):
    """Bid/Ask order book depth (prepare interface)."""
    bids: List[MarketDepthLevel] = Field(default_factory=list)
    asks: List[MarketDepthLevel] = Field(default_factory=list)

class MarketData(BaseModel):
    """
    Unified canonical model. Every module must consume/produce this exact object.
    No agent, broker, or strategy may define its own tick structure.
    """
    symbol:       str
    timestamp:    datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ltp:          float = 0.0
    bid:          float = 0.0
    ask:          float = 0.0
    volume:       float = 0.0
    open_interest: float = 0.0
    vwap:         float = 0.0
    atp:          float = 0.0
    open:         float = 0.0
    high:         float = 0.0
    low:          float = 0.0
    close:        float = 0.0
    prev_close:   float = 0.0
    depth:        Optional[MarketDepth] = None

class Candle(BaseModel):
    """Standard OHLCV candle for any timeframe."""
    symbol:     str
    timeframe:  Timeframe
    timestamp:  datetime
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    vwap:       float = 0.0
    complete:   bool = False

# ── Option Chain ──────────────────────────────────────────────────────────────

class OptionGreeks(BaseModel):
    """Greeks interface — values computed by separate strategy layer, not here."""
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega:  float = 0.0
    rho:   float = 0.0
    iv:    float = 0.0

class OptionContract(BaseModel):
    strike:       float
    option_type:  str       # CE | PE
    ltp:          float = 0.0
    bid:          float = 0.0
    ask:          float = 0.0
    volume:       float = 0.0
    open_interest: float = 0.0
    greeks:       OptionGreeks = Field(default_factory=OptionGreeks)

class OptionChain(BaseModel):
    underlying:       str
    underlying_price: float
    expiry:           str
    contracts:        List[OptionContract] = Field(default_factory=list)
    timestamp:        datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# ── Standard Events ───────────────────────────────────────────────────────────

class TickEvent(BaseModel):
    event_id:  str = Field(default_factory=lambda: uuid.uuid4().hex)
    tick:      MarketData

class CandleEvent(BaseModel):
    event_id:  str = Field(default_factory=lambda: uuid.uuid4().hex)
    candle:    Candle

class MarketOpenEvent(BaseModel):
    event_id:  str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MarketCloseEvent(BaseModel):
    event_id:  str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MarketStatusChangedEvent(BaseModel):
    event_id:   str = Field(default_factory=lambda: uuid.uuid4().hex)
    old_status: MarketSession
    new_status: MarketSession
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FeedDisconnectedEvent(BaseModel):
    event_id:  str = Field(default_factory=lambda: uuid.uuid4().hex)
    reason:    str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FeedConnectedEvent(BaseModel):
    event_id:    str = Field(default_factory=lambda: uuid.uuid4().hex)
    broker_name: str = ""
    timestamp:   datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class OptionChainUpdatedEvent(BaseModel):
    event_id:    str = Field(default_factory=lambda: uuid.uuid4().hex)
    option_chain: OptionChain
    timestamp:   datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
