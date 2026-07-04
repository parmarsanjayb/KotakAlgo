import asyncio
import random
from typing import Any, Callable, Dict, Optional
from market.models import FeedDisconnectedEvent, FeedConnectedEvent
from market.health import FeedHealthMonitor
from core.bus import event_bus, EventModel
from core.logging import get_logger

logger = get_logger("websocket_stream")

class WebSocketStreamManager:
    """
    Manages the live feed WebSocket connection.
    Generates mock price ticks in simulation mode.
    Notifies FeedHealthMonitor on connect / disconnect / stale data.
    Auto-reconnects up to _max_reconnects before raising FeedDisconnectedEvent.
    """

    def __init__(
        self,
        on_raw_tick:    Callable[[Dict[str, Any]], Any],
        health_monitor: FeedHealthMonitor,
    ) -> None:
        self._on_raw_tick    = on_raw_tick
        self._health         = health_monitor
        self._connected:     bool = False
        self._running:       bool = False
        self._max_reconnects: int = 5
        self._reconnect_attempts: int = 0
        self._loop_task: Optional[asyncio.Task] = None

        # Mock price seed
        self._prices = {"BTCUSD": 65000.0, "ETHUSD": 3500.0, "NIFTY50": 24200.0, "BANKNIFTY": 52000.0}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        logger.info("Connecting to market data feed...")
        await asyncio.sleep(0.1)
        self._connected = True
        self._reconnect_attempts = 0
        self._health.signal_connected()
        logger.info("Market data feed connected.")

    async def start(self) -> None:
        self._running = True
        await self.connect()
        self._loop_task = asyncio.create_task(self._stream_loop())

    async def stop(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._connected = False
        self._health.signal_disconnected("Graceful shutdown")
        logger.info("Market data feed stopped.")

    def is_connected(self) -> bool:
        return self._connected

    # ── Stream loop ───────────────────────────────────────────────────────────

    async def _stream_loop(self) -> None:
        while self._running:
            try:
                if not self._connected:
                    await self._reconnect()
                    continue

                await asyncio.sleep(0.5)

                # Simulate 0.5 % random drop
                if random.random() < 0.005:
                    logger.warning("Simulated feed drop.")
                    self._connected = False
                    self._health.signal_disconnected("Simulated drop")
                    continue

                sym    = random.choice(list(self._prices.keys()))
                change = random.uniform(-0.0003, 0.0003)
                self._prices[sym] *= (1.0 + change)
                p = round(self._prices[sym], 2)

                raw = {
                    "symbol":        sym,
                    "price":         p,
                    "ltp":           p,
                    "bid":           round(p * 0.9998, 2),
                    "ask":           round(p * 1.0002, 2),
                    "volume":        round(random.uniform(1.0, 30.0), 3),
                    "open_interest": round(random.uniform(1000, 8000), 0) if sym in ("NIFTY50", "BANKNIFTY") else 0.0,
                    "vwap":          p,
                    "atp":           p,
                    "open":          round(self._prices[sym] * random.uniform(0.998, 1.002), 2),
                    "high":          round(p * random.uniform(1.000, 1.003), 2),
                    "low":           round(p * random.uniform(0.997, 1.000), 2),
                    "close":         p,
                    "prev_close":    round(p * random.uniform(0.995, 1.005), 2),
                }
                self._health.record_tick()
                asyncio.create_task(self._on_raw_tick(raw))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Stream loop error", error=str(e))
                self._connected = False
                await asyncio.sleep(1.0)

    async def _reconnect(self) -> None:
        self._reconnect_attempts += 1
        logger.info("Reconnecting feed...", attempt=self._reconnect_attempts, max=self._max_reconnects)
        await asyncio.sleep(1.0)

        if self._reconnect_attempts >= self._max_reconnects:
            logger.error("Max reconnect attempts reached. Feed declared dead.")
            await event_bus.publish(EventModel(
                event_type   = "feed_disconnected",
                source_agent = "websocket_stream",
                payload      = FeedDisconnectedEvent(reason="Max reconnect attempts exceeded").model_dump(),
                priority     = 0,
            ))
            await asyncio.sleep(10.0)
            self._reconnect_attempts = 0
        else:
            if random.random() < 0.85:
                self._connected = True
                self._reconnect_attempts = 0
                self._health.signal_connected()
                logger.info("Feed reconnected successfully.")
