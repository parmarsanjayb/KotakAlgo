from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from brokers.models import (
    Order, Position, Holding, Funds, Trade, BrokerResponse,
    OrderSide, OrderType, OrderStatus
)

class BaseBroker(ABC):
    """
    Abstract Base Class for all broker integrations.
    Every broker (Paper, Kotak, Zerodha, Angel, Fyers, Upstox, Dhan,
    Shoonya, AliceBlue, IBKR) MUST implement this interface.
    No module outside brokers/ may call a broker SDK directly.
    """
    def __init__(self, name: str) -> None:
        self.name = name
        self._connected: bool = False
        self._latency_ms: float = 0.0

    # ── Connection lifecycle ─────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> BrokerResponse:
        """Establish network connection / open API session."""
        ...

    @abstractmethod
    async def disconnect(self) -> BrokerResponse:
        """Gracefully close API session and release resources."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if broker session is live."""
        ...

    # ── Authentication ───────────────────────────────────────────────────────

    @abstractmethod
    async def login(self, **credentials: Any) -> BrokerResponse:
        """Authenticate with the broker (OTP, TOTP, password, token)."""
        ...

    @abstractmethod
    async def logout(self) -> BrokerResponse:
        """Invalidate the current session token."""
        ...

    # ── Account information ──────────────────────────────────────────────────

    @abstractmethod
    async def get_profile(self) -> BrokerResponse:
        """Return account profile (name, PAN, segments, etc.)."""
        ...

    @abstractmethod
    async def get_balance(self) -> BrokerResponse:
        """Return Funds model: equity, available_margin, used_margin."""
        ...

    @abstractmethod
    async def get_funds(self) -> BrokerResponse:
        """Return available and total account funds details."""
        ...

    @abstractmethod
    async def get_margin(self) -> BrokerResponse:
        """Return utilized, available, and required margin details."""
        ...

    @abstractmethod
    async def get_positions(self) -> BrokerResponse:
        """Return list of open intraday / carry-forward positions."""
        ...

    @abstractmethod
    async def get_holdings(self) -> BrokerResponse:
        """Return list of demat holdings."""
        ...

    @abstractmethod
    async def get_orders(self) -> BrokerResponse:
        """Return all orders for the current trading session."""
        ...

    # ── Order management ─────────────────────────────────────────────────────

    @abstractmethod
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
        """Submit a new order. Returns BrokerResponse with Order in data."""
        ...

    @abstractmethod
    async def modify_order(
        self,
        order_id:   str,
        quantity:   Optional[float] = None,
        price:      Optional[float] = None,
        order_type: Optional[OrderType] = None,
    ) -> BrokerResponse:
        """Modify price / quantity / type of a pending order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> BrokerResponse:
        """Cancel an open/pending order."""
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> BrokerResponse:
        """Return the current OrderStatus for a given order_id."""
        ...

    # ── Market data subscriptions ────────────────────────────────────────────

    @abstractmethod
    async def subscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        """Subscribe to live tick stream for the given symbols."""
        ...

    @abstractmethod
    async def unsubscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        """Unsubscribe from live tick stream for the given symbols."""
        ...

    @abstractmethod
    async def subscribe_option_chain(self, underlying: str, expiry: str) -> BrokerResponse:
        """Subscribe to full option chain updates for an underlying/expiry."""
        ...

    # ── Historical data ──────────────────────────────────────────────────────

    @abstractmethod
    async def get_historical_data(
        self,
        symbol:    str,
        interval:  str,
        from_date: str,
        to_date:   str,
    ) -> BrokerResponse:
        """
        Fetch OHLCV candles. interval: '1m','5m','15m','30m','1H','1D'.
        Returns BrokerResponse with list[dict] OHLCV rows in data.
        """
        ...

    # ── Health check ─────────────────────────────────────────────────────────

    @abstractmethod
    async def health_check(self) -> BrokerResponse:
        """Ping the broker API. Returns latency_ms and connection status."""
        ...
