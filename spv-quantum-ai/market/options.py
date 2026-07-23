from datetime import datetime, timezone
from typing import List, Optional, Any
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

    def __init__(self, cache: DataCacheManager, registry: Optional[Any] = None) -> None:
        self._cache = cache
        self._registry = registry

    async def build_chain(self, underlying: str, spot: float, expiry: str) -> OptionChain:
        """Builds the option chain for an underlying.

        Prefers the REAL contracts we subscribe to on the Kotak feed (live ltp,
        volume and open interest). The synthetic matrix below is only a fallback
        for when no real chain is available — its numbers are placeholders and
        must never be presented to the user as market data.
        """
        real = await self._build_real_chain(underlying, expiry)
        if real is not None:
            return real

        meta    = self._get_strike_meta(underlying)
        step    = meta["step"]
        atm     = round(spot / step) * step
        contracts: List[OptionContract] = []

        # Format expiry date (e.g. 2026-07-31 -> 26JUL)
        try:
            dt = datetime.strptime(expiry, "%Y-%m-%d")
            expiry_formatted = dt.strftime("%y%b").upper()
        except Exception:
            expiry_formatted = "26JUL"

        for i in range(-5, 6):
            strike = atm + i * step
            for ot in ("CE", "PE"):
                strike_int = int(strike)
                underlying_clean = underlying.replace("50", "")
                opt_symbol = f"{underlying_clean}{expiry_formatted}{strike_int}{ot}"

                # Greeks interface — values are placeholders until strategy layer populates
                greeks = OptionGreeks()
                ltp    = max(1.0, self._synthetic_ltp(spot, strike, ot))
                
                # Register the symbol dynamically so it becomes tradable and searchable
                if self._registry:
                    lot_size = self._registry.get_meta(underlying).get("lot_size", 1) if self._registry.is_registered(underlying) else 1
                    self._registry.register(opt_symbol, {
                        "exchange": "NSE" if underlying != "SENSEX" else "BSE",
                        "segment": "OPTIONS",
                        "lot_size": lot_size,
                        "tick_size": 0.05
                    })

                contracts.append(OptionContract(
                    strike       = strike,
                    option_type  = ot,
                    ltp          = round(ltp, 2),
                    bid          = round(ltp * 0.995, 2),
                    ask          = round(ltp * 1.005, 2),
                    volume       = 1200.0,
                    open_interest= 5000.0,
                    greeks       = greeks,
                    symbol       = opt_symbol,
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

    async def _build_real_chain(self, underlying: str, expiry: str) -> Optional[OptionChain]:
        """Build the chain from the real option contracts we subscribe to.

        The index itself has no price feed, so the underlying price is derived by
        put-call parity (S ≈ K + CE − PE, median over strikes quoting both legs).
        That keeps the ATM strike honest instead of drifting to a stale default.
        Returns None when no real quotes are available, so the caller can fall
        back to the synthetic matrix.
        """
        import json, pathlib
        try:
            path = pathlib.Path(__file__).resolve().parent.parent.parent / "option_tokens.json"
            if not path.exists():
                return None
            with open(path) as fh:
                tokens = json.load(fh)
        except Exception:
            return None

        name = "NIFTY" if underlying in ("NIFTY50", "NIFTY") else underlying
        rows = [o for o in tokens if o.get("name") == name]
        if not rows:
            return None

        contracts: List[OptionContract] = []
        legs: dict = {}
        for o in rows:
            sym = o.get("trd")
            try:
                tick = await self._cache.get_tick(sym)
            except Exception:
                tick = None
            if not tick:
                continue
            ltp = float(getattr(tick, "ltp", 0) or 0)
            if ltp <= 0:
                continue
            strike = float(o.get("strike") or 0)
            ot = o.get("type")
            legs.setdefault(strike, {})[ot] = ltp
            contracts.append(OptionContract(
                strike        = strike,
                option_type   = ot,
                ltp           = round(ltp, 2),
                bid           = float(getattr(tick, "bid", 0) or 0),
                ask           = float(getattr(tick, "ask", 0) or 0),
                volume        = float(getattr(tick, "volume", 0) or 0),
                open_interest = float(getattr(tick, "open_interest", 0) or 0),
                greeks        = OptionGreeks(),
                symbol        = sym,
            ))

        if len(contracts) < 4:
            return None

        parity = sorted(k + v["CE"] - v["PE"] for k, v in legs.items()
                        if "CE" in v and "PE" in v)
        if not parity:
            return None
        spot = parity[len(parity) // 2]

        chain = OptionChain(
            underlying       = underlying,
            underlying_price = spot,
            expiry           = expiry,
            contracts        = contracts,
            timestamp        = datetime.now(timezone.utc),
        )
        await self._cache.update_option_chain(chain)
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
