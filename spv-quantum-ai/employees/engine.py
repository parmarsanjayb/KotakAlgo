from typing import Dict, Any, List, Tuple, Optional
from employees.models import EmployeeProfile, EmployeeState, EmployeeType
from employees.manager import EmployeeManager
from employees.permission import EmployeePermissionManager
from employees.policy import EmployeePolicyManager
from employees.volume_intelligence import VolumeIntelligenceEmployee
from employees.option_flow import OptionFlowIntelligenceEmployee
from employees.trend_intelligence import TrendIntelligenceEmployee

class EmployeeEngine:
    """Enterprise AI Employee Profile Engine coordinating identity layers and configuration guards."""
    def __init__(self) -> None:
        self.manager = EmployeeManager()
        self.permission_mgr = EmployeePermissionManager()
        self.policy_mgr = EmployeePolicyManager()
        self.volume_intelligence = VolumeIntelligenceEmployee()
        self.option_flow = OptionFlowIntelligenceEmployee()
        self.trend_intelligence = TrendIntelligenceEmployee()
        
        from employees.new_specialists import (
            MomentumEmployee, VWAPEmployee, MarketRegimeEmployee,
            OIEmployee, PCREmployee, GreeksEmployee, MaxPainEmployee,
            SmartMoneyEmployee, LiquidityEmployee, OrderFlowEmployee, DeliveryEmployee,
            RiskEmployee, PositionSizingEmployee, CapitalProtectionEmployee, ExposureEmployee,
            NewsEmployee, EconomicCalendarEmployee, EventRiskEmployee,
            ExecutionEmployee, PortfolioEmployee, PaperTradingEmployee,
            OptionsSpecialistEmployee, EquityIntradaySpecialistEmployee,
            EquitySwingSpecialistEmployee, CommoditySpecialistEmployee,
            CurrencySpecialistEmployee, PortfolioManagerEmployee
        )
        self.new_specialists = [
            MomentumEmployee(), VWAPEmployee(), MarketRegimeEmployee(),
            OIEmployee(), PCREmployee(), GreeksEmployee(), MaxPainEmployee(),
            SmartMoneyEmployee(), LiquidityEmployee(), OrderFlowEmployee(), DeliveryEmployee(),
            RiskEmployee(), PositionSizingEmployee(), CapitalProtectionEmployee(), ExposureEmployee(),
            NewsEmployee(), EconomicCalendarEmployee(), EventRiskEmployee(),
            ExecutionEmployee(), PortfolioEmployee(), PaperTradingEmployee(),
            OptionsSpecialistEmployee(), EquityIntradaySpecialistEmployee(),
            EquitySwingSpecialistEmployee(), CommoditySpecialistEmployee(),
            CurrencySpecialistEmployee(), PortfolioManagerEmployee()
        ]
        
        self.momentum = self.new_specialists[0]
        self.vwap_emp = self.new_specialists[1]
        self.market_regime = self.new_specialists[2]
        self.oi_emp = self.new_specialists[3]
        self.pcr_emp = self.new_specialists[4]
        self.greeks = self.new_specialists[5]
        self.max_pain = self.new_specialists[6]
        self.smart_money = self.new_specialists[7]
        self.liquidity = self.new_specialists[8]
        self.order_flow = self.new_specialists[9]
        self.delivery = self.new_specialists[10]
        self.risk_emp = self.new_specialists[11]
        self.pos_sizing = self.new_specialists[12]
        self.cap_protection = self.new_specialists[13]
        self.exposure_emp = self.new_specialists[14]
        self.news_emp = self.new_specialists[15]
        self.calendar = self.new_specialists[16]
        self.event_risk = self.new_specialists[17]
        self.execution = self.new_specialists[18]
        self.portfolio_emp = self.new_specialists[19]
        self.paper_trading = self.new_specialists[20]
        self.options_specialist = self.new_specialists[21]
        self.equity_intraday = self.new_specialists[22]
        self.equity_swing = self.new_specialists[23]
        self.commodity_specialist = self.new_specialists[24]
        self.currency_specialist = self.new_specialists[25]
        self.portfolio_mgr = self.new_specialists[26]
        
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.manager.start()
        await self.volume_intelligence.start()
        await self.option_flow.start()
        await self.trend_intelligence.start()
        for spec in self.new_specialists:
            await spec.start()

    async def stop(self) -> None:
        self._running = False
        for spec in self.new_specialists:
            await spec.stop()
        await self.trend_intelligence.stop()
        await self.option_flow.stop()
        await self.volume_intelligence.stop()
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
        import time
        start_t = time.perf_counter()
        
        # 1. Volume Intelligence validation
        symbol = order_data.get("symbol")
        if symbol:
            vol_confirm = await self.volume_intelligence.check_confirmation(symbol)
            if vol_confirm == "REJECT":
                try:
                    await self.manager.record_activity(
                        employee_code="EMP-VOL",
                        decision="REJECT",
                        confidence=60.0,
                        execution_time_ms=1.0
                    )
                except Exception:
                    pass
                return False, f"Volume Intelligence Employee REJECTED trade execution for {symbol}: Fake Breakout/Low Volume detected."

        emp_code = order_data.get("employee_code") or self.manager.active_code
        if not emp_code:
            # If no active employee config, pass through
            return True, ""

        profile = self.manager.get_profile(emp_code)
        if not profile:
            return False, f"Employee profile code '{emp_code}' not found."

        decision = "REJECTED"
        error_msg = None
        confidence = getattr(profile, "confidence_threshold", 60.0)
        
        try:
            allowed, msg = self.permission_mgr.check_order(profile, order_data)
            if allowed:
                decision = "APPROVED"
            else:
                decision = f"REJECTED: {msg}"
            return allowed, msg
        except Exception as e:
            decision = f"ERROR: {str(e)}"
            error_msg = str(e)
            raise e
        finally:
            exec_time_ms = (time.perf_counter() - start_t) * 1000.0
            try:
                await self.manager.record_activity(
                    employee_code=emp_code,
                    decision=decision,
                    confidence=confidence,
                    execution_time_ms=exec_time_ms,
                    error=error_msg
                )
            except Exception:
                pass

    async def get_dashboard_metrics(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Compiles AI employee registry lists and performance analytics for APIs."""
        plan_tier = "FREE"
        if tenant_id:
            from database.connection import async_session
            from database.models import SubscriptionModel
            from sqlalchemy import select
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(SubscriptionModel).where(SubscriptionModel.user_id == tenant_id)
                    )
                    sub = result.scalars().first()
                    if sub:
                        plan_tier = sub.plan_tier
            except Exception:
                pass

        profiles = self.manager.get_profiles_by_tenant(tenant_id, plan_tier)
        
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
                "is_active": p.is_active,
                "health_status": p.health_status,
                "last_decision": p.last_decision,
                "last_decision_confidence": p.last_decision_confidence,
                "total_signals": p.total_signals,
                "correct_signals": p.correct_signals,
                "incorrect_signals": p.incorrect_signals,
                "accuracy_pct": p.accuracy_pct,
                "last_execution_time_ms": p.last_execution_time_ms,
                "avg_execution_time_ms": p.avg_execution_time_ms,
                "error_count": p.error_count,
                "last_error": p.last_error,
                "heartbeat_timestamp": p.heartbeat_timestamp.isoformat() if p.heartbeat_timestamp else None,
                "accuracy_history": p.accuracy_history,
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
