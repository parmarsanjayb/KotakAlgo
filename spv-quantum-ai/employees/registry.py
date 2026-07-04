from typing import Dict, Any, List
from employees.models import EmployeeProfile, EmployeeType, EmployeeState

class EmployeeRegistry:
    """Predefines and tracks default templates for AI Employee profiles."""
    @staticmethod
    def get_default_profile(employee_type: EmployeeType, code: str, name: str) -> EmployeeProfile:
        if employee_type == EmployeeType.OPTIONS_SPECIALIST:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_options.png",
                description="Specializes in Option trading strategies, margins, and hedging.",
                employee_type=EmployeeType.OPTIONS_SPECIALIST,
                allowed_segments=["Options"],
                allowed_products=["NRML", "MIS"],
                allowed_timeframes=["1m", "5m"],
                allowed_strategies=["OptionBuyingStrategy", "OptionSellingStrategy"],
                confidence_threshold=70.0,
                max_exposure=25000.0,
                capital_allocation=200000.0
            )
        elif employee_type == EmployeeType.EQUITY_INTRADAY:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_intraday.png",
                description="Trades Equity Intraday positions utilizing margin multiplier leverage.",
                employee_type=EmployeeType.EQUITY_INTRADAY,
                allowed_segments=["Equity"],
                allowed_products=["MIS"],
                allowed_timeframes=["1m", "5m", "15m"],
                confidence_threshold=65.0,
                max_exposure=15000.0,
                capital_allocation=100000.0
            )
        elif employee_type == EmployeeType.EQUITY_SWING:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_swing.png",
                description="Executes longer-duration swing signals on equity markets.",
                employee_type=EmployeeType.EQUITY_SWING,
                allowed_segments=["Equity"],
                allowed_products=["CNC", "NRML"],
                allowed_timeframes=["1h", "daily"],
                confidence_threshold=60.0,
                max_exposure=30000.0,
                capital_allocation=150000.0
            )
        elif employee_type == EmployeeType.DELIVERY_INVESTOR:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_delivery.png",
                description="Long-term delivery investment, focuses on value holding.",
                employee_type=EmployeeType.DELIVERY_INVESTOR,
                allowed_segments=["Equity"],
                allowed_products=["CNC"],
                allowed_timeframes=["daily", "weekly"],
                confidence_threshold=55.0,
                max_exposure=50000.0,
                capital_allocation=300000.0
            )
        elif employee_type == EmployeeType.COMMODITY_SPECIALIST:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_commodity.png",
                description="Focuses on Gold, Crude Oil, and Natural Gas futures.",
                employee_type=EmployeeType.COMMODITY_SPECIALIST,
                allowed_segments=["Commodity"],
                allowed_products=["NRML"],
                allowed_timeframes=["5m", "15m", "1h"],
                confidence_threshold=68.0,
                max_exposure=20000.0,
                capital_allocation=150000.0
            )
        elif employee_type == EmployeeType.CURRENCY_SPECIALIST:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_currency.png",
                description="Focuses on USDINR and EURINR currency derivatives.",
                employee_type=EmployeeType.CURRENCY_SPECIALIST,
                allowed_segments=["Currency"],
                allowed_products=["NRML"],
                allowed_timeframes=["1m", "5m"],
                confidence_threshold=62.0,
                max_exposure=10000.0,
                capital_allocation=80000.0
            )
        elif employee_type == EmployeeType.PORTFOLIO_MANAGER:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_pm.png",
                description="Orchestrates allocation rules across multi-asset categories.",
                employee_type=EmployeeType.PORTFOLIO_MANAGER,
                allowed_segments=["Equity", "Options", "Commodity"],
                allowed_products=["CNC", "NRML", "MIS"],
                allowed_timeframes=["daily"],
                confidence_threshold=60.0,
                max_exposure=100000.0,
                capital_allocation=500000.0
            )
        else:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_custom.png",
                description="Custom behavior configurations.",
                employee_type=EmployeeType.CUSTOM,
                allowed_segments=["Equity"],
                allowed_products=["MIS"],
                allowed_timeframes=["1m", "5m"]
            )
