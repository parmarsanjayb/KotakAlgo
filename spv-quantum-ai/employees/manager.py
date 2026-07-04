import asyncio
import os
import yaml
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

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Subscribe to execution updates
        await event_bus.subscribe("order_filled", self._on_order_filled)
        
        # Pre-populate with defaults
        if not self.profiles:
            for emp_type in EmployeeType:
                if emp_type != EmployeeType.CUSTOM:
                    code = f"EMP-{emp_type.name[:3]}"
                    name = f"Default {emp_type.value}"
                    profile = EmployeeRegistry.get_default_profile(emp_type, code, name)
                    self.profiles[code] = profile
                    
        config_path = "config/employees.yaml"
        if os.path.exists(config_path):
            await self.load_from_yaml(config_path)

        logger.info("EmployeeManager sub-systems started.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("order_filled", self._on_order_filled)
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
