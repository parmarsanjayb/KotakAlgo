from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

class ChargesConfig(BaseModel):
    enabled_charges: Dict[str, bool] = Field(default_factory=lambda: {
        "brokerage": True,
        "stt": True,
        "exchange_txn": True,
        "sebi": True,
        "gst": True,
        "stamp_duty": True,
        "dp_charges": True
    })

class BrokerageRules(BaseModel):
    intraday_rate: float = 0.0003  # 0.03%
    intraday_max: float = 20.0
    delivery_rate: float = 0.0000  # Kotak Neo offers free delivery trades
    delivery_max: float = 0.0
    futures_rate: float = 0.0003  # 0.03%
    futures_max: float = 20.0
    options_rate: float = 0.0  # flat brokerage
    options_max: float = 20.0  # Rs. 20 flat per order
    commodity_rate: float = 0.0003
    commodity_max: float = 20.0
    currency_rate: float = 0.0003
    currency_max: float = 20.0

class TaxRules(BaseModel):
    stt_equity_intraday_sell: float = 0.00025  # 0.025%
    stt_equity_delivery_buy_sell: float = 0.001  # 0.1%
    stt_futures_sell: float = 0.000125  # 0.0125%
    stt_options_sell: float = 0.000625  # 0.0625% on premium
    gst_rate: float = 0.18  # 18% on Brokerage + Exchange Txn Charges
    stamp_duty_equity_intraday_buy: float = 0.00003  # 0.003%
    stamp_duty_equity_delivery_buy: float = 0.00015  # 0.015%
    stamp_duty_futures_buy: float = 0.00002  # 0.002%
    stamp_duty_options_buy: float = 0.00003  # 0.003%
    sebi_charges_rate: float = 0.000001  # Rs 10 per crore (0.000001)

class ExchangeChargeRules(BaseModel):
    exchange_txn_equity_intraday: float = 0.0000345  # 0.00345%
    exchange_txn_equity_delivery: float = 0.0000345
    exchange_txn_futures: float = 0.00002
    exchange_txn_options: float = 0.00053  # 0.053%
    dp_charges_delivery_sell: float = 13.5  # flat DP charges on delivery sell

class BrokerProfile(BaseModel):
    name: str
    brokerage_rules: BrokerageRules = Field(default_factory=BrokerageRules)
    tax_rules: TaxRules = Field(default_factory=TaxRules)
    exchange_charge_rules: ExchangeChargeRules = Field(default_factory=ExchangeChargeRules)
    charges_config: ChargesConfig = Field(default_factory=ChargesConfig)

class TradeChargesBreakdown(BaseModel):
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    gst: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    dp_charges: float = 0.0
    total_charges: float = 0.0
    entry_cost: float = 0.0
    exit_cost: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    breakeven_price: float = 0.0
    cost_pct: float = 0.0

# Event definitions
class ChargesCalculatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    breakdown: TradeChargesBreakdown

class TradeCostUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trade_id: str
    symbol: str
    breakdown: TradeChargesBreakdown

class NetPnLUpdatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    net_pnl: float
    gross_pnl: float
    total_charges: float
