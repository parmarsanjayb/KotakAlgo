import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timezone
from portfolio.models import Position, PositionState, PortfolioSummary
from core.logging import get_logger

logger = get_logger("portfolio_managers")

class PositionManager:
    """
    Manages all open and closed positions for all SaaS tenants.
    Maintains average prices, net quantities, and states.
    """
    def __init__(self) -> None:
        self._positions: Dict[str, Dict[str, Position]] = {}  # user_id -> { symbol -> Position }
        self._lock = asyncio.Lock()

    def _get_user_positions(self, user_id: str) -> Dict[str, Position]:
        # Handle backward-compatibility: if the dictionary was populated flatly by a test
        flat_positions = {}
        for k, v in list(self._positions.items()):
            if isinstance(v, Position):
                flat_positions[k] = v
                
        if flat_positions:
            # Move flat positions to the default "admin" user
            if "admin" not in self._positions:
                self._positions["admin"] = {}
            for k, v in flat_positions.items():
                self._positions["admin"][k] = v
                del self._positions[k]

        if user_id not in self._positions:
            self._positions[user_id] = {}
        return self._positions[user_id]

    async def update_on_fill(self, fill_symbol: str, fill_side: str, fill_qty: float, fill_price: float, user_id: str = "admin") -> tuple[Optional[Position], str]:
        """
        Updates average price and quantity based on trade fill details for a specific user.
        Returns: (updated_position, action_type) where action_type can be "OPENED", "UPDATED", "CLOSED", or "NONE"
        """
        from portfolio.trade_log import record_closed_trade, resolve_segment

        # Filled in whenever this fill books realised P&L; written to the
        # database after the lock is released so accounting never holds up
        # order handling.
        closed_record: Optional[dict] = None

        async with self._lock:
            user_pos = self._get_user_positions(user_id)
            pos = user_pos.get(fill_symbol)
            action = "NONE"

            # Shared classifier — a plain "CE"/"PE"/"FUT" match filed CRUDEOIL
            # and ZINC under Equity, which made the segment breakdown wrong.
            segment = resolve_segment(fill_symbol)

            if not pos or pos.state == PositionState.CLOSED:
                # Open new position
                pos = Position(
                    symbol=fill_symbol,
                    segment=segment,
                    side=fill_side.upper(),
                    quantity=fill_qty,
                    avg_price=fill_price,
                    ltp=fill_price,
                    state=PositionState.OPEN,
                    user_id=user_id
                )
                user_pos[fill_symbol] = pos
                action = "OPENED"
            else:
                # Update existing position
                # Determine net direction change
                curr_side = pos.side.upper()
                new_side = fill_side.upper()
                
                if curr_side == new_side:
                    # Adding to position
                    new_qty = pos.quantity + fill_qty
                    new_avg = ((pos.avg_price * pos.quantity) + (fill_price * fill_qty)) / new_qty
                    pos.avg_price = new_avg
                    pos.quantity = new_qty
                    pos.state = PositionState.OPEN
                    pos.updated_at = datetime.now(timezone.utc)
                    action = "UPDATED"
                else:
                    # Subtracting or reversing position
                    if fill_qty < pos.quantity:
                        # Partial close
                        # Realized PNL calculation: entry vs exit
                        trade_realized = (fill_price - pos.avg_price) * fill_qty if curr_side == "BUY" else (pos.avg_price - fill_price) * fill_qty
                        pos.realized_pnl += trade_realized
                        closed_record = dict(
                            symbol=fill_symbol, side=curr_side, quantity=fill_qty,
                            entry_price=pos.avg_price, exit_price=fill_price,
                            pnl=trade_realized, user_id=user_id, opened_at=pos.created_at,
                        )

                        pos.quantity -= fill_qty
                        pos.state = PositionState.PARTIAL
                        pos.updated_at = datetime.now(timezone.utc)
                        action = "UPDATED"
                    elif fill_qty == pos.quantity:
                        # Full close
                        trade_realized = (fill_price - pos.avg_price) * fill_qty if curr_side == "BUY" else (pos.avg_price - fill_price) * fill_qty
                        pos.realized_pnl += trade_realized
                        closed_record = dict(
                            symbol=fill_symbol, side=curr_side, quantity=fill_qty,
                            entry_price=pos.avg_price, exit_price=fill_price,
                            pnl=trade_realized, user_id=user_id, opened_at=pos.created_at,
                        )

                        pos.quantity = 0.0
                        pos.state = PositionState.CLOSED
                        pos.updated_at = datetime.now(timezone.utc)
                        action = "CLOSED"
                    else:
                        # Reversal (position flipped buy -> sell or vice-versa)
                        trade_realized = (fill_price - pos.avg_price) * pos.quantity if curr_side == "BUY" else (pos.avg_price - fill_price) * pos.quantity
                        pos.realized_pnl += trade_realized
                        closed_record = dict(
                            symbol=fill_symbol, side=curr_side, quantity=pos.quantity,
                            entry_price=pos.avg_price, exit_price=fill_price,
                            pnl=trade_realized, user_id=user_id, opened_at=pos.created_at,
                        )

                        remaining_qty = fill_qty - pos.quantity
                        pos.side = new_side
                        pos.quantity = remaining_qty
                        pos.avg_price = fill_price
                        pos.state = PositionState.OPEN
                        pos.updated_at = datetime.now(timezone.utc)
                        action = "OPENED"

        if closed_record is not None:
            await record_closed_trade(**closed_record)

        return pos, action

    async def update_ltp(self, symbol: str, ltp: float) -> List[Position]:
        """Updates LTP for all users currently holding the given symbol."""
        async with self._lock:
            updated_positions = []
            for user_id, user_pos in self._positions.items():
                pos = user_pos.get(symbol)
                if pos and pos.state != PositionState.CLOSED:
                    pos.ltp = ltp
                    # Recalculate unrealized PNL
                    if pos.side.upper() == "BUY":
                        pos.unrealized_pnl = (ltp - pos.avg_price) * pos.quantity
                    else:
                        pos.unrealized_pnl = (pos.avg_price - ltp) * pos.quantity
                    pos.updated_at = datetime.now(timezone.utc)
                    updated_positions.append(pos)
            return updated_positions

    async def get_all_positions(self, user_id: str = "admin") -> List[Position]:
        async with self._lock:
            user_pos = self._get_user_positions(user_id)
            return list(user_pos.values())

    async def get_open_positions(self, user_id: str = "admin") -> List[Position]:
        async with self._lock:
            user_pos = self._get_user_positions(user_id)
            return [p for p in user_pos.values() if p.state in (PositionState.OPEN, PositionState.PARTIAL)]

    async def get_closed_positions(self, user_id: str = "admin") -> List[Position]:
        async with self._lock:
            user_pos = self._get_user_positions(user_id)
            return [p for p in user_pos.values() if p.state == PositionState.CLOSED]


class PnLManager:
    """
    Computes portfolio-level realized, unrealized, and MTM (Mark-to-Market).
    """
    def calculate_pnl(self, positions: List[Position]) -> tuple[float, float, float]:
        realized = sum(p.realized_pnl for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions)
        mtm = realized + unrealized
        return realized, unrealized, mtm


class ExposureCalculator:
    """
    Computes absolute USD exposure, segment allocations, and sector distributions.
    """
    def calculate_exposure(self, positions: List[Position]) -> tuple[float, Dict[str, float], Dict[str, float]]:
        total_exposure = 0.0
        segment_exposures: Dict[str, float] = {}
        sector_exposures: Dict[str, float] = {}

        for p in positions:
            if p.state == PositionState.CLOSED:
                continue
            
            # Exposure = Quantity * LTP (or avg_price if LTP not yet set)
            price = p.ltp if p.ltp > 0 else p.avg_price
            exposure = p.quantity * price
            total_exposure += exposure

            segment_exposures[p.segment] = segment_exposures.get(p.segment, 0.0) + exposure
            
            # Simulating sector distribution (e.g. NIFTY50 -> Index, RELIANCE -> Energy, rest -> General)
            sector = "General"
            if p.symbol == "NIFTY50":
                sector = "Index"
            elif p.symbol == "RELIANCE":
                sector = "Energy"
            elif "BTC" in p.symbol or "ETH" in p.symbol:
                sector = "Crypto"
            sector_exposures[sector] = sector_exposures.get(sector, 0.0) + exposure

        # Normalize distributions to percentage
        segment_dist = {}
        sector_dist = {}
        if total_exposure > 0:
            segment_dist = {k: round(v / total_exposure * 100.0, 2) for k, v in segment_exposures.items()}
            sector_dist = {k: round(v / total_exposure * 100.0, 2) for k, v in sector_exposures.items()}

        return total_exposure, segment_dist, sector_dist
