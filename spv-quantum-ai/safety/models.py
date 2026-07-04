from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class SafetyStatus(str, Enum):
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"

class SafetyResponse(BaseModel):
    allowed: bool
    status: SafetyStatus
    reason: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SafetyCheckPassedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_details: Dict[str, Any]
    response: SafetyResponse

class SafetyBlockedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_details: Dict[str, Any]
    response: SafetyResponse

class EmergencyTriggeredEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str  # e.g., "kill_switch", "pause", "close_all"
    message: str

class HiddenStopTriggeredEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    side: str
    quantity: float
    trigger_price: float
    exit_price: float
    message: str

class TrailingUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    old_stop_price: float
    new_stop_price: float
    current_price: float
    reason: str
