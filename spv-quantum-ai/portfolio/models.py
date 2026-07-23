from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class PositionState(str, Enum):
    OPEN      = "OPEN"
    PARTIAL   = "PARTIAL"
    CLOSED    = "CLOSED"
    CANCELLED = "CANCELLED"
    EXPIRED   = "EXPIRED"

class Position(BaseModel):
    symbol: str
    segment: str  # Equity, Equity Delivery, Futures, Options, Commodity, Currency
    side: str  # BUY, SELL (representing the net position side: LONG or SHORT)
    quantity: float
    avg_price: float
    ltp: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    state: PositionState = PositionState.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str = "admin"

class PortfolioSummary(BaseModel):
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    mtm: float = 0.0
    available_capital: float = 0.0
    utilized_margin: float = 0.0
    portfolio_exposure: float = 0.0
    segment_distribution: Dict[str, float] = Field(default_factory=dict)
    sector_distribution: Dict[str, float] = Field(default_factory=dict)
    broker_distribution: Dict[str, float] = Field(default_factory=dict)

# Events
class PortfolioUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: PortfolioSummary

class PositionOpenedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    position: Position

class PositionUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    position: Position

class PositionClosedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    position: Position

class PnLUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    realized_pnl: float
    unrealized_pnl: float
    mtm: float

class ExposureUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    portfolio_exposure: float
    segment_exposure: Dict[str, float]
