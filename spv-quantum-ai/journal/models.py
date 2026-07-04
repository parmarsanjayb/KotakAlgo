from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
import uuid

class TradeRecord(BaseModel):
    trade_id: str = Field(default_factory=lambda: f"TRD-{uuid.uuid4().hex[:8]}")
    order_id: str
    broker_order_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exchange: str = "NSE"
    segment: str = "Equity"  # Equity, Equity Delivery, Futures, Options, Commodity, Currency
    instrument: str = "STOCK"
    symbol: str
    side: str = "BUY"
    strategy_name: Optional[str] = None
    scanner_name: Optional[str] = None
    market_regime: Optional[str] = None
    decision_score: Optional[float] = None
    risk_score: Optional[float] = None
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    order_type: str = "MARKET"
    product_type: str = "MIS"
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    trailing_stop: Optional[float] = None
    reason: Optional[str] = None
    execution_latency: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    charges: float = 0.0
    taxes: float = 0.0
    realized_pnl: float = 0.0
    holding_duration: float = 0.0  # In seconds
    entry_cost: float = 0.0
    exit_cost: float = 0.0
    total_charges: float = 0.0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    breakeven_price: float = 0.0
    cost_pct: float = 0.0

class DecisionAudit(BaseModel):
    audit_id: str = Field(default_factory=lambda: f"AUD-{uuid.uuid4().hex[:8]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    market_analysis_summary: Optional[str] = None
    indicator_snapshot: Dict[str, Any] = Field(default_factory=dict)
    strategy_match: Optional[str] = None
    risk_validation: Optional[str] = None
    decision_confidence: float = 0.0
    execution_result: Optional[str] = None

# Events
class TradeRecordedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trade: TradeRecord

class TradeUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trade: TradeRecord

class TradeClosedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trade: TradeRecord

class JournalUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entry_id: int
    entry_type: str
