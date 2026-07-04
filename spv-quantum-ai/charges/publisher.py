from core.bus import event_bus, EventModel
from core.logging import get_logger
from charges.models import (
    ChargesCalculatedEvent, TradeCostUpdatedEvent, NetPnLUpdatedEvent, TradeChargesBreakdown
)

logger = get_logger("charges_publisher")

class CostPublisher:
    """
    Publishes cost, brokerage, and P&L reconciliation events to the Event Bus.
    """
    async def publish_charges_calculated(
        self, order_id: str, symbol: str, side: str, qty: float, price: float, breakdown: TradeChargesBreakdown
    ) -> None:
        evt = ChargesCalculatedEvent(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            breakdown=breakdown
        )
        await event_bus.publish(EventModel(
            event_type="charges_calculated",
            source_agent="charges_engine",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published charges_calculated event", order_id=order_id, total_charges=breakdown.total_charges)

    async def publish_trade_cost_updated(
        self, trade_id: str, symbol: str, breakdown: TradeChargesBreakdown
    ) -> None:
        evt = TradeCostUpdatedEvent(
            trade_id=trade_id,
            symbol=symbol,
            breakdown=breakdown
        )
        await event_bus.publish(EventModel(
            event_type="trade_cost_updated",
            source_agent="charges_engine",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published trade_cost_updated event", trade_id=trade_id)

    async def publish_net_pnl_updated(
        self, net_pnl: float, gross_pnl: float, total_charges: float
    ) -> None:
        evt = NetPnLUpdatedEvent(
            net_pnl=net_pnl,
            gross_pnl=gross_pnl,
            total_charges=total_charges
        )
        await event_bus.publish(EventModel(
            event_type="net_pnl_updated",
            source_agent="charges_engine",
            payload=evt.model_dump(mode="json")
        ))
        logger.debug("Published net_pnl_updated event", net_pnl=net_pnl)
