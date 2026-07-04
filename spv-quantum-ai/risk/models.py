from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class RiskStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REDUCE_POSITION = "REDUCE_POSITION"

class RiskResponse(BaseModel):
    risk_status: RiskStatus
    allowed: bool
    reason: str
    risk_score: float  # e.g., 0.0 to 100.0
    recommended_position_size: float
    recommended_max_loss: float

class RiskApprovedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_details: Dict[str, Any]
    risk_response: RiskResponse

class RiskRejectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_details: Dict[str, Any]
    risk_response: RiskResponse

class DrawdownAlertEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_drawdown: float
    max_drawdown_limit: float
    message: str

class DailyLossLimitEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    daily_loss: float
    daily_loss_limit: float
    message: str

class PositionSizeAdjustedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    original_size: float
    adjusted_size: float
    reason: str
