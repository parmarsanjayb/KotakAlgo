import asyncio
from typing import Any, Dict, List, Optional
from brokers.base import BaseBroker
from brokers.manager import broker_manager
from brokers.models import BrokerResponse, BrokerState, Order, OrderSide, OrderType, OrderStatus
from brokers.resolver import BrokerResolver
from brokers.publisher import BrokerPublisher

class BrokerEngine:
    """
    Enterprise Broker Abstraction Layer.
    Execution Engine and other modules communicate ONLY with BrokerEngine.
    """
    def __init__(self) -> None:
        self.publisher = BrokerPublisher()
        self._states: Dict[str, BrokerState] = {}
        self._lock = asyncio.Lock()

    def get_broker_state(self, broker_name: str) -> BrokerState:
        return self._states.get(broker_name, BrokerState.DISCONNECTED)

    def set_broker_state(self, broker_name: str, state: BrokerState) -> None:
        self._states[broker_name] = state

    def is_connected(self) -> bool:
        name = BrokerResolver.resolve_active_name()
        return self.get_broker_state(name) == BrokerState.CONNECTED

    async def connect(self, broker_name: Optional[str] = None) -> BrokerResponse:
        name = broker_name or BrokerResolver.resolve_active_name()
        self.set_broker_state(name, BrokerState.CONNECTING)
        try:
            broker = await broker_manager.load(name)
            resp = await broker.connect()
            if resp.success:
                broker_manager._active_broker_name = name
                self.set_broker_state(name, BrokerState.CONNECTED)
                await self.publisher.publish_connected(name)
            else:
                self.set_broker_state(name, BrokerState.FAILED)
            return resp
        except Exception as e:
            self.set_broker_state(name, BrokerState.FAILED)
            return BrokerResponse(success=False, broker=name, error=str(e))

    async def disconnect(self, broker_name: Optional[str] = None) -> BrokerResponse:
        name = broker_name or BrokerResolver.resolve_active_name()
        try:
            broker = broker_manager.get_active()
            resp = await broker.disconnect()
            self.set_broker_state(name, BrokerState.DISCONNECTED)
            await self.publisher.publish_disconnected(name)
            return resp
        except Exception as e:
            return BrokerResponse(success=False, broker=name, error=str(e))

    async def health_check(self) -> BrokerResponse:
        name = BrokerResolver.resolve_active_name()
        try:
            broker = broker_manager.get_active()
            resp = await broker.health_check()
            # Publish health change event
            await self.publisher.publish_health_changed(
                broker=name,
                is_healthy=resp.success,
                latency_ms=resp.latency_ms,
                error=resp.error
            )
            return resp
        except Exception as e:
            await self.publisher.publish_health_changed(broker=name, is_healthy=False, latency_ms=-1.0, error=str(e))
            return BrokerResponse(success=False, broker=name, error=str(e))

    async def place_order(
        self,
        symbol:        str,
        side:          OrderSide,
        quantity:      float,
        order_type:    OrderType = OrderType.MARKET,
        price:         Optional[float] = None,
        trigger_price: Optional[float] = None,
        tag:           Optional[str] = None,
    ) -> BrokerResponse:
        broker = broker_manager.get_active()
        resp = await broker.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            trigger_price=trigger_price,
            tag=tag
        )
        if resp.success and resp.data:
            order_data = resp.data
            if isinstance(order_data, dict):
                order = Order(**order_data)
            else:
                order = order_data
            await self.publisher.publish_order_placed(broker.name, order)
        return resp

    async def modify_order(
        self,
        order_id:   str,
        quantity:   Optional[float] = None,
        price:      Optional[float] = None,
        order_type: Optional[OrderType] = None,
    ) -> BrokerResponse:
        broker = broker_manager.get_active()
        resp = await broker.modify_order(
            order_id=order_id,
            quantity=quantity,
            price=price,
            order_type=order_type
        )
        if resp.success and resp.data:
            order_data = resp.data
            if isinstance(order_data, dict):
                order = Order(**order_data)
            else:
                order = order_data
            await self.publisher.publish_order_modified(broker.name, order)
        return resp

    async def cancel_order(self, order_id: str) -> BrokerResponse:
        broker = broker_manager.get_active()
        resp = await broker.cancel_order(order_id)
        if resp.success and resp.data:
            order_data = resp.data
            if isinstance(order_data, dict):
                order = Order(**order_data)
            else:
                order = order_data
            await self.publisher.publish_order_cancelled(broker.name, order)
        return resp

    async def get_order_status(self, order_id: str) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_order_status(order_id)

    async def get_positions(self) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_positions()

    async def get_holdings(self) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_holdings()

    async def get_balance(self) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_balance()

    async def get_funds(self) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_funds()

    async def get_margin(self) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.get_margin()

    async def subscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.subscribe_market_data(symbols)

    async def unsubscribe_market_data(self, symbols: List[str]) -> BrokerResponse:
        broker = broker_manager.get_active()
        return await broker.unsubscribe_market_data(symbols)

# Singleton instance
broker_engine = BrokerEngine()
