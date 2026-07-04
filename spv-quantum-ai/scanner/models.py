from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid

class ScanResult(BaseModel):
    symbol: str
    exchange: str
    segment: str
    scanner_name: str
    priority: int
    confidence: float
    matched_conditions: List[str] = Field(default_factory=list)
    scan_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ScannerEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scan_result: ScanResult

class ScannerConfig(BaseModel):
    name: str
    enabled: bool = True
    segment: str  # Equity, Equity Futures, Stock Options, Index Futures, Index Options, Commodity, Currency
    filter_type: str  # VolumeSpike, PriceBreakout, GapUp, GapDown, HighRelativeVolume, HighOIChange, VWAPDeviation, ATRExpansion, 52WeekHigh, 52WeekLow, OpeningRangeBreak, MomentumExpansion
    params: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 2
