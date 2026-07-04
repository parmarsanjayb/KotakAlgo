from typing import Dict, Any, Tuple
from employees.models import EmployeeProfile, EmployeeState

class EmployeePermissionManager:
    """Evaluates whether an order complies with the AI Employee's allowed boundaries."""
    def check_order(self, profile: EmployeeProfile, order_data: Dict[str, Any]) -> Tuple[bool, str]:
        if profile.state == EmployeeState.DISABLED:
            return False, f"Employee {profile.name} is DISABLED."

        # Segment check
        segment = order_data.get("segment", "Equity")
        if segment not in profile.allowed_segments:
            return False, f"Segment '{segment}' is not allowed for employee {profile.name}."

        # Product check
        product = order_data.get("product", "MIS")
        if product not in profile.allowed_products:
            return False, f"Product '{product}' is not allowed for employee {profile.name}."

        # Strategy check
        strategy = order_data.get("strategy_name")
        if strategy and profile.allowed_strategies and strategy not in profile.allowed_strategies:
            return False, f"Strategy '{strategy}' is not allowed for employee {profile.name}."

        # Timeframe check
        timeframe = order_data.get("timeframe")
        if timeframe and timeframe not in profile.allowed_timeframes:
            return False, f"Timeframe '{timeframe}' is not allowed for employee {profile.name}."

        # Max open trades check
        current_open = len([t for t in profile.trade_history if t.get("status") in ("OPEN", "QUEUED", "SENT")])
        if current_open >= profile.max_open_trades:
            return False, f"Maximum open trades limit ({profile.max_open_trades}) reached for employee {profile.name}."

        return True, ""
