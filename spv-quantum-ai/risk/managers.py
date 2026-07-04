import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from core.config import settings
from brokers.manager import broker_manager
from core.logging import get_logger

logger = get_logger("risk_managers")

class CapitalManager:
    """
    Tracks capital availability and verifies if margin limits are respected.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    async def get_capital_info(self) -> Dict[str, float]:
        """Queries the active broker for actual cash balance and margin."""
        try:
            broker = broker_manager.get_active()
            resp = await broker.get_balance()
            if resp.success and resp.data:
                data = resp.data
                return {
                    "equity": float(data.get("equity", 0.0)),
                    "available_margin": float(data.get("available_margin", 0.0)),
                    "used_margin": float(data.get("used_margin", 0.0))
                }
        except Exception as e:
            logger.error("Failed to query capital info from broker", error=str(e))
        
        # Fallback to config values or default
        return {
            "equity": 100000.0,
            "available_margin": 100000.0,
            "used_margin": 0.0
        }

    async def validate_margin(self, order_cost: float) -> bool:
        cap = await self.get_capital_info()
        return cap["available_margin"] >= order_cost


class DrawdownManager:
    """
    Monitors peak equity and current drawdown percentage.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.max_drawdown_pct = float(config.get("max_drawdown_percent", 5.0))
        self.peak_equity = 0.0
        self._lock = asyncio.Lock()

    async def check_drawdown(self, current_equity: float) -> tuple[bool, float]:
        """
        Updates peak equity, calculates current drawdown % and checks if limit is breached.
        Returns: (is_allowed, current_drawdown_pct)
        """
        async with self._lock:
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            
            if self.peak_equity <= 0:
                return True, 0.0
            
            drawdown_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100.0
            allowed = drawdown_pct < self.max_drawdown_pct
            return allowed, drawdown_pct


class ExposureManager:
    """
    Manages net exposure limits and maximum open positions.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.max_exposure_usd = float(config.get("max_exposure_usd", 50000.0))
        self.max_open_positions = int(config.get("max_open_positions", 5))

    async def evaluate_exposure(self, symbol: str, additional_cost: float) -> tuple[bool, str]:
        """
        Queries active positions and calculates total exposure.
        """
        try:
            broker = broker_manager.get_active()
            resp = await broker.get_positions()
            if resp.success and isinstance(resp.data, list):
                positions = resp.data
                open_positions_count = len(positions)
                
                # Check max open positions limit
                # If this is a new position (symbol not in current positions), check the count limit
                has_position = any(pos.get("symbol") == symbol for pos in positions)
                if not has_position and open_positions_count >= self.max_open_positions:
                    return False, f"Max open positions count limit ({self.max_open_positions}) reached"
                
                # Check total exposure
                total_exposure = sum(float(pos.get("quantity", 0.0)) * float(pos.get("avg_price", 0.0)) for pos in positions)
                if total_exposure + additional_cost > self.max_exposure_usd:
                    return False, f"Total exposure limit exceeded. Current: {total_exposure}, Attempted additional: {additional_cost}, Max: {self.max_exposure_usd}"
                
        except Exception as e:
            logger.error("Failed to query exposure info from broker", error=str(e))
            
        return True, ""


class DailyLossManager:
    """
    Enforces maximum daily and weekly loss limits.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.daily_loss_limit = float(config.get("daily_loss_limit_usd", 500.0))
        self.weekly_loss_limit = float(config.get("weekly_loss_limit_usd", 2000.0))
        
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self._lock = asyncio.Lock()

    async def update_pnl(self, realized_pnl: float) -> None:
        async with self._lock:
            self.daily_pnl += realized_pnl
            self.weekly_pnl += realized_pnl

    async def validate_limits(self) -> tuple[bool, str, float, float]:
        """
        Returns: (allowed, reason, daily_pnl, weekly_pnl)
        """
        async with self._lock:
            # If daily PNL is negative and exceeds daily loss limit
            if self.daily_pnl < 0 and abs(self.daily_pnl) >= self.daily_loss_limit:
                return False, f"Daily loss limit breached: {self.daily_pnl} <= -{self.daily_loss_limit}", self.daily_pnl, self.weekly_pnl
            
            # If weekly PNL is negative and exceeds weekly loss limit
            if self.weekly_pnl < 0 and abs(self.weekly_pnl) >= self.weekly_loss_limit:
                return False, f"Weekly loss limit breached: {self.weekly_pnl} <= -{self.weekly_loss_limit}", self.daily_pnl, self.weekly_pnl
            
            return True, "", self.daily_pnl, self.weekly_pnl

    async def reset_daily(self) -> None:
        async with self._lock:
            self.daily_pnl = 0.0

    async def reset_weekly(self) -> None:
        async with self._lock:
            self.weekly_pnl = 0.0


class TradeLimitManager:
    """
    Enforces trade limits, cooldowns, and consecutive losses.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.daily_trade_limit = int(config.get("daily_trade_limit", 20))
        self.max_consecutive_losses = int(config.get("max_consecutive_losses", 5))
        self.cooldown_period_min = float(config.get("cooldown_period_minutes", 30.0))
        
        self.daily_trades_count = 0
        self.consecutive_losses = 0
        self.cooldown_until: Optional[datetime] = None
        self._lock = asyncio.Lock()

    async def record_trade_execution(self, realized_pnl: float) -> None:
        async with self._lock:
            self.daily_trades_count += 1
            if realized_pnl < 0:
                self.consecutive_losses += 1
                if self.consecutive_losses >= self.max_consecutive_losses:
                    # Trigger cooldown
                    self.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=self.cooldown_period_min)
                    logger.warning(f"Consecutive losses limit reached. Cooldown until {self.cooldown_until}")
            else:
                self.consecutive_losses = 0

    async def validate_trade_limits(self) -> tuple[bool, str]:
        async with self._lock:
            # Check cooldown
            if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
                remaining = self.cooldown_until - datetime.now(timezone.utc)
                return False, f"Risk Engine Cooldown active. Remaining: {remaining.total_seconds():.1f}s"
            
            # Check daily trade limit
            if self.daily_trades_count >= self.daily_trade_limit:
                return False, f"Daily trade limit ({self.daily_trade_limit}) reached"
            
            # Check consecutive losses
            if self.consecutive_losses >= self.max_consecutive_losses:
                return False, f"Max consecutive losses ({self.max_consecutive_losses}) reached"
                
            return True, ""

    async def reset_daily(self) -> None:
        async with self._lock:
            self.daily_trades_count = 0
            self.consecutive_losses = 0
            self.cooldown_until = None


class PortfolioRiskManager:
    """
    Evaluates global portfolio risk checks such as portfolio correlation limit placeholders.
    """
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    async def validate_portfolio_risk(self) -> tuple[bool, str]:
        # Placeholders for future correlation checks or portfolio level VaR / Beta calculations
        return True, ""
