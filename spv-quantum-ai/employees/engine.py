from typing import Dict, Any, List, Tuple, Optional
from employees.models import EmployeeProfile, EmployeeState, EmployeeType
from employees.manager import EmployeeManager
from employees.permission import EmployeePermissionManager
from employees.policy import EmployeePolicyManager

class EmployeeEngine:
    """Enterprise AI Employee Profile Engine coordinating identity layers and configuration guards."""
    def __init__(self) -> None:
        self.manager = EmployeeManager()
        self.permission_mgr = EmployeePermissionManager()
        self.policy_mgr = EmployeePolicyManager()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.manager.start()

    async def stop(self) -> None:
        self._running = False
        await self.manager.stop()

    async def activate_employee(self, code: str) -> bool:
        """Sets the active employee profile and propagates safety policies."""
        profile = self.manager.get_profile(code)
        if not profile:
            return False
        
        # Transition state
        success = await self.manager.set_employee_state(code, EmployeeState.ACTIVE)
        if success:
            self.manager.active_code = code
            self.policy_mgr.apply_policy(profile)
        return success

    async def check_allowed_order(self, order_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Validates order requests against active employee permissions before execution."""
        emp_code = order_data.get("employee_code") or self.manager.active_code
        if not emp_code:
            # If no active employee config, pass through
            return True, ""

        profile = self.manager.get_profile(emp_code)
        if not profile:
            return False, f"Employee profile code '{emp_code}' not found."

        return self.permission_mgr.check_order(profile, order_data)

    async def get_dashboard_metrics(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Compiles AI employee registry lists and performance analytics for APIs."""
        profiles = self.manager.get_profiles_by_tenant(tenant_id)
        
        employee_list = []
        for p in profiles:
            employee_list.append({
                "employee_code": p.employee_code,
                "name": p.name,
                "avatar": p.avatar,
                "description": p.description,
                "state": p.state.value,
                "employee_type": p.employee_type.value,
                "capital_allocation": p.capital_allocation,
                "pnl": p.pnl,
                "win_rate": p.win_rate,
                "trade_count": p.trade_count,
                "risk_stats": {
                    "consecutive_wins": p.consecutive_wins,
                    "consecutive_losses": p.consecutive_losses,
                    "max_exposure": p.max_exposure,
                    "max_daily_loss": p.max_daily_loss
                },
                "strategy_performance": p.strategy_pnl,
                "config": {
                    "allowed_segments": p.allowed_segments,
                    "allowed_products": p.allowed_products,
                    "allowed_timeframes": p.allowed_timeframes,
                    "confidence_threshold": p.confidence_threshold,
                    "enable_news_filter": p.enable_news_filter,
                    "enable_regime_filter": p.enable_regime_filter
                }
            })

        active_profile = self.manager.get_profile(self.manager.active_code) if self.manager.active_code else None

        return {
            "active_employee_code": self.manager.active_code,
            "active_employee_name": active_profile.name if active_profile else None,
            "employees": employee_list
        }

# Singleton
employee_engine = EmployeeEngine()
