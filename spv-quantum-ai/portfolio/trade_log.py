"""Permanent record of completed trades, and the P&L report built from it.

The live position book lives in memory, so a restart erases it — on 2026-07-22
a mid-session restart destroyed the P&L of nine trades that had already been
executed. Everything here writes to the database instead, so the day / week /
month / all-time report survives restarts, redeploys and crashes.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger("trade_log")

IST = timezone(timedelta(hours=5, minutes=30))

# MCX products the platform trades. Needed because a plain symbol match would
# file CRUDEOIL and ZINC under "Equity", which is what made the old segment
# breakdown useless.
_COMMODITY_ROOTS = (
    "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER", "ZINC",
    "ALUMINIUM", "LEAD", "NICKEL", "MENTHAOIL", "COTTON",
)
_INDEX_ROOTS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")


def resolve_segment(symbol: str) -> str:
    """Classify a symbol the way an Indian trader thinks about their book."""
    if not symbol:
        return "Equity"
    s = symbol.upper()
    is_option = s.endswith("CE") or s.endswith("PE")
    is_commodity = any(s.startswith(root) for root in _COMMODITY_ROOTS)

    if is_option and is_commodity:
        return "Commodity Options"
    if is_option:
        return "Index Options" if any(s.startswith(r) for r in _INDEX_ROOTS) else "Stock Options"
    if is_commodity:
        return "Commodity"
    if s.endswith("FUT") or "FUT" in s:
        return "Futures"
    return "Equity"


async def record_closed_trade(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    pnl: float,
    user_id: str = "admin",
    strategy: Optional[str] = None,
    opened_at: Optional[datetime] = None,
) -> None:
    """Persist one completed round trip. Never raises — accounting must not
    be able to break order handling."""
    try:
        from database.connection import async_session
        from database.models import ClosedTradeModel

        row = ClosedTradeModel(
            id=uuid.uuid4().hex,
            user_id=user_id,
            symbol=symbol,
            segment=resolve_segment(symbol),
            side=str(side).upper(),
            quantity=float(quantity),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            pnl=float(pnl),
            strategy=strategy,
            opened_at=opened_at,
            closed_at=datetime.now(timezone.utc),
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
        logger.info(
            "Closed trade recorded",
            symbol=symbol,
            segment=row.segment,
            quantity=quantity,
            pnl=round(float(pnl), 2),
        )
    except Exception as e:
        logger.error("Failed to record closed trade", symbol=symbol, error=str(e))


# ── Reporting ────────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


def _period_starts() -> Dict[str, Optional[datetime]]:
    """UTC cut-offs for today / this week / this month, measured in IST.

    Indian sessions (09:15 equity, up to 23:30 MCX) all fall inside one IST
    calendar day, so an IST midnight boundary matches what the user calls
    "today".
    """
    now = _ist_now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())        # Monday
    month = day.replace(day=1)
    return {
        "today": day.astimezone(timezone.utc).replace(tzinfo=None),
        "week": week.astimezone(timezone.utc).replace(tzinfo=None),
        "month": month.astimezone(timezone.utc).replace(tzinfo=None),
        "total": None,
    }


def _summarise(trades: List[Any]) -> Dict[str, Any]:
    """Aggregate a list of closed trades, overall and per segment."""
    def block(rows: List[Any]) -> Dict[str, Any]:
        wins = [r for r in rows if (r.pnl or 0) > 0]
        losses = [r for r in rows if (r.pnl or 0) < 0]
        gross_profit = sum(r.pnl for r in wins)
        gross_loss = abs(sum(r.pnl for r in losses))
        return {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "profit": round(gross_profit, 2),
            "loss": round(gross_loss, 2),
            "net_pnl": round(gross_profit - gross_loss, 2),
            "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "best": round(max((r.pnl for r in rows), default=0.0), 2),
            "worst": round(min((r.pnl for r in rows), default=0.0), 2),
        }

    segments: Dict[str, Any] = {}
    for row in trades:
        segments.setdefault(row.segment or "Equity", []).append(row)

    out = block(trades)
    out["segments"] = {name: block(rows) for name, rows in sorted(segments.items())}
    return out


def _fmt_ist(dt: Optional[datetime]) -> Optional[str]:
    """Stored timestamps are naive UTC; the user reads IST."""
    if not dt:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%d %b %H:%M")


def _trade_dict(t: Any) -> Dict[str, Any]:
    return {
        "symbol": t.symbol,
        "segment": t.segment,
        "side": t.side,
        "quantity": t.quantity,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "entry_time": _fmt_ist(t.opened_at),
        "exit_time": _fmt_ist(t.closed_at),
        "pnl": round(t.pnl, 2),
        "strategy": t.strategy,
    }


async def fetch_trades(
    period: str = "today",
    segment: Optional[str] = None,
    user_id: str = "admin",
    limit: int = 500,
) -> Dict[str, Any]:
    """Every completed trade in a period, optionally for one segment only.

    Backs the drill-down: click a segment in the report and see each trade with
    its entry time/price, exit time/price and P&L.
    """
    from sqlalchemy import select
    from database.connection import async_session
    from database.models import ClosedTradeModel

    start = _period_starts().get(period, None)
    try:
        query = select(ClosedTradeModel).where(ClosedTradeModel.user_id == user_id)
        if start is not None:
            query = query.where(ClosedTradeModel.closed_at >= start)
        if segment:
            query = query.where(ClosedTradeModel.segment == segment)
        query = query.order_by(ClosedTradeModel.closed_at.desc()).limit(limit)

        async with async_session() as session:
            rows = list((await session.execute(query)).scalars().all())

        return {
            "period": period,
            "segment": segment,
            "count": len(rows),
            "net_pnl": round(sum(r.pnl for r in rows), 2),
            "trades": [_trade_dict(r) for r in rows],
        }
    except Exception as e:
        logger.error("Failed to fetch trades", period=period, segment=segment, error=str(e))
        return {"period": period, "segment": segment, "count": 0, "net_pnl": 0.0,
                "trades": [], "error": str(e)}


async def build_pnl_report(user_id: str = "admin", recent_limit: int = 25) -> Dict[str, Any]:
    """Day / week / month / all-time P&L, each broken down by segment."""
    from sqlalchemy import select
    from database.connection import async_session
    from database.models import ClosedTradeModel

    starts = _period_starts()
    report: Dict[str, Any] = {"generated_at": _ist_now().isoformat(), "periods": {}}

    try:
        async with async_session() as session:
            result = await session.execute(
                select(ClosedTradeModel)
                .where(ClosedTradeModel.user_id == user_id)
                .order_by(ClosedTradeModel.closed_at.desc())
            )
            all_trades = list(result.scalars().all())

        for name, start in starts.items():
            rows = all_trades if start is None else [
                t for t in all_trades if t.closed_at and t.closed_at >= start
            ]
            report["periods"][name] = _summarise(rows)

        report["recent"] = [_trade_dict(t) for t in all_trades[:recent_limit]]
    except Exception as e:
        logger.error("Failed to build P&L report", error=str(e))
        report["error"] = str(e)
        for name in starts:
            report["periods"].setdefault(name, _summarise([]))
        report.setdefault("recent", [])

    return report
