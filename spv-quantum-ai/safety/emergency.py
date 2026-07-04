from typing import Dict, Any
from safety.publisher import SafetyPublisher
from core.logging import get_logger

logger = get_logger("emergency_manager")

class EmergencyManager:
    """Manages system-wide emergency overrides, halts, kills, and liquidations."""
    def __init__(self, publisher: SafetyPublisher) -> None:
        self.publisher = publisher
        self.kill_switch_active = False
        self.trading_paused = False
        self.new_entries_disabled = False

    async def trigger_kill_switch(self, reason: str = "Manual emergency trigger") -> None:
        self.kill_switch_active = True
        self.new_entries_disabled = True
        self.trading_paused = True
        logger.warning(f"EMERGENCY KILL SWITCH TRIGGERED: {reason}")
        await self.publisher.publish_emergency("kill_switch", reason)
        # Liquidate everything
        closed_count = await self.close_all_positions()
        logger.warning(f"Liquidated {closed_count} active positions during kill switch trigger.")

    async def reset_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.trading_paused = False
        self.new_entries_disabled = False
        logger.info("Emergency kill switch reset successfully.")
        await self.publisher.publish_emergency("reset_kill_switch", "Emergency kill switch reset")

    async def pause_trading(self) -> None:
        self.trading_paused = True
        logger.info("Trading PAUSED by emergency control.")
        await self.publisher.publish_emergency("pause", "Trading paused")

    async def resume_trading(self) -> None:
        if self.kill_switch_active:
            logger.error("Cannot resume trading while Emergency Kill Switch is active.")
            return
        self.trading_paused = False
        logger.info("Trading RESUMED.")
        await self.publisher.publish_emergency("resume", "Trading resumed")

    async def disable_new_entries(self) -> None:
        self.new_entries_disabled = True
        logger.info("New trade entries DISABLED.")
        await self.publisher.publish_emergency("disable_entries", "New entries disabled")

    async def enable_new_entries(self) -> None:
        self.new_entries_disabled = False
        logger.info("New trade entries ENABLED.")
        await self.publisher.publish_emergency("enable_entries", "New entries enabled")

    async def close_all_positions(self) -> int:
        """Immediately liquidates all open portfolio positions at market price."""
        from portfolio.engine import portfolio_engine
        from brokers import broker_engine
        from brokers.models import OrderSide, OrderType
        
        positions = await portfolio_engine.positions.get_all_positions()
        closed_count = 0
        for pos in positions:
            qty = pos.quantity
            if qty == 0:
                continue
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY
            resp = await broker_engine.place_order(
                symbol=pos.symbol,
                side=side,
                quantity=abs(qty),
                order_type=OrderType.MARKET,
                tag="emergency_close_all"
            )
            if resp.success:
                closed_count += 1
                logger.info(f"Liquidated position: {pos.symbol} | qty: {qty} | side: {side}")
            else:
                logger.error(f"Failed to liquidate position: {pos.symbol} | error: {resp.error}")
        return closed_count
