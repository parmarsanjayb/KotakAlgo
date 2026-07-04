from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from market.models import Timeframe
import uuid

class IndicatorEvent(BaseModel):
    """Standard event published for every indicator calculation result."""
    event_id:       str = Field(default_factory=lambda: uuid.uuid4().hex)
    indicator_name: str
    symbol:         str
    timeframe:      Timeframe
    timestamp:      datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    value:          Any                    # float, dict, or list depending on indicator
    metadata:       Dict[str, Any] = Field(default_factory=dict)

class IndicatorResult(BaseModel):
    """Internal computation result before publishing."""
    indicator_name: str
    symbol:         str
    timeframe:      Timeframe
    timestamp:      datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    value:          Any
    calc_time_ms:   float = 0.0
    metadata:       Dict[str, Any] = Field(default_factory=dict)

class PivotPoints(BaseModel):
    pivot: float
    r1: float; r2: float; r3: float
    s1: float; s2: float; s3: float

class BollingerBands(BaseModel):
    upper: float
    middle: float
    lower: float
    bandwidth: float

class MACDResult(BaseModel):
    macd_line:    float
    signal_line:  float
    histogram:    float

class ADXResult(BaseModel):
    adx:    float
    di_pos: float
    di_neg: float

class StochRSIResult(BaseModel):
    k: float
    d: float
