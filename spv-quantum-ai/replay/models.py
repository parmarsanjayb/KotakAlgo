from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

class ReplayConfig(BaseModel):
    symbols: List[str]
    timeframe: str = "1m"
    start_date: datetime
    end_date: datetime
    speed: str = "10x"  # 1x, 2x, 5x, 10x, 25x, 50x, 100x, Unlimited
    mode: str = "Full Trading System"  # Market Only, Market + Scanner, Full Trading System
    initial_capital: float = 100000.0

class ReplayState(BaseModel):
    replay_id: str
    status: str = "PENDING"  # PENDING, PLAYING, PAUSED, STOPPED, COMPLETED
    speed: str = "10x"
    mode: str = "Full Trading System"
    current_index: int = 0
    total_candles: int = 0
    current_symbol: Optional[str] = None
    current_time: Optional[datetime] = None
    progress_pct: float = 0.0

class ReplayStartedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replay_id: str
    config: ReplayConfig

class ReplayPausedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replay_id: str
    current_index: int

class ReplayResumedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replay_id: str

class ReplayStoppedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replay_id: str

class ReplayCompletedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replay_id: str
    state: ReplayState
