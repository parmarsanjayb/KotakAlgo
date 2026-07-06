from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class EmployeeState(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    TRAINING = "TRAINING"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_TRADING = "LIVE_TRADING"
    DISABLED = "DISABLED"

class EmployeeType(str, Enum):
    OPTIONS_SPECIALIST = "Options Specialist"
    EQUITY_INTRADAY = "Equity Intraday Specialist"
    EQUITY_SWING = "Equity Swing Specialist"
    DELIVERY_INVESTOR = "Delivery Investor"
    COMMODITY_SPECIALIST = "Commodity Specialist"
    CURRENCY_SPECIALIST = "Currency Specialist"
    PORTFOLIO_MANAGER = "Portfolio Manager"
    VOLUME_INTELLIGENCE = "Volume Intelligence Specialist"
    OPTION_FLOW = "Option Flow Specialist"
    TREND_INTELLIGENCE = "Trend Intelligence Specialist"
    
    # Market Intelligence Department
    MOMENTUM = "Momentum Employee"
    VWAP = "VWAP Employee"
    MARKET_REGIME = "Market Regime Employee"
    
    # Options Intelligence Department
    OPTION_OI = "OI Employee"
    OPTION_PCR = "PCR Employee"
    OPTION_GREEKS = "Greeks Employee"
    OPTION_MAX_PAIN = "Max Pain Employee"
    
    # Institutional Department
    INSTITUTIONAL_SMART_MONEY = "Smart Money Employee"
    INSTITUTIONAL_LIQUIDITY = "Liquidity Employee"
    INSTITUTIONAL_ORDER_FLOW = "Order Flow Employee"
    
    # Risk Department
    RISK_MONITOR = "Risk Employee"
    RISK_POSITION_SIZING = "Position Sizing Employee"
    RISK_CAPITAL_PROTECTION = "Capital Protection Employee"
    RISK_EXPOSURE = "Exposure Employee"
    
    # News Department
    NEWS_SENTIMENT = "News Employee"
    ECONOMIC_CALENDAR = "Economic Calendar Employee"
    EVENT_RISK = "Event Risk Employee"
    
    # Execution Department
    EXECUTION = "Execution Employee"
    PORTFOLIO = "Portfolio Employee"
    PAPER_TRADING = "Paper Trading Employee"
    
    CUSTOM = "Custom Employee"

class EmployeeProfile(BaseModel):
    # Metadata
    employee_code: str
    name: str
    avatar: str
    description: str
    state: EmployeeState = EmployeeState.ACTIVE
    employee_type: EmployeeType = EmployeeType.CUSTOM

    # Allowed Rules (Identity Configurations)
    allowed_segments: List[str] = Field(default_factory=lambda: ["Equity"])
    allowed_products: List[str] = Field(default_factory=lambda: ["MIS"])
    allowed_timeframes: List[str] = Field(default_factory=lambda: ["1m", "5m"])
    allowed_strategies: List[str] = Field(default_factory=list)
    allowed_risk_profiles: List[str] = Field(default_factory=lambda: ["conservative"])
    
    # Capital Safety & Limits
    max_open_trades: int = 5
    max_exposure: float = 10000.0
    max_daily_loss: float = 200.0
    max_daily_profit: float = 1000.0
    trading_sessions: List[str] = Field(default_factory=lambda: ["09:15-15:30"])
    holiday_rules: List[str] = Field(default_factory=list)
    capital_allocation: float = 100000.0
    confidence_threshold: float = 60.0
    
    # Filters & Toggles
    enable_news_filter: bool = True
    enable_regime_filter: bool = True
    enable_indicators: List[str] = Field(default_factory=list)
    enable_strategy_groups: List[str] = Field(default_factory=list)

    # Runtime / Live performance stats (Maintained per employee)
    pnl: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    trade_history: List[Dict[str, Any]] = Field(default_factory=list)
    strategy_pnl: Dict[str, float] = Field(default_factory=dict)
    
    # Monitoring Metrics
    is_active: bool = True
    health_status: str = "HEALTHY"
    last_decision: str = "NONE"
    last_decision_confidence: float = 0.0
    total_signals: int = 0
    correct_signals: int = 0
    incorrect_signals: int = 0
    accuracy_pct: float = 100.0
    last_execution_time_ms: float = 0.0
    avg_execution_time_ms: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None
    heartbeat_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    accuracy_history: List[Dict[str, Any]] = Field(default_factory=list)

    tenant_id: Optional[str] = None  # Future SaaS/multi-tenant support

class EmployeeActivatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    employee_code: str
    name: str

class EmployeePausedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    employee_code: str
    name: str

class EmployeeProfileUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    employee_code: str
    profile_updates: Dict[str, Any]

class EmployeeCapitalUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    employee_code: str
    allocated_capital: float
