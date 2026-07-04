from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

class PaperTradingConfig(BaseModel):
    initial_capital: float = 1000000.0
    latency_ms: float = 50.0
    slippage_pct: float = 0.0005
    spread_pct: float = 0.0002
    start_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class PaperTradingState(BaseModel):
    session_id: str
    is_running: bool = False
    virtual_capital: float = 1000000.0
    virtual_pnl: float = 0.0
    trades_executed: int = 0
    win_rate: float = 0.0
    current_positions: int = 0

class PaperTradeStartedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str
    config: PaperTradingConfig

class PaperOrderPlacedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float

class PaperOrderFilledEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    latency_ms: float

class PaperTradeClosedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str
    symbol: str
    pnl: float
    duration: float

class PaperTradingStoppedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: str
