from employees.models import EmployeeProfile
from safety import safety_engine

class EmployeePolicyManager:
    """Configures other platform modules dynamically to match active employee rules."""
    def apply_policy(self, profile: EmployeeProfile) -> None:
        """Propagates employee configuration limits to the Safety Engine."""
        safety_engine.config["daily_loss_guard_usd"] = profile.max_daily_loss
        safety_engine.config["daily_profit_lock_usd"] = profile.max_daily_profit
        safety_engine.config["max_exposure_usd"] = profile.max_exposure
        
        # Override session bounds if specified
        if profile.trading_sessions:
            # Assume first format e.g. "09:15-15:30"
            session = profile.trading_sessions[0]
            if "-" in session:
                start, end = session.split("-")
                safety_engine.config["trading_session_start"] = start.strip()
                safety_engine.config["trading_session_end"] = end.strip()
