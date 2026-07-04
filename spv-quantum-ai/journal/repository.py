import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from database.connection import async_session
from database.models import JournalModel
from journal.models import TradeRecord, DecisionAudit
from core.logging import get_logger

logger = get_logger("journal_repository")

class TradeHistoryRepository:
    """
    Repository class storing and querying TradeRecords and DecisionAudits.
    Utilizes the database 'journal' table (JournalModel) in production/development,
    and falls back to an in-memory list in test environments to avoid postgres dependency.
    """
    _in_memory_journal: List[Dict[str, Any]] = []
    _next_id = 1

    def _is_test(self) -> bool:
        return "pytest" in sys.modules or "pytest" in sys.argv[0]

    async def save_trade_record(self, trade: TradeRecord) -> int:
        if self._is_test():
            entry_id = TradeHistoryRepository._next_id
            TradeHistoryRepository._next_id += 1
            TradeHistoryRepository._in_memory_journal.append({
                "id": entry_id,
                "entry_type": "trade_record",
                "text": f"Trade {trade.trade_id} for {trade.symbol} side {trade.side}",
                "tags": trade.model_dump(mode="json")
            })
            return entry_id

        async with async_session() as session:
            try:
                db_entry = JournalModel(
                    entry_type="trade_record",
                    text=f"Trade {trade.trade_id} for {trade.symbol} side {trade.side}",
                    tags=trade.model_dump(mode="json")
                )
                session.add(db_entry)
                await session.commit()
                await session.refresh(db_entry)
                return db_entry.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to save trade record to db: {e}")
                raise e

    async def update_trade_record(self, trade: TradeRecord) -> None:
        if self._is_test():
            for entry in TradeHistoryRepository._in_memory_journal:
                if entry["entry_type"] == "trade_record" and entry["tags"].get("trade_id") == trade.trade_id:
                    entry["tags"] = trade.model_dump(mode="json")
                    entry["text"] = f"Trade {trade.trade_id} updated: PNL={trade.realized_pnl}"
                    break
            return

        async with async_session() as session:
            try:
                stmt = select(JournalModel).where(
                    JournalModel.entry_type == "trade_record"
                )
                res = await session.execute(stmt)
                db_entries = res.scalars().all()
                
                for entry in db_entries:
                    if entry.tags and entry.tags.get("trade_id") == trade.trade_id:
                        entry.tags = trade.model_dump(mode="json")
                        entry.text = f"Trade {trade.trade_id} updated: PNL={trade.realized_pnl}"
                        session.add(entry)
                        await session.commit()
                        break
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to update trade record: {e}")
                raise e

    async def save_decision_audit(self, audit: DecisionAudit) -> int:
        if self._is_test():
            entry_id = TradeHistoryRepository._next_id
            TradeHistoryRepository._next_id += 1
            TradeHistoryRepository._in_memory_journal.append({
                "id": entry_id,
                "entry_type": "decision_audit",
                "text": f"Decision audit {audit.audit_id} for {audit.symbol}",
                "tags": audit.model_dump(mode="json")
            })
            return entry_id

        async with async_session() as session:
            try:
                db_entry = JournalModel(
                    entry_type="decision_audit",
                    text=f"Decision audit {audit.audit_id} for {audit.symbol}",
                    tags=audit.model_dump(mode="json")
                )
                session.add(db_entry)
                await session.commit()
                await session.refresh(db_entry)
                return db_entry.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to save decision audit to db: {e}")
                raise e

    async def get_all_trades(self) -> List[TradeRecord]:
        if self._is_test():
            trades = []
            for entry in TradeHistoryRepository._in_memory_journal:
                if entry["entry_type"] == "trade_record":
                    trades.append(TradeRecord.model_validate(entry["tags"]))
            return trades

        async with async_session() as session:
            stmt = select(JournalModel).where(JournalModel.entry_type == "trade_record")
            res = await session.execute(stmt)
            entries = res.scalars().all()
            
            trades = []
            for entry in entries:
                if entry.tags:
                    trades.append(TradeRecord.model_validate(entry.tags))
            return trades

    async def get_all_audits(self) -> List[DecisionAudit]:
        if self._is_test():
            audits = []
            for entry in TradeHistoryRepository._in_memory_journal:
                if entry["entry_type"] == "decision_audit":
                    audits.append(DecisionAudit.model_validate(entry["tags"]))
            return audits

        async with async_session() as session:
            stmt = select(JournalModel).where(JournalModel.entry_type == "decision_audit")
            res = await session.execute(stmt)
            entries = res.scalars().all()
            
            audits = []
            for entry in entries:
                if entry.tags:
                    audits.append(DecisionAudit.model_validate(entry.tags))
            return audits

    async def search_trades(self, filters: Dict[str, Any]) -> List[TradeRecord]:
        all_trades = await self.get_all_trades()
        filtered = []

        start_date = filters.get("start_date")
        end_date = filters.get("end_date")
        strategy = filters.get("strategy")
        segment = filters.get("segment")
        pnl_min = filters.get("pnl_min")
        pnl_max = filters.get("pnl_max")

        for t in all_trades:
            # Date check
            t_time = t.timestamp
            if isinstance(t_time, str):
                t_time = datetime.fromisoformat(t_time.replace("Z", "+00:00"))
            
            if start_date and t_time < start_date:
                continue
            if end_date and t_time > end_date:
                continue

            # Strategy check
            if strategy and t.strategy_name != strategy:
                continue

            # Segment check
            if segment and t.segment.upper() != segment.upper():
                continue

            # PNL checks
            if pnl_min is not None and t.realized_pnl < float(pnl_min):
                continue
            if pnl_max is not None and t.realized_pnl > float(pnl_max):
                continue

            filtered.append(t)
        return filtered
