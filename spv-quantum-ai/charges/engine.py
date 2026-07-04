import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from core.bus import event_bus, EventModel
from core.logging import get_logger
from charges.models import (
    BrokerProfile, BrokerageRules, TaxRules, ExchangeChargeRules, ChargesConfig,
    TradeChargesBreakdown
)
from charges.calculators import BrokerageCalculator, TaxCalculator, ExchangeChargeCalculator
from charges.publisher import CostPublisher

logger = get_logger("charges_engine")

class ChargesEngine:
    """
    Enterprise Charges & Cost Engine.
    Single source of truth for calculating actual transaction costs, taxes, and P&L details.
    """
    def __init__(self) -> None:
        self.publisher = CostPublisher()
        self.profiles: Dict[str, BrokerProfile] = {}
        self.active_profile_name = "kotak_neo"
        self._lock = asyncio.Lock()
        self._cache: Dict[str, TradeChargesBreakdown] = {}
        
        # Load default profiles
        self._init_profiles()

    def _init_profiles(self) -> None:
        # 1. Kotak Neo Profile (Neo Trade Free Plan: Rs 0 Brokerage across all segments)
        kotak = BrokerProfile(
            name="kotak_neo",
            brokerage_rules=BrokerageRules(
                intraday_rate=0.0, intraday_max=0.0,
                delivery_rate=0.0, delivery_max=0.0,
                futures_rate=0.0, futures_max=0.0,
                options_rate=0.0, options_max=0.0
            )
        )
        
        # 2. Zerodha Profile (Rs 20 flat for Intraday/F&O, Rs 0 for Delivery)
        zerodha = BrokerProfile(
            name="zerodha",
            brokerage_rules=BrokerageRules(
                intraday_rate=0.0003, intraday_max=20.0,
                delivery_rate=0.0, delivery_max=0.0,
                futures_rate=0.0003, futures_max=20.0,
                options_rate=0.0, options_max=20.0
            )
        )

        # 3. Angel One Profile
        angel = BrokerProfile(
            name="angel_one",
            brokerage_rules=BrokerageRules(
                intraday_rate=0.0003, intraday_max=20.0,
                delivery_rate=0.0, delivery_max=0.0,
                futures_rate=0.0003, futures_max=20.0,
                options_rate=0.0, options_max=20.0
            )
        )

        self.profiles["kotak_neo"] = kotak
        self.profiles["zerodha"] = zerodha
        self.profiles["angel_one"] = angel

    async def get_active_profile(self) -> BrokerProfile:
        async with self._lock:
            return self.profiles.get(self.active_profile_name, self.profiles["kotak_neo"])

    async def set_active_profile(self, name: str) -> None:
        async with self._lock:
            if name in self.profiles:
                self.active_profile_name = name
                logger.info(f"Active charges broker profile switched to: {name}")

    async def calculate_charges(
        self, order_id: str, symbol: str, side: str, qty: float, price: float, segment: Optional[str] = None
    ) -> TradeChargesBreakdown:
        """
        Calculates all charges and taxes for an executed order fill.
        """
        cache_key = f"{order_id}_{side}"
        async with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        profile = await self.get_active_profile()
        
        # Infer segment if not provided
        if not segment:
            symbol_upper = symbol.upper()
            if symbol_upper.endswith("FUT") or "FUT" in symbol_upper:
                segment = "Futures"
            elif any(x in symbol_upper for x in ["CE", "PE", "OPT"]):
                segment = "Options"
            else:
                segment = "Equity Intraday"

        # 1. Brokerage
        brokerage = BrokerageCalculator.calculate(profile, segment, side, qty, price)

        # 2. Taxes
        stt = TaxCalculator.calculate_stt(profile, segment, side, qty, price)
        stamp_duty = TaxCalculator.calculate_stamp_duty(profile, segment, side, qty, price)
        sebi = TaxCalculator.calculate_sebi_charges(profile, qty, price)

        # 3. Exchange Charges
        exchange_txn = ExchangeChargeCalculator.calculate_exchange_txn(profile, segment, qty, price)
        dp_charges = ExchangeChargeCalculator.calculate_dp_charges(profile, segment, side)

        # 4. GST (18% on Brokerage + Exchange Transaction)
        gst = TaxCalculator.calculate_gst(profile, brokerage, exchange_txn)

        # Total
        total_charges = brokerage + stt + exchange_txn + gst + sebi + stamp_duty + dp_charges

        # Break-even Price
        if qty > 0:
            if side.upper() == "BUY":
                breakeven_price = price + (total_charges / qty)
            else:
                breakeven_price = price - (total_charges / qty)
        else:
            breakeven_price = price

        cost_pct = (total_charges / (qty * price) * 100.0) if (qty * price) > 0 else 0.0

        breakdown = TradeChargesBreakdown(
            brokerage=round(brokerage, 4),
            stt=round(stt, 4),
            exchange_txn=round(exchange_txn, 4),
            gst=round(gst, 4),
            sebi=round(sebi, 4),
            stamp_duty=round(stamp_duty, 4),
            dp_charges=round(dp_charges, 4),
            total_charges=round(total_charges, 4),
            breakeven_price=round(breakeven_price, 4),
            cost_pct=round(cost_pct, 4)
        )

        # Publish calculation event
        await self.publisher.publish_charges_calculated(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            breakdown=breakdown
        )

        async with self._lock:
            self._cache[cache_key] = breakdown
            if len(self._cache) > 1000:
                for k in list(self._cache.keys())[:200]:
                    self._cache.pop(k, None)

        return breakdown

# Singleton instance
charges_engine = ChargesEngine()


class TradeCostManager:
    """
    Tracks dynamic, session-level charge metrics and updates net P&L statistics.
    """
    def __init__(self) -> None:
        self.todays_charges = 0.0
        self.monthly_charges = 0.0
        self.brokerage_breakdown = 0.0
        self.tax_breakdown = 0.0
        self.net_profit = 0.0
        self.gross_profit = 0.0
        
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("charges_calculated", self._on_charges_calculated)
        logger.info("TradeCostManager started and subscribed to events.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("charges_calculated", self._on_charges_calculated)
        logger.info("TradeCostManager stopped.")

    async def _on_charges_calculated(self, event: EventModel) -> None:
        try:
            payload = event.payload
            data = payload.get("breakdown", payload)
            
            async with self._lock:
                total = float(data.get("total_charges", 0.0))
                brokerage = float(data.get("brokerage", 0.0))
                
                stt = float(data.get("stt", 0.0))
                gst = float(data.get("gst", 0.0))
                sebi = float(data.get("sebi", 0.0))
                stamp = float(data.get("stamp_duty", 0.0))
                dp = float(data.get("dp_charges", 0.0))
                txn = float(data.get("exchange_txn", 0.0))
                
                taxes = stt + gst + sebi + stamp + dp + txn
                
                self.todays_charges += total
                self.monthly_charges += total
                self.brokerage_breakdown += brokerage
                self.tax_breakdown += taxes
                
                # Re-calculate net profit if we know gross profit
                self.net_profit = self.gross_profit - self.todays_charges
                
            # Publish updated stats
            await charges_engine.publisher.publish_net_pnl_updated(
                net_pnl=self.net_profit,
                gross_pnl=self.gross_profit,
                total_charges=self.todays_charges
            )
        except Exception as e:
            logger.error("Error processing charges_calculated in TradeCostManager", error=str(e))

    async def update_gross_profit(self, gross: float) -> None:
        async with self._lock:
            self.gross_profit = gross
            self.net_profit = gross - self.todays_charges
            
        await charges_engine.publisher.publish_net_pnl_updated(
            net_pnl=self.net_profit,
            gross_pnl=self.gross_profit,
            total_charges=self.todays_charges
        )

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                "estimated_charges": 0.0,
                "todays_charges": round(self.todays_charges, 2),
                "monthly_charges": round(self.monthly_charges, 2),
                "brokerage_breakdown": round(self.brokerage_breakdown, 2),
                "tax_breakdown": round(self.tax_breakdown, 2),
                "net_profit": round(self.net_profit, 2),
                "gross_profit": round(self.gross_profit, 2)
            }

# Singleton instance
trade_cost_manager = TradeCostManager()
