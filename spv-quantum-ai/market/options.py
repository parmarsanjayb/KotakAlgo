from datetime import datetime, timezone
from typing import List
from market.models import (
    OptionChain, OptionContract, OptionGreeks, OptionChainUpdatedEvent
)
from market.cache import DataCacheManager
from core.bus import event_bus, EventModel

class OptionChainManager:
    """
    Manages option contract matrices for index underlyings.
    Provides interfaces for Greeks (computed externally by strategy layer).
    """

    def __init__(self, cache: DataCacheManager) -> None:
        self._cache = cache

    async def build_chain(self, underlying: str, spot: float, expiry: str) -> OptionChain:
        """Constructs a synthetic option chain matrix around ATM strike."""
        meta    = self._get_strike_meta(underlying)
        step    = meta["step"]
        atm     = round(spot / step) * step
        contracts: List[OptionContract] = []

        for i in range(-5, 6):
            strike = atm + i * step
            for ot in ("CE", "PE"):
                # Greeks interface — values are placeholders until strategy layer populates
                greeks = OptionGreeks()
                ltp    = max(1.0, self._synthetic_ltp(spot, strike, ot))
                contracts.append(OptionContract(
                    strike       = strike,
                    option_type  = ot,
                    ltp          = round(ltp, 2),
                    bid          = round(ltp * 0.995, 2),
                    ask          = round(ltp * 1.005, 2),
                    volume       = 1200.0,
                    open_interest= 5000.0,
                    greeks       = greeks,
                ))

        chain = OptionChain(
            underlying       = underlying,
            underlying_price = spot,
            expiry           = expiry,
            contracts        = contracts,
            timestamp        = datetime.now(timezone.utc),
        )

        await self._cache.update_option_chain(chain)

        # Publish OptionChainUpdatedEvent
        evt = OptionChainUpdatedEvent(option_chain=chain)
        await event_bus.publish(EventModel(
            event_type   = "option_chain_updated",
            source_agent = "option_chain_manager",
            payload      = evt.model_dump(),
        ))

        return chain

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_strike_meta(underlying: str) -> dict:
        defaults = {"step": 50.0}
        mapping  = {"BANKNIFTY": {"step": 100.0}, "SENSEX": {"step": 100.0}}
        return mapping.get(underlying, defaults)

    @staticmethod
    def _synthetic_ltp(spot: float, strike: float, ot: str) -> float:
        intrinsic = max(0.0, spot - strike) if ot == "CE" else max(0.0, strike - spot)
        dist      = abs(spot - strike)
        time_val  = max(2.0, 90.0 - dist * 0.5)
        return round(intrinsic + time_val, 2)
