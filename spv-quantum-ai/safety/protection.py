import asyncio
from typing import Dict, Any, Optional
from core.logging import get_logger
from brokers import broker_engine
from brokers.models import OrderSide, OrderType
from market.manager import market_data_manager
from safety.publisher import SafetyPublisher

logger = get_logger("protection_manager")

class ProtectionManager:
    """Tracks internal hidden stop-losses and manages trailing profit protections."""
    def __init__(self, config: Dict[str, Any], publisher: SafetyPublisher) -> None:
        self.config = config
        self.publisher = publisher
        self.active_sls: Dict[str, Dict[str, Any]] = {}  # symbol -> SL config/state
        self.monitor_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("ProtectionManager started monitoring hidden stop-losses.")

    async def stop(self) -> None:
        self._running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            self.monitor_task = None
        logger.info("ProtectionManager stopped monitoring.")

    def register_position(self, symbol: str, side: str, qty: float, entry_price: float) -> None:
        """Sets up the initial stop-loss parameters internally."""
        if qty == 0:
            self.active_sls.pop(symbol, None)
            return

        sl_pct = float(self.config.get("hidden_sl_pct", 2.0))
        if side.upper() in ("BUY", "LONG"):
            sl_price = entry_price * (1 - sl_pct / 100.0)
            highest_price = entry_price
            lowest_price = entry_price
        else:
            sl_price = entry_price * (1 + sl_pct / 100.0)
            highest_price = entry_price
            lowest_price = entry_price

        self.active_sls[symbol] = {
            "symbol": symbol,
            "side": side.upper(),
            "qty": abs(qty),
            "entry_price": entry_price,
            "sl_price": sl_price,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "sl_shifted_to_be": False,
            "profit_locked": False,
        }
        logger.info(f"Registered hidden SL for {symbol} | side: {side} | entry: {entry_price} | sl: {sl_price}")

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(0.2)
                for symbol, sl_data in list(self.active_sls.items()):
                    ltp = await market_data_manager.get_ltp(symbol)
                    if ltp <= 0:
                        continue
                    await self._evaluate_price_update(symbol, ltp)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in protection monitor loop", error=str(e))

    async def _evaluate_price_update(self, symbol: str, ltp: float) -> None:
        sl_data = self.active_sls.get(symbol)
        if not sl_data:
            return

        side = sl_data["side"]
        qty = sl_data["qty"]
        entry = sl_data["entry_price"]
        sl_price = sl_data["sl_price"]
        
        # Trailing/protection parameters from config
        trailing_pct = float(self.config.get("trailing_stop_pct", 1.0))
        be_shift_pct = float(self.config.get("break_even_shift_pct", 1.5))
        profit_lock_pct = float(self.config.get("profit_lock_pct", 3.0))

        if side in ("BUY", "LONG"):
            # Check hidden stop-loss trigger
            if ltp <= sl_price:
                await self._trigger_market_exit(symbol, OrderSide.SELL, qty, sl_price, ltp, "Long hidden stop-loss triggered")
                return

            # Trailing stop update
            if ltp > sl_data["highest_price"]:
                sl_data["highest_price"] = ltp
                new_sl = ltp * (1 - trailing_pct / 100.0)
                if new_sl > sl_price:
                    sl_data["sl_price"] = new_sl
                    logger.info(f"Trailing SL updated for {symbol}: {sl_price:.2f} -> {new_sl:.2f}")
                    await self.publisher.publish_trailing_updated(symbol, sl_price, new_sl, ltp, "Trailing Stop")
                    sl_price = new_sl

            # Break-even Shift
            if not sl_data["sl_shifted_to_be"]:
                profit_pct = (ltp - entry) / entry * 100.0
                if profit_pct >= be_shift_pct:
                    if entry > sl_price:
                        sl_data["sl_price"] = entry
                        logger.info(f"Break-even shift triggered for {symbol}: SL shifted to entry price {entry:.2f}")
                        await self.publisher.publish_trailing_updated(symbol, sl_price, entry, ltp, "Break-even Shift")
                        sl_price = entry
                    sl_data["sl_shifted_to_be"] = True

            # Profit Lock
            if not sl_data["profit_locked"]:
                profit_pct = (ltp - entry) / entry * 100.0
                if profit_pct >= profit_lock_pct:
                    locked_sl = entry * (1 + profit_lock_pct * 0.5 / 100.0)
                    if locked_sl > sl_price:
                        sl_data["sl_price"] = locked_sl
                        logger.info(f"Profit lock triggered for {symbol}: SL moved to locked profit level {locked_sl:.2f}")
                        await self.publisher.publish_trailing_updated(symbol, sl_price, locked_sl, ltp, "Profit Lock")
                    sl_data["profit_locked"] = True

        else:  # SHORT
            # Check hidden stop-loss trigger
            if ltp >= sl_price:
                await self._trigger_market_exit(symbol, OrderSide.BUY, qty, sl_price, ltp, "Short hidden stop-loss triggered")
                return

            # Trailing stop update
            if ltp < sl_data["lowest_price"]:
                sl_data["lowest_price"] = ltp
                new_sl = ltp * (1 + trailing_pct / 100.0)
                if new_sl < sl_price:
                    sl_data["sl_price"] = new_sl
                    logger.info(f"Trailing SL updated for {symbol}: {sl_price:.2f} -> {new_sl:.2f}")
                    await self.publisher.publish_trailing_updated(symbol, sl_price, new_sl, ltp, "Trailing Stop")
                    sl_price = new_sl

            # Break-even Shift
            if not sl_data["sl_shifted_to_be"]:
                profit_pct = (entry - ltp) / entry * 100.0
                if profit_pct >= be_shift_pct:
                    if entry < sl_price:
                        sl_data["sl_price"] = entry
                        logger.info(f"Break-even shift triggered for {symbol}: SL shifted to entry price {entry:.2f}")
                        await self.publisher.publish_trailing_updated(symbol, sl_price, entry, ltp, "Break-even Shift")
                        sl_price = entry
                    sl_data["sl_shifted_to_be"] = True

            # Profit Lock
            if not sl_data["profit_locked"]:
                profit_pct = (entry - ltp) / entry * 100.0
                if profit_pct >= profit_lock_pct:
                    locked_sl = entry * (1 - profit_lock_pct * 0.5 / 100.0)
                    if locked_sl < sl_price:
                        sl_data["sl_price"] = locked_sl
                        logger.info(f"Profit lock triggered for {symbol}: SL moved to locked profit level {locked_sl:.2f}")
                        await self.publisher.publish_trailing_updated(symbol, sl_price, locked_sl, ltp, "Profit Lock")
                    sl_data["profit_locked"] = True

    async def _trigger_market_exit(self, symbol: str, side: OrderSide, qty: float, trigger_price: float, exit_price: float, msg: str) -> None:
        logger.warning(f"HIDDEN STOP-LOSS TRIGGERED: {symbol} | side: {side} | qty: {qty} | trigger: {trigger_price}")
        self.active_sls.pop(symbol, None)
        
        # Place market exit order
        resp = await broker_engine.place_order(
            symbol=symbol,
            side=side,
            quantity=qty,
            order_type=OrderType.MARKET,
            tag="hidden_sl_exit"
        )
        if resp.success:
            logger.warning(f"Successfully exited position for {symbol} at market price.")
        else:
            logger.error(f"Failed to execute emergency hidden SL market exit for {symbol}: {resp.error}")
            
        await self.publisher.publish_hidden_stop(symbol, side.value, qty, trigger_price, exit_price, msg)
