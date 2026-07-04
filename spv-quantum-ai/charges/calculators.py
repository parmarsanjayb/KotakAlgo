from typing import Dict, Any
from charges.models import BrokerProfile, TradeChargesBreakdown

class BrokerageCalculator:
    """Calculates brokerage charges according to active broker profile rules and trade segment."""
    @staticmethod
    def calculate(profile: BrokerProfile, segment: str, side: str, qty: float, price: float) -> float:
        if not profile.charges_config.enabled_charges.get("brokerage", True):
            return 0.0
            
        rules = profile.brokerage_rules
        turnover = qty * price
        seg_lower = segment.lower()
        
        if "delivery" in seg_lower:
            rate = rules.delivery_rate
            max_val = rules.delivery_max
        elif "intraday" in seg_lower or seg_lower == "equity":
            rate = rules.intraday_rate
            max_val = rules.intraday_max
        elif "future" in seg_lower:
            rate = rules.futures_rate
            max_val = rules.futures_max
        elif "option" in seg_lower:
            rate = rules.options_rate
            max_val = rules.options_max
            # Options brokerage is typically flat per executed order/fill
            if rate == 0.0:
                return max_val
        elif "commodity" in seg_lower:
            rate = rules.commodity_rate
            max_val = rules.commodity_max
        elif "currency" in seg_lower:
            rate = rules.currency_rate
            max_val = rules.currency_max
        else:
            # Fallback to intraday
            rate = rules.intraday_rate
            max_val = rules.intraday_max
            
        calc = turnover * rate
        if max_val > 0:
            return min(calc, max_val)
        return calc

class TaxCalculator:
    """Calculates STT, GST, SEBI Turnover, and Stamp Duty based on dynamic tax rules."""
    @staticmethod
    def calculate_stt(profile: BrokerProfile, segment: str, side: str, qty: float, price: float) -> float:
        if not profile.charges_config.enabled_charges.get("stt", True):
            return 0.0
            
        rules = profile.tax_rules
        turnover = qty * price
        side_upper = side.upper()
        seg_lower = segment.lower()
        
        if "delivery" in seg_lower:
            # STT applies on both buy and sell for Delivery
            return turnover * rules.stt_equity_delivery_buy_sell
        elif "intraday" in seg_lower or seg_lower == "equity":
            # STT applies on sell side only for Intraday
            if side_upper == "SELL":
                return turnover * rules.stt_equity_intraday_sell
        elif "future" in seg_lower:
            # STT applies on sell side only for Futures
            if side_upper == "SELL":
                return turnover * rules.stt_futures_sell
        elif "option" in seg_lower:
            # STT applies on sell side premium for Options
            if side_upper == "SELL":
                return turnover * rules.stt_options_sell
        return 0.0

    @staticmethod
    def calculate_stamp_duty(profile: BrokerProfile, segment: str, side: str, qty: float, price: float) -> float:
        if not profile.charges_config.enabled_charges.get("stamp_duty", True):
            return 0.0
            
        # Stamp duty applies to buy side only
        if side.upper() != "BUY":
            return 0.0
            
        rules = profile.tax_rules
        turnover = qty * price
        seg_lower = segment.lower()
        
        if "delivery" in seg_lower:
            return turnover * rules.stamp_duty_equity_delivery_buy
        elif "intraday" in seg_lower or seg_lower == "equity":
            return turnover * rules.stamp_duty_equity_intraday_buy
        elif "future" in seg_lower:
            return turnover * rules.stamp_duty_futures_buy
        elif "option" in seg_lower:
            return turnover * rules.stamp_duty_options_buy
        return 0.0

    @staticmethod
    def calculate_sebi_charges(profile: BrokerProfile, qty: float, price: float) -> float:
        if not profile.charges_config.enabled_charges.get("sebi", True):
            return 0.0
        return qty * price * profile.tax_rules.sebi_charges_rate

    @staticmethod
    def calculate_gst(profile: BrokerProfile, brokerage: float, exchange_txn: float) -> float:
        if not profile.charges_config.enabled_charges.get("gst", True):
            return 0.0
        return (brokerage + exchange_txn) * profile.tax_rules.gst_rate

class ExchangeChargeCalculator:
    """Calculates Exchange Transaction charges and DP delivery charges."""
    @staticmethod
    def calculate_exchange_txn(profile: BrokerProfile, segment: str, qty: float, price: float) -> float:
        if not profile.charges_config.enabled_charges.get("exchange_txn", True):
            return 0.0
            
        rules = profile.exchange_charge_rules
        turnover = qty * price
        seg_lower = segment.lower()
        
        if "delivery" in seg_lower:
            return turnover * rules.exchange_txn_equity_delivery
        elif "intraday" in seg_lower or seg_lower == "equity":
            return turnover * rules.exchange_txn_equity_intraday
        elif "future" in seg_lower:
            return turnover * rules.exchange_txn_futures
        elif "option" in seg_lower:
            return turnover * rules.exchange_txn_options
        return turnover * rules.exchange_txn_equity_intraday

    @staticmethod
    def calculate_dp_charges(profile: BrokerProfile, segment: str, side: str) -> float:
        if not profile.charges_config.enabled_charges.get("dp_charges", True):
            return 0.0
            
        # DP charges apply on delivery sell transactions
        if "delivery" in segment.lower() and side.upper() == "SELL":
            return profile.exchange_charge_rules.dp_charges_delivery_sell
        return 0.0
