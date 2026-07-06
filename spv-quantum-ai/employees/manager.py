import asyncio
import os
import yaml
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.bus import event_bus, EventModel
from core.logging import get_logger
from employees.models import EmployeeProfile, EmployeeState, EmployeeType
from employees.registry import EmployeeRegistry
from employees.publisher import EmployeePublisher

logger = get_logger("employee_manager")

class EmployeeManager:
    """Manages profile lists, SaaS tenant routing, state controls, and trade histories."""
    def __init__(self) -> None:
        self.profiles: Dict[str, EmployeeProfile] = {}
        self.publisher = EmployeePublisher()
        self._lock = asyncio.Lock()
        self.active_code: Optional[str] = None
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Subscribe to execution updates
        await event_bus.subscribe("order_filled", self._on_order_filled)
        await event_bus.subscribe("trade_closed", self._on_trade_closed)
        
        # Pre-populate with defaults
        if not self.profiles:
            mapping = {
                EmployeeType.VOLUME_INTELLIGENCE: ("EMP-VOL", "Default Volume Intelligence Specialist"),
                EmployeeType.OPTION_FLOW: ("EMP-OFT", "Default Option Flow Specialist"),
                EmployeeType.TREND_INTELLIGENCE: ("EMP-TRD", "Default Trend Intelligence Specialist"),
                EmployeeType.MOMENTUM: ("EMP-MOM", "Default Momentum Employee"),
                EmployeeType.VWAP: ("EMP-VWP", "Default VWAP Employee"),
                EmployeeType.MARKET_REGIME: ("EMP-RGM", "Default Market Regime Employee"),
                EmployeeType.OPTION_OI: ("EMP-OIE", "Default OI Employee"),
                EmployeeType.OPTION_PCR: ("EMP-PCR", "Default PCR Employee"),
                EmployeeType.OPTION_GREEKS: ("EMP-GRK", "Default Greeks Employee"),
                EmployeeType.OPTION_MAX_PAIN: ("EMP-MPN", "Default Max Pain Employee"),
                EmployeeType.INSTITUTIONAL_SMART_MONEY: ("EMP-SME", "Default Smart Money Employee"),
                EmployeeType.INSTITUTIONAL_LIQUIDITY: ("EMP-LQD", "Default Liquidity Employee"),
                EmployeeType.INSTITUTIONAL_ORDER_FLOW: ("EMP-OFL", "Default Order Flow Employee"),
                EmployeeType.DELIVERY_INVESTOR: ("EMP-DEL", "Default Delivery Employee"),
                EmployeeType.RISK_MONITOR: ("EMP-RSK", "Default Risk Employee"),
                EmployeeType.RISK_POSITION_SIZING: ("EMP-PZS", "Default Position Sizing Employee"),
                EmployeeType.RISK_CAPITAL_PROTECTION: ("EMP-CPT", "Default Capital Protection Employee"),
                EmployeeType.RISK_EXPOSURE: ("EMP-EXP", "Default Exposure Employee"),
                EmployeeType.NEWS_SENTIMENT: ("EMP-NWS", "Default News Employee"),
                EmployeeType.ECONOMIC_CALENDAR: ("EMP-CAL", "Default Economic Calendar Employee"),
                EmployeeType.EVENT_RISK: ("EMP-EVR", "Default Event Risk Employee"),
                EmployeeType.EXECUTION: ("EMP-EXE", "Default Execution Employee"),
                EmployeeType.PORTFOLIO: ("EMP-PTF", "Default Portfolio Employee"),
                EmployeeType.PAPER_TRADING: ("EMP-PPR", "Default Paper Trading Employee"),
                
                # Predefined original specialists
                EmployeeType.OPTIONS_SPECIALIST: ("EMP-OPT", "Default Options Specialist"),
                EmployeeType.EQUITY_INTRADAY: ("EMP-EQI", "Default Equity Intraday Specialist"),
                EmployeeType.EQUITY_SWING: ("EMP-EQS", "Default Equity Swing Specialist"),
                EmployeeType.COMMODITY_SPECIALIST: ("EMP-COM", "Default Commodity Specialist"),
                EmployeeType.CURRENCY_SPECIALIST: ("EMP-CUR", "Default Currency Specialist"),
                EmployeeType.PORTFOLIO_MANAGER: ("EMP-PM", "Default Portfolio Manager"),
            }
            for emp_type, (code, name) in mapping.items():
                profile = EmployeeRegistry.get_default_profile(emp_type, code, name)
                self.profiles[code] = profile
                    
        config_path = "config/employees.yaml"
        if os.path.exists(config_path):
            await self.load_from_yaml(config_path)

        # Start heartbeat monitoring
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())

        logger.info("EmployeeManager sub-systems started.")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            
        await event_bus.unsubscribe("order_filled", self._on_order_filled)
        await event_bus.unsubscribe("trade_closed", self._on_trade_closed)
        logger.info("EmployeeManager sub-systems stopped.")

    async def load_from_yaml(self, filepath: str) -> None:
        """Loads and parses employee configurations from a YAML file."""
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "employees" in data:
                    async with self._lock:
                        for emp_data in data["employees"]:
                            profile = EmployeeProfile(**emp_data)
                            self.profiles[profile.employee_code] = profile
            logger.info(f"Successfully loaded employees from {filepath}")
        except Exception as e:
            logger.error("Failed to parse employees config", error=str(e))

    async def register_employee(self, profile: EmployeeProfile) -> None:
        async with self._lock:
            self.profiles[profile.employee_code] = profile

    def get_profile(self, code: str) -> Optional[EmployeeProfile]:
        return self.profiles.get(code)

    def get_profiles_by_tenant(self, tenant_id: Optional[str] = None) -> List[EmployeeProfile]:
        """Provides SaaS/multi-tenant support by filtering profiles by tenant ID."""
        return [p for p in self.profiles.values() if p.tenant_id == tenant_id]

    async def set_employee_state(self, code: str, state: EmployeeState) -> bool:
        """Transitions state and publishes activation/pause notifications."""
        profile = self.get_profile(code)
        if not profile:
            return False

        old_state = profile.state
        profile.state = state
        
        if state in (EmployeeState.ACTIVE, EmployeeState.LIVE_TRADING, EmployeeState.PAPER_TRADING) and old_state != state:
            await self.publisher.publish_activated(code, profile.name)
        elif state == EmployeeState.PAUSED and old_state != state:
            await self.publisher.publish_paused(code, profile.name)

        await self.publisher.publish_profile_updated(code, {"state": state.value})
        return True

    async def update_allocation(self, code: str, capital: float) -> bool:
        profile = self.get_profile(code)
        if not profile:
            return False
        profile.capital_allocation = capital
        await self.publisher.publish_capital_updated(code, capital)
        return True

    async def _on_order_filled(self, event: EventModel) -> None:
        """Listens for fills to update performance stats of the executing AI Employee."""
        try:
            payload = event.payload
            order_data = payload.get("order", payload)
            
            # Identify executing employee
            emp_code = order_data.get("employee_code") or self.active_code
            if not emp_code:
                return

            profile = self.get_profile(emp_code)
            if not profile:
                return

            async with self._lock:
                symbol = order_data.get("symbol")
                side = order_data.get("side", "BUY")
                qty = float(order_data.get("filled_quantity", order_data.get("quantity", 0.0)))
                price = float(order_data.get("avg_fill_price", order_data.get("price", 0.0)))
                pnl = float(payload.get("pnl", 0.0))
                strategy = order_data.get("strategy_name", "Default")

                # Record trade
                trade_entry = {
                    "timestamp": payload.get("timestamp", ""),
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "price": price,
                    "pnl": pnl,
                    "strategy": strategy,
                    "status": "CLOSED" if pnl != 0 else "OPEN"
                }
                profile.trade_history.append(trade_entry)
                profile.trade_count += 1
                profile.pnl += pnl

                # Update strategy performance
                profile.strategy_pnl[strategy] = profile.strategy_pnl.get(strategy, 0.0) + pnl

                # Wins/Losses streaks
                if pnl > 0:
                    profile.consecutive_wins += 1
                    profile.consecutive_losses = 0
                elif pnl < 0:
                    profile.consecutive_losses += 1
                    profile.consecutive_wins = 0

                # Win rate calculation
                wins = sum(1 for t in profile.trade_history if t.get("pnl", 0.0) > 0)
                profile.win_rate = round((wins / profile.trade_count) * 100.0, 2) if profile.trade_count > 0 else 0.0

            await self.publisher.publish_profile_updated(emp_code, {"pnl": profile.pnl, "win_rate": profile.win_rate})

        except Exception as e:
            logger.error("Failed to handle order filled update in EmployeeManager", error=str(e))

    async def _on_trade_closed(self, event: EventModel) -> None:
        """Listens for closed trades to update accuracy and signals of participating employees."""
        try:
            payload = event.payload
            trade_data = payload.get("trade", payload)
            
            # Avoid direct circular imports
            from journal.models import TradeRecord
            if isinstance(trade_data, dict):
                trade_record = TradeRecord(**trade_data)
            else:
                trade_record = trade_data
            
            pnl = trade_record.net_pnl
            employee_codes = getattr(trade_record, "employee_codes", [])
            
            async with self._lock:
                for emp_code in employee_codes:
                    profile = self.get_profile(emp_code)
                    if not profile:
                        continue
                    
                    profile.total_signals += 1
                    if pnl > 0:
                        profile.correct_signals += 1
                    elif pnl < 0:
                        profile.incorrect_signals += 1
                    
                    # Update accuracy
                    if profile.total_signals > 0:
                        profile.accuracy_pct = round((profile.correct_signals / profile.total_signals) * 100.0, 2)
                    else:
                        profile.accuracy_pct = 100.0
                    
                    # Track accuracy snapshot over time
                    profile.accuracy_history.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "accuracy_pct": profile.accuracy_pct,
                        "total_signals": profile.total_signals
                    })
                    
                    # Publish updates
                    await self.publisher.publish_profile_updated(emp_code, {
                        "total_signals": profile.total_signals,
                        "correct_signals": profile.correct_signals,
                        "incorrect_signals": profile.incorrect_signals,
                        "accuracy_pct": profile.accuracy_pct,
                        "accuracy_history": profile.accuracy_history
                    })
        except Exception as e:
            logger.error("Failed to handle trade closed in EmployeeManager", error=str(e))

    async def record_activity(self, employee_code: str, decision: str, confidence: float, execution_time_ms: float, error: Optional[str] = None) -> None:
        """Records employee runtime heartbeat and decision execution metrics."""
        async with self._lock:
            profile = self.get_profile(employee_code)
            if not profile:
                return
            
            profile.heartbeat_timestamp = datetime.now(timezone.utc)
            profile.is_active = True
            profile.health_status = "HEALTHY" if not error else "FAILED"
            profile.last_decision = decision
            profile.last_decision_confidence = confidence
            
            # Execution time tracking
            profile.last_execution_time_ms = execution_time_ms
            # Calculate rolling average
            total_execs = profile.total_signals or 1
            profile.avg_execution_time_ms = round(((profile.avg_execution_time_ms * (total_execs - 1)) + execution_time_ms) / total_execs, 2)
            
            if error:
                profile.error_count += 1
                profile.last_error = error
                profile.health_status = "FAILED"
            
            # Publish heartbeat update
            await self.publisher.publish_profile_updated(employee_code, {
                "is_active": profile.is_active,
                "health_status": profile.health_status,
                "last_decision": profile.last_decision,
                "last_decision_confidence": profile.last_decision_confidence,
                "last_execution_time_ms": profile.last_execution_time_ms,
                "avg_execution_time_ms": profile.avg_execution_time_ms,
                "error_count": profile.error_count,
                "last_error": profile.last_error,
                "heartbeat_timestamp": profile.heartbeat_timestamp.isoformat()
            })

            # Stream decision logs to WebSocket dashboard
            await event_bus.publish(EventModel(
                event_type="employee_decision",
                source_agent="employee_manager",
                payload={
                    "timestamp": profile.heartbeat_timestamp.isoformat(),
                    "employee_code": employee_code,
                    "name": profile.name,
                    "decision": decision,
                    "confidence": confidence,
                    "execution_time_ms": execution_time_ms,
                    "error": error
                }
            ))

            # Stream status changes to WebSocket dashboard
            await event_bus.publish(EventModel(
                event_type="employee_status_updated",
                source_agent="employee_manager",
                payload={
                    "employee_code": employee_code,
                    "name": profile.name,
                    "is_active": profile.is_active,
                    "health_status": profile.health_status,
                    "last_decision": profile.last_decision,
                    "last_decision_confidence": profile.last_decision_confidence,
                    "total_signals": profile.total_signals,
                    "accuracy_pct": profile.accuracy_pct,
                    "last_execution_time_ms": profile.last_execution_time_ms,
                    "heartbeat_timestamp": profile.heartbeat_timestamp.isoformat()
                }
            ))

    async def _monitor_heartbeats(self) -> None:
        """Background loop validating heartbeats and raising alerts if employees stop responding."""
        await asyncio.sleep(5)  # Wait for startup
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                async with self._lock:
                    for code, profile in self.profiles.items():
                        if profile.state == EmployeeState.ACTIVE:
                            if not profile.heartbeat_timestamp:
                                profile.heartbeat_timestamp = now
                            
                            delta = (now - profile.heartbeat_timestamp).total_seconds()
                            if delta > 30.0:  # If stale for more than 30 seconds
                                if profile.health_status != "FAILED" or profile.is_active:
                                    profile.health_status = "FAILED"
                                    profile.is_active = False
                                    logger.error(f"ALERT: Employee '{profile.name}' ({code}) stopped responding! Last heartbeat was {delta} seconds ago.")
                                    
                                    # Publish alert
                                    await event_bus.publish(EventModel(
                                        event_type="critical_employee_failure_alert",
                                        source_agent="employee_manager",
                                        payload={
                                            "employee_code": code,
                                            "name": profile.name,
                                            "message": f"AI Employee '{profile.name}' has stopped responding. Heartbeat stale by {delta:.1f} seconds.",
                                            "category": "employee_failure"
                                        }
                                    ))
                                    
                                    # Publish status update
                                    await event_bus.publish(EventModel(
                                        event_type="employee_status_updated",
                                        source_agent="employee_manager",
                                        payload={
                                            "employee_code": code,
                                            "name": profile.name,
                                            "is_active": profile.is_active,
                                            "health_status": profile.health_status,
                                            "last_decision": profile.last_decision,
                                            "last_decision_confidence": profile.last_decision_confidence,
                                            "total_signals": profile.total_signals,
                                            "accuracy_pct": profile.accuracy_pct,
                                            "last_execution_time_ms": profile.last_execution_time_ms,
                                            "heartbeat_timestamp": profile.heartbeat_timestamp.isoformat()
                                        }
                                    ))
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in employee heartbeat monitor loop", error=str(e))
                await asyncio.sleep(5)
