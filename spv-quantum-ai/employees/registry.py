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
        elif employee_type == EmployeeType.VOLUME_INTELLIGENCE:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_volume.png",
                description="Volume trend validation, Spike detection, and Breakout confirmation. Does NOT execute trades directly.",
                employee_type=EmployeeType.VOLUME_INTELLIGENCE,
                allowed_segments=["Equity", "Options", "Commodity"],
                allowed_products=["CNC", "NRML", "MIS"],
                allowed_timeframes=["1m", "5m", "15m", "1h", "daily"],
                confidence_threshold=50.0,
                max_exposure=0.0,
                capital_allocation=0.0
            )
        elif employee_type == EmployeeType.OPTION_FLOW:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_option_flow.png",
                description="ATM Option Chain order flow tracking, PCR, build-ups, and smart money direction bias. Does NOT execute trades directly.",
                employee_type=EmployeeType.OPTION_FLOW,
                allowed_segments=["Options"],
                allowed_products=["NRML", "MIS"],
                allowed_timeframes=["1m", "5m", "15m"],
                confidence_threshold=60.0,
                max_exposure=0.0,
                capital_allocation=0.0
            )
        elif employee_type == EmployeeType.TREND_INTELLIGENCE:
            return EmployeeProfile(
                employee_code=code,
                name=name,
                avatar="avatar_trend.png",
                description="Market Trend validation, EMA alignments, VWAP status, ADX/RSI momentum checks. Does NOT execute trades directly.",
                employee_type=EmployeeType.TREND_INTELLIGENCE,
                allowed_segments=["Equity", "Options"],
                allowed_products=["CNC", "NRML", "MIS"],
                allowed_timeframes=["1m", "5m", "15m", "1h", "daily"],
                confidence_threshold=50.0,
                max_exposure=0.0,
                capital_allocation=0.0
            )
        elif employee_type == EmployeeType.MOMENTUM:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_momentum.png",
                description="Tracks RSI, StochRSI, and CCI momentum indicators.",
                employee_type=EmployeeType.MOMENTUM,
                allowed_segments=["Equity", "Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.VWAP:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_vwap.png",
                description="Tracks price status in relation to VWAP.",
                employee_type=EmployeeType.VWAP,
                allowed_segments=["Equity", "Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.MARKET_REGIME:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_regime.png",
                description="Tracks regime transitions (Trending, Rangebound, Gap, etc.).",
                employee_type=EmployeeType.MARKET_REGIME,
                allowed_segments=["Equity", "Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.OPTION_OI:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_oi.png",
                description="Analyzes Open Interest changes in Calls vs Puts.",
                employee_type=EmployeeType.OPTION_OI,
                allowed_segments=["Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.OPTION_PCR:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_pcr.png",
                description="Monitors Put-Call Ratio (PCR) thresholds.",
                employee_type=EmployeeType.OPTION_PCR,
                allowed_segments=["Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.OPTION_GREEKS:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_greeks.png",
                description="Monitors Delta, Gamma, Theta, and Vega risk indicators.",
                employee_type=EmployeeType.OPTION_GREEKS,
                allowed_segments=["Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.OPTION_MAX_PAIN:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_max_pain.png",
                description="Tracks the strike price with maximum pain for option buyers.",
                employee_type=EmployeeType.OPTION_MAX_PAIN,
                allowed_segments=["Options"], allowed_products=["MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.INSTITUTIONAL_SMART_MONEY:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_smart_money.png",
                description="Tracks smart money blocks and institutional activities.",
                employee_type=EmployeeType.INSTITUTIONAL_SMART_MONEY,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m", "1h", "daily"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.INSTITUTIONAL_LIQUIDITY:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_liquidity.png",
                description="Monitors depth of bid/ask queues and market liquidity.",
                employee_type=EmployeeType.INSTITUTIONAL_LIQUIDITY,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.INSTITUTIONAL_ORDER_FLOW:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_order_flow.png",
                description="Monitors real-time institutional transaction flow delta.",
                employee_type=EmployeeType.INSTITUTIONAL_ORDER_FLOW,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.RISK_MONITOR:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_risk_emp.png",
                description="Monitors total portfolio drawdown limits and risk rules.",
                employee_type=EmployeeType.RISK_MONITOR,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.RISK_POSITION_SIZING:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_pos_sizing.png",
                description="Recommends size metrics based on risk allocations.",
                employee_type=EmployeeType.RISK_POSITION_SIZING,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.RISK_CAPITAL_PROTECTION:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_cap_protection.png",
                description="Ensures trade execution pauses during major drawdowns.",
                employee_type=EmployeeType.RISK_CAPITAL_PROTECTION,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.RISK_EXPOSURE:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_exposure_emp.png",
                description="Monitors active exposures across symbols.",
                employee_type=EmployeeType.RISK_EXPOSURE,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.NEWS_SENTIMENT:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_news_emp.png",
                description="Monitors global headlines and news sentiments.",
                employee_type=EmployeeType.NEWS_SENTIMENT,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m", "15m", "1h", "daily"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.ECONOMIC_CALENDAR:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_calendar.png",
                description="Monitors upcoming central bank policies and events.",
                employee_type=EmployeeType.ECONOMIC_CALENDAR,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.EVENT_RISK:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_event_risk.png",
                description="Monitors high-volatility event risk.",
                employee_type=EmployeeType.EVENT_RISK,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.EXECUTION:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_exec_emp.png",
                description="Monitors transaction execution speeds.",
                employee_type=EmployeeType.EXECUTION,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.PORTFOLIO:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_portfolio_emp.png",
                description="Monitors net asset returns and win rates.",
                employee_type=EmployeeType.PORTFOLIO,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
            )
        elif employee_type == EmployeeType.PAPER_TRADING:
            return EmployeeProfile(
                employee_code=code, name=name, avatar="avatar_paper_emp.png",
                description="Monitors virtual margins and account balances.",
                employee_type=EmployeeType.PAPER_TRADING,
                allowed_segments=["Equity", "Options"], allowed_products=["CNC", "MIS", "NRML"],
                allowed_timeframes=["1m", "5m"], confidence_threshold=50.0,
                max_exposure=0.0, capital_allocation=0.0
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
