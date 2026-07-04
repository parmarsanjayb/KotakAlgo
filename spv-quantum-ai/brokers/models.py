from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import uuid

class OrderStatus(str, Enum):
    NEW             = "NEW"
    OPEN            = "OPEN"
    FILLED          = "FILLED"
    PARTIAL         = "PARTIAL"
    CANCELLED       = "CANCELLED"
    REJECTED        = "REJECTED"
    TRIGGER_PENDING = "TRIGGER_PENDING"

class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    SL     = "SL"
    SLM    = "SLM"

class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"

class Order(BaseModel):
    order_id:         str
    broker_order_id:  Optional[str] = None
    symbol:           str
    side:             OrderSide
    order_type:       OrderType = OrderType.MARKET
    quantity:         float
    price:            Optional[float] = None
    trigger_price:    Optional[float] = None
    filled_quantity:  float = 0.0
    avg_price:        float = 0.0
    status:           OrderStatus = OrderStatus.NEW
    broker:           str = ""
    tag:              Optional[str] = None
    placed_at:        datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:       datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reject_reason:    Optional[str] = None

class Position(BaseModel):
    symbol:           str
    side:             OrderSide
    quantity:         float
    avg_price:        float
    ltp:              float = 0.0
    unrealised_pnl:   float = 0.0
    realised_pnl:     float = 0.0
    broker:           str = ""

class Holding(BaseModel):
    symbol:           str
    quantity:         float
    avg_price:        float
    ltp:              float = 0.0
    current_value:    float = 0.0
    pnl:              float = 0.0
    broker:           str = ""

class Funds(BaseModel):
    equity:           float
    available_margin: float
    used_margin:      float
    broker:           str = ""

class Trade(BaseModel):
    trade_id:         str
    order_id:         str
    symbol:           str
    side:             OrderSide
    quantity:         float
    price:            float
    commission:       float = 0.0
    executed_at:      datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker:           str = ""

class BrokerResponse(BaseModel):
    success:          bool
    broker:           str
    data:             Optional[Any] = None
    error:            Optional[str] = None
    latency_ms:       float = 0.0

class BrokerState(str, Enum):
    CONNECTED    = "CONNECTED"
    CONNECTING   = "CONNECTING"
    DISCONNECTED = "DISCONNECTED"
    FAILED       = "FAILED"
    RECONNECTING = "RECONNECTING"

class BrokerConnectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    message: str = "Connected successfully"

class BrokerDisconnectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    message: str = "Disconnected"

class BrokerOrderPlacedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    order: Order

class BrokerOrderModifiedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    order: Order

class BrokerOrderCancelledEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    order: Order

class BrokerHealthChangedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str
    is_healthy: bool
    latency_ms: float
    error: Optional[str] = None

class KotakConnectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    message: str = "Kotak session connected"

class KotakDisconnectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    message: str = "Kotak session disconnected"

class KotakOrderPlacedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    order: Order

class KotakOrderFilledEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    order: Order

class KotakOrderRejectedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    order: Order
    reason: str

class KotakSessionExpiredEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    broker: str = "kotak_neo"
    message: str = "Kotak session expired"
