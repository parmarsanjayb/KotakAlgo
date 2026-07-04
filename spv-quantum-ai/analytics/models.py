from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

class PerformanceMetrics(BaseModel):
    gross_profit: float = 0.0
    net_profit: float = 0.0
    total_charges: float = 0.0
    brokerage: float = 0.0
    taxes: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_holding_time: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_r_multiple: float = 0.0
    max_drawdown: float = 0.0
    max_consec_wins: int = 0
    max_consec_losses: int = 0
    recovery_factor: float = 0.0
    risk_reward_ratio: float = 0.0
    avg_slippage: float = 0.0
    avg_execution_latency: float = 0.0
    capital_utilization: float = 0.0
    return_on_capital: float = 0.0
    segment_performance: Dict[str, float] = Field(default_factory=dict)
    strategy_performance: Dict[str, float] = Field(default_factory=dict)
    broker_performance: Dict[str, float] = Field(default_factory=dict)

class PerformanceReport(BaseModel):
    report_id: str = Field(default_factory=lambda: f"REP-{uuid.uuid4().hex[:8]}")
    report_type: str  # Daily, Weekly, Monthly, Yearly, Strategy, Segment, Portfolio
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metrics: PerformanceMetrics
    equity_curve: List[float] = Field(default_factory=list)
    drawdown_curve: List[float] = Field(default_factory=list)

class PerformanceUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metrics: PerformanceMetrics

class DailyReportGeneratedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    report: PerformanceReport

class MonthlyReportGeneratedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    report: PerformanceReport
