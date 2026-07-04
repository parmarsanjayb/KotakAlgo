from enum import Enum
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class OrderProductType(str, Enum):
    MIS  = "MIS"
    CNC  = "CNC"
    NRML = "NRML"

class OrderLifecycleStatus(str, Enum):
    NEW              = "NEW"
    VALIDATED        = "VALIDATED"
    QUEUED           = "QUEUED"
    SENT             = "SENT"
    ACKNOWLEDGED     = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED           = "FILLED"
    MODIFIED         = "MODIFIED"
    CANCELLED        = "CANCELLED"
    REJECTED         = "REJECTED"
    FAILED           = "FAILED"

class ExecutionOrder(BaseModel):
    order_id: str = Field(default_factory=lambda: f"ORD-{uuid.uuid4().hex[:8]}")
    symbol: str
    side: str  # BUY, SELL
    order_type: str  # MARKET, LIMIT, SL, SL-M
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    product_type: OrderProductType = OrderProductType.MIS
    status: OrderLifecycleStatus = OrderLifecycleStatus.NEW
    broker_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    client_tag: Optional[str] = None
    retry_count: int = 0
    broker_latency_ms: float = 0.0

class ExecutionRequestEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder

class OrderSubmittedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder

class OrderFilledEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder

class OrderRejectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder
    reason: str

class OrderCancelledEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder

class ExecutionFailedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order: ExecutionOrder
    error_message: str
