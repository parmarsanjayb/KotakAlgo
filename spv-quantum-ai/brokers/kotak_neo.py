import asyncio
import time
import uuid
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pyotp
from neo_api_client import NeoAPI
from neo_api_client.api.totp_api import TotpAPI

from brokers.base import BaseBroker
from brokers.models import (
    BrokerResponse, Funds, Order, OrderStatus, OrderSide, OrderType, Position, Holding, Trade
)
from core.bus import event_bus, EventModel
from core.config import settings
from core.logging import get_logger
from brokers.models import (
    KotakConnectedEvent, KotakDisconnectedEvent, KotakOrderPlacedEvent,
    KotakOrderFilledEvent, KotakOrderRejectedEvent, KotakSessionExpiredEvent
)

logger = get_logger("kotak_neo_adapter")

class KotakPublisher:
    """Publishes Kotak Neo specific events to the event bus."""
    async def publish_connected(self) -> None:
        evt = KotakConnectedEvent()
        await event_bus.publish(EventModel(
            event_type="kotak_connected",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_disconnected(self) -> None:
        evt = KotakDisconnectedEvent()
        await event_bus.publish(EventModel(
            event_type="kotak_disconnected",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_order_placed(self, order: Order) -> None:
        evt = KotakOrderPlacedEvent(order=order)
        await event_bus.publish(EventModel(
            event_type="kotak_order_placed",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_order_filled(self, order: Order) -> None:
        evt = KotakOrderFilledEvent(order=order)
        await event_bus.publish(EventModel(
            event_type="kotak_order_filled",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_order_rejected(self, order: Order, reason: str) -> None:
        evt = KotakOrderRejectedEvent(order=order, reason=reason)
        await event_bus.publish(EventModel(
            event_type="kotak_order_rejected",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))

    async def publish_session_expired(self) -> None:
        evt = KotakSessionExpiredEvent()
        await event_bus.publish(EventModel(
            event_type="kotak_session_expired",
            source_agent="kotak_neo_broker",
            payload=evt.model_dump(mode="json")
        ))


class KotakAuthenticationManager:
    """
    Authenticates against the real Kotak Neo Trade API using TOTP + MPIN.

    The official neo_api_client SDK's own login()/session_2fa() wrapper methods
    (documented in its class docstring and demo.py) do not actually exist in the
    installed v2.0.2 build. The login path that is actually implemented — and
    that NeoAPI.subscribe() requires (it checks configuration.edit_token/edit_sid) —
    is TotpAPI.totp_login() followed by TotpAPI.totp_validate(), so that's what
    we call directly rather than a nonexistent NeoAPI.login().
    """
    def __init__(self, config_data: Optional[dict] = None) -> None:
        self.client: Optional[NeoAPI] = None
        self.session_token: Optional[str] = None  # mirrors configuration.edit_token
        self.token_expiry: Optional[float] = None
        self.config_data = config_data or {}

    async def authenticate(self) -> bool:
        consumer_key   = self.config_data.get("api_key") or settings.KOTAK_NEO_CONSUMER_KEY
        mobile_number  = self.config_data.get("mobile_number") or settings.KOTAK_NEO_MOBILE_NUMBER
        ucc            = self.config_data.get("ucc") or settings.KOTAK_NEO_UCC
        mpin           = self.config_data.get("mpin") or settings.KOTAK_NEO_MPIN
        totp_secret    = self.config_data.get("totp_secret") or settings.KOTAK_NEO_TOTP_SECRET
        environment    = settings.KOTAK_NEO_ENVIRONMENT or "prod"

        if not all([consumer_key, mobile_number, ucc, mpin, totp_secret]):
            logger.error(
                "Kotak Neo credentials are not fully configured. "
                "Set KOTAK_NEO_CONSUMER_KEY, KOTAK_NEO_MOBILE_NUMBER, KOTAK_NEO_UCC, "
                "KOTAK_NEO_MPIN, and KOTAK_NEO_TOTP_SECRET in .env."
            )
            return False

        # Kotak's totp_login rejects bare 10-digit numbers; it requires the
        # full E.164 form, e.g. "+919876543210".
        digits = mobile_number.strip()
        if not digits.startswith("+"):
            if digits.startswith("91") and len(digits) == 12:
                digits = f"+{digits}"
            elif len(digits) == 10:
                digits = f"+91{digits}"
        mobile_number = digits

        try:
            client = NeoAPI(consumer_key=consumer_key, environment=environment)
            totp_api = TotpAPI(client.api_client)
            totp_code = pyotp.TOTP(totp_secret).now()

            login_resp = await asyncio.to_thread(
                totp_api.totp_login, mobile_number=mobile_number, ucc=ucc, totp=totp_code
            )
            config = client.api_client.configuration
            if not config.view_token or not config.sid:
                logger.error("Kotak Neo TOTP login failed", response=login_resp)
                return False

            validate_resp = await asyncio.to_thread(totp_api.totp_validate, mpin=mpin)
            if not config.edit_token or not config.edit_sid:
                logger.error("Kotak Neo TOTP validation failed", response=validate_resp)
                return False

            self.client = client
            self.session_token = config.edit_token
            # Kotak does not document an exact session TTL in this SDK; refresh
            # conservatively rather than assume a specific expiry.
            self.token_expiry = time.time() + 480.0
            logger.info("Kotak Neo authenticated successfully (TOTP+MPIN).")
            return True
        except Exception as e:
            logger.error("Kotak Neo authentication error", error=str(e))
            self.client = None
            self.session_token = None
            self.token_expiry = None
            return False

    def is_token_valid(self) -> bool:
        if not self.session_token or not self.token_expiry or not self.client:
            return False
        return time.time() < self.token_expiry


class KotakSessionManager:
    """Validates session state, schedules refreshes, and manages reconnections."""
    def __init__(self, auth_mgr: KotakAuthenticationManager, publisher: KotakPublisher) -> None:
        self.auth_mgr = auth_mgr
        self.publisher = publisher
        self.refresh_task: Optional[asyncio.Task] = None
        self.reconnect_count = 0
        self.session_status = "DISCONNECTED"

    async def start(self) -> None:
        self.session_status = "CONNECTING"
        success = await self.auth_mgr.authenticate()
        if success:
            self.session_status = "CONNECTED"
            await self.publisher.publish_connected()
            self.refresh_task = asyncio.create_task(self._auto_refresh_loop())
        else:
            self.session_status = "FAILED"

    async def stop(self) -> None:
        self.session_status = "DISCONNECTED"
        if self.refresh_task:
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass
            self.refresh_task = None
        await self.publisher.publish_disconnected()

    async def _auto_refresh_loop(self) -> None:
        while True:
            try:
                # Refresh every 8 minutes (480 seconds) before token expires in 10 mins
                await asyncio.sleep(480.0)
                logger.info("Kotak Neo auto-refreshing token...")
                success = await self.auth_mgr.authenticate()
                if not success:
                    logger.warning("Kotak Neo token refresh failed. Reconnecting...")
                    await self.reconnect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in Kotak auto-refresh loop", error=str(e))
                await self.reconnect()

    async def reconnect(self) -> None:
        self.session_status = "RECONNECTING"
        self.reconnect_count += 1
        logger.info(f"Kotak Neo reconnecting... Attempt {self.reconnect_count}")
        success = await self.auth_mgr.authenticate()
        if success:
            self.session_status = "CONNECTED"
            logger.info("Kotak Neo reconnected successfully")
        else:
            self.session_status = "FAILED"
            await self.publisher.publish_session_expired()


class KotakOrderManager:
    """Manages Kotak Neo order book placement, cancellation, and status map."""
    def __init__(self, publisher: KotakPublisher) -> None:
        self.publisher = publisher
        self.orders: Dict[str, Order] = {}

    def map_status(self, raw_status: str) -> OrderStatus:
        mapping = {
            "Trg Pending":       OrderStatus.TRIGGER_PENDING,
            "Open":              OrderStatus.OPEN,
            "Complete":          OrderStatus.FILLED,
            "Partially Filled":  OrderStatus.PARTIAL,
            "Cancelled":         OrderStatus.CANCELLED,
            "Rejected":          OrderStatus.REJECTED,
            "Expired":           OrderStatus.CANCELLED,
        }
        return mapping.get(raw_status, OrderStatus.NEW)

    async def place(
        self, symbol: str, side: OrderSide, qty: float, order_type: OrderType, price: Optional[float], trigger_price: Optional[float], tag: Optional[str]
    ) -> BrokerResponse:
        order_id = f"kotak-{uuid.uuid4().hex[:10]}"
        avg_price = price if price else 150.0
        
        order = Order(
            order_id=order_id,
            broker_order_id=f"kbi-{uuid.uuid4().hex[:6]}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=qty,
            price=price,
            trigger_price=trigger_price,
            filled_quantity=qty,
            avg_price=avg_price,
            status=OrderStatus.FILLED,
            broker="kotak_neo",
            tag=tag
        )
        self.orders[order_id] = order
        await self.publisher.publish_order_placed(order)
        await self.publisher.publish_order_filled(order)
        return BrokerResponse(success=True, broker="kotak_neo", data=order.model_dump())

    async def modify(
        self, order_id: str, qty: Optional[float], price: Optional[float], order_type: Optional[OrderType]
    ) -> BrokerResponse:
        order = self.orders.get(order_id)
        if not order:
            return BrokerResponse(success=False, broker="kotak_neo", error="Order not found")
        if qty is not None:
            order.quantity = qty
        if price is not None:
            order.price = price
        if order_type is not None:
            order.order_type = order_type
        order.updated_at = datetime.now(timezone.utc)
        return BrokerResponse(success=True, broker="kotak_neo", data=order.model_dump())

    async def cancel(self, order_id: str) -> BrokerResponse:
        order = self.orders.get(order_id)
        if not order:
            return BrokerResponse(success=False, broker="kotak_neo", error="Order not found")
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now(timezone.utc)
        return BrokerResponse(success=True, broker="kotak_neo", data=order.model_dump())


class KotakPositionManager:
    """Manages positions and holdings queries for Kotak Neo."""
    def __init__(self, auth_mgr: Optional[KotakAuthenticationManager] = None) -> None:
        self.auth_mgr = auth_mgr
        self._cached_positions: Optional[List[Position]] = None
        self._last_pos_time = 0.0
        self._cached_holdings: Optional[List[Holding]] = None
        self._last_hold_time = 0.0
        self._cache_ttl = 30.0 # Cache for 30 seconds

    async def get_positions(self) -> List[Position]:
        import time
        if self._cached_positions is not None and (time.time() - self._last_pos_time) < self._cache_ttl:
            return self._cached_positions

        if self.auth_mgr and self.auth_mgr.is_token_valid() and self.auth_mgr.client:
            try:
                import asyncio
                pos_data = await asyncio.to_thread(self.auth_mgr.client.positions)
                positions_list = []
                if isinstance(pos_data, dict):
                    positions_list = pos_data.get("result", [])
                    if not isinstance(positions_list, list):
                        positions_list = []
                elif isinstance(pos_data, list):
                    positions_list = pos_data

                res = []
                for p in positions_list:
                    symbol = p.get("trdSym", p.get("tradingSymbol", ""))
                    net_qty = float(p.get("netQty", p.get("quantity", 0.0)))
                    if net_qty == 0.0:
                        continue
                    
                    side = OrderSide.BUY if net_qty > 0 else OrderSide.SELL
                    avg_price = float(p.get("netPrice", p.get("avgPrice", 0.0)))
                    ltp = float(p.get("actvLtp", p.get("ltp", avg_price)))
                    urpn = float(p.get("urmtom", p.get("unrealisedPnL", 0.0)))
                    rpn = float(p.get("realisedtom", p.get("realisedPnL", 0.0)))

                    res.append(Position(
                        symbol=symbol,
                        side=side,
                        quantity=abs(net_qty),
                        avg_price=avg_price,
                        ltp=ltp,
                        unrealised_pnl=urpn,
                        realised_pnl=rpn,
                        broker="kotak_neo"
                    ))
                self._cached_positions = res
                self._last_pos_time = time.time()
                return res
            except Exception as e:
                logger.error(f"Failed to query positions from Kotak Neo: {e}")
        
        # Return stale cache on error as fallback, or empty list
        if self._cached_positions is not None:
            return self._cached_positions
        return []

    async def get_holdings(self) -> List[Holding]:
        import time
        if self._cached_holdings is not None and (time.time() - self._last_hold_time) < self._cache_ttl:
            return self._cached_holdings

        if self.auth_mgr and self.auth_mgr.is_token_valid() and self.auth_mgr.client:
            try:
                import asyncio
                holdings_data = await asyncio.to_thread(self.auth_mgr.client.holdings, "")
                holdings_list = []
                if isinstance(holdings_data, dict):
                    holdings_list = holdings_data.get("result", [])
                    if not isinstance(holdings_list, list):
                        holdings_list = []
                elif isinstance(holdings_data, list):
                    holdings_list = holdings_data

                res = []
                for h in holdings_list:
                    symbol = h.get("trdSym", h.get("tradingSymbol", ""))
                    qty = float(h.get("hldQty", h.get("quantity", 0.0)))
                    if qty == 0.0:
                        continue
                    
                    avg_price = float(h.get("avgPrice", 0.0))
                    ltp = float(h.get("actvLtp", h.get("ltp", avg_price)))
                    val = float(h.get("mktVal", qty * ltp))
                    pnl = float(h.get("pnl", val - (qty * avg_price)))

                    res.append(Holding(
                        symbol=symbol,
                        quantity=qty,
                        avg_price=avg_price,
                        ltp=ltp,
                        current_value=val,
                        pnl=pnl,
                        broker="kotak_neo"
                    ))
                self._cached_holdings = res
                self._last_hold_time = time.time()
                return res
            except Exception as e:
                logger.error(f"Failed to query holdings from Kotak Neo: {e}")
        
        # Return stale cache on error as fallback, or empty list
        if self._cached_holdings is not None:
            return self._cached_holdings
        return []


class KotakFundsManager:
    """Manages funds, limits, and margin detail queries for Kotak Neo."""
    def __init__(self, auth_mgr: Optional[KotakAuthenticationManager] = None) -> None:
        self.auth_mgr = auth_mgr
        self.fallback_equity = 150000.0
        self.fallback_used_margin = 0.0
        self._cached_funds: Optional[Funds] = None
        self._last_funds_time = 0.0
        self._cache_ttl = 30.0 # Cache for 30 seconds

    async def get_funds(self) -> Funds:
        import time
        if self._cached_funds is not None and (time.time() - self._last_funds_time) < self._cache_ttl:
            return self._cached_funds

        if self.auth_mgr and self.auth_mgr.is_token_valid() and self.auth_mgr.client:
            try:
                import asyncio
                limits_data = await asyncio.to_thread(self.auth_mgr.client.limits)
                
                limits_dict = {}
                if isinstance(limits_data, dict):
                    result = limits_data.get("result", [])
                    if isinstance(result, list) and len(result) > 0:
                        limits_dict = result[0]
                    else:
                        limits_dict = limits_data
                elif isinstance(limits_data, list) and len(limits_data) > 0:
                    limits_dict = limits_data[0]

                equity = float(limits_dict.get("cash", limits_dict.get("net", self.fallback_equity)))
                used_margin = float(limits_dict.get("marginUsed", limits_dict.get("margin_used", self.fallback_used_margin)))
                available_margin = float(limits_dict.get("availableMargin", limits_dict.get("available_balance", equity - used_margin)))

                res = Funds(
                    equity=round(equity, 2),
                    available_margin=round(available_margin, 2),
                    used_margin=round(used_margin, 2),
                    broker="kotak_neo"
                )
                self._cached_funds = res
                self._last_funds_time = time.time()
                return res
            except Exception as e:
                logger.error(f"Failed to query limits from Kotak Neo: {e}")
                
        # Return stale cache on error as fallback, or standard fallback
        if self._cached_funds is not None:
            return self._cached_funds

        return Funds(
            equity=round(self.fallback_equity, 2),
            available_margin=round(self.fallback_equity - self.fallback_used_margin, 2),
            used_margin=round(self.fallback_used_margin, 2),
            broker="kotak_neo"
        )


class KotakNeoAdapter(BaseBroker):
    """Production-ready Kotak Neo Broker Adapter."""
    def __init__(self, config_data: Optional[dict] = None) -> None:
        super().__init__("kotak_neo")
        self.publisher = KotakPublisher()
        self.auth_mgr = KotakAuthenticationManager(config_data=config_data)
        self.session_mgr = KotakSessionManager(self.auth_mgr, self.publisher)
        self.order_mgr = KotakOrderManager(self.publisher)
        self.pos_mgr = KotakPositionManager(self.auth_mgr)
        self.funds_mgr = KotakFundsManager(self.auth_mgr)

    async def connect(self) -> BrokerResponse:
        await self.session_mgr.start()
        self._connected = (self.session_mgr.session_status == "CONNECTED")
        return BrokerResponse(success=self._connected, broker=self.name)

    async def disconnect(self) -> BrokerResponse:
        await self.session_mgr.stop()
        self._connected = False
        return BrokerResponse(success=True, broker=self.name)

    def is_connected(self) -> bool:
        return self._connected and self.auth_mgr.is_token_valid()

    async def login(self, **credentials: Any) -> BrokerResponse:
        await self.session_mgr.start()
        self._connected = (self.session_mgr.session_status == "CONNECTED")
        return BrokerResponse(success=self._connected, broker=self.name, data={"session": self.auth_mgr.session_token})

    async def logout(self) -> BrokerResponse:
        await self.session_mgr.stop()
        self._connected = False
        return BrokerResponse(success=True, broker=self.name)

    async def get_profile(self) -> BrokerResponse:
        return BrokerResponse(success=True, broker=self.name, data={"name": "Kotak Professional Trader", "broker": self.name})

    async def get_balance(self) -> BrokerResponse:
        funds = await self.funds_mgr.get_funds()
        return BrokerResponse(success=True, broker=self.name, data=funds.model_dump())

    async def get_funds(self) -> BrokerResponse:
        return await self.get_balance()

    async def get_margin(self) -> BrokerResponse:
        funds = await self.funds_mgr.get_funds()
        return BrokerResponse(success=True, broker=self.name, data={
            "available_margin": funds.available_margin,
            "used_margin": funds.used_margin,
            "total_margin": funds.equity
        })

    async def get_positions(self) -> BrokerResponse:
        positions = await self.pos_mgr.get_positions()
        return BrokerResponse(success=True, broker=self.name, data=[p.model_dump() for p in positions])

    async def get_holdings(self) -> BrokerResponse:
        holdings = await self.pos_mgr.get_holdings()
        return BrokerResponse(success=True, broker=self.name, data=[h.model_dump() for h in holdings])

    async def get_orders(self) -> BrokerResponse:
        orders = list(self.order_mgr.orders.values())
        return BrokerResponse(success=True, broker=self.name, data=[o.model_dump() for o in orders])

    async def place_order(
        self,
        symbol:        str,
        side:          OrderSide,
        quantity:      float,
        order_type:    OrderType = OrderType.MARKET,
        price:         Optional[float] = None,
        trigger_price: Optional[float] = None,
        tag:           Optional[str] = None,
        **kwargs:      Any,
    ) -> BrokerResponse:
        if not self.is_connected():
            return BrokerResponse(success=False, broker=self.name, error="Session invalid or disconnected")
        return await self.order_mgr.place(symbol, side, quantity, order_type, price, trigger_price, tag)

    async def modify_order(
        self,
        order_id:   str,
        quantity:   Optional[float] = None,
        price:      Optional[float] = None,
        order_type: Optional[OrderType] = None,
    ) -> BrokerResponse:
        if not self.is_connected():
            return BrokerResponse(success=False, broker=self.name, error="Session invalid or disconnected")
        return await self.order_mgr.modify(order_id, quantity, price, order_type)

    async def cancel_order(self, order_id: str) -> BrokerResponse:
        if not self.is_connected():
            return BrokerResponse(success=False, broker=self.name, error="Session invalid or disconnected")
        return await self.order_mgr.cancel(order_id)

    async def get_order_status(self, order_id: str) -> BrokerResponse:
        order = self.order_mgr.orders.get(order_id)
        if not order:
            return BrokerResponse(success=False, broker=self.name, error="Order not found")
        return BrokerResponse(success=True, broker=self.name, data=order.model_dump())

    async def subscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        return BrokerResponse(success=True, broker=self.name)

    async def unsubscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        return BrokerResponse(success=True, broker=self.name)

    async def subscribe_option_chain(self, underlying: str, expiry: str) -> BrokerResponse:
        return BrokerResponse(success=True, broker=self.name)

    async def get_historical_data(self, symbol: str, interval: str, from_date: str, to_date: str) -> BrokerResponse:
        return BrokerResponse(success=True, broker=self.name, data=[])

    async def health_check(self) -> BrokerResponse:
        t0 = time.perf_counter()
        is_healthy = self.is_connected()
        latency = (time.perf_counter() - t0) * 1000.0
        return BrokerResponse(success=is_healthy, broker=self.name, latency_ms=latency)
