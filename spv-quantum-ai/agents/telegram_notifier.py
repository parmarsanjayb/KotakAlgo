import time
from typing import Any, Dict, List, Optional
from core.agent import BaseAgent, AgentResultModel
from core.bus import EventModel
from core.config import settings
from telegram.bot import TelegramBotClient

class TelegramNotifier(BaseAgent):
    """
    Subscribes to notifications and pushes alerts/trade reports to Telegram Bot API.
    """
    def __init__(self) -> None:
        super().__init__(
            name="telegram_notifier",
            description="Streams system alerts, risk updates, and trade executions to Telegram channels"
        )
        self.bot = TelegramBotClient()
        
        telegram_cfg = settings.yaml_config.get("telegram", {})
        self.send_alerts = telegram_cfg.get("send_alerts", True)
        self.send_trades = telegram_cfg.get("send_trades", True)
        self.send_rejections = telegram_cfg.get("send_rejections", False)
        self.send_news = telegram_cfg.get("send_news", True)

    @property
    def input_event_types(self) -> List[str]:
        types = ["risk_alert", "order_filled", "execution_failed"]
        # Rejections are routine internal filtering — the CEO rejects hundreds of
        # signals a day by design, so pushing them to Telegram spams the user.
        # They already appear in the dashboard Decision Log; opt-in for debugging.
        if self.send_rejections:
            types += ["trade_rejected", "trade_blocked"]
        if self.send_news:
            types += ["news_update"]
        return types

    @property
    def output_event_types(self) -> List[str]:
        return []

    async def initialize(self) -> None:
        await self.bot.start()
        self.log_info("TelegramNotifier initialized.")

    async def shutdown(self) -> None:
        await self.bot.close()
        self.log_info("TelegramNotifier shutdown complete.")

    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        """Processes logs and submits them to Telegram helper client."""
        start_time = time.perf_counter()
        message = ""
        payload = event.payload
        
        # 1. Extract user_id from various event structures to handle multi-tenancy
        user_id = None
        if isinstance(payload, dict):
            if "user_id" in payload:
                user_id = payload["user_id"]
            elif "order" in payload and isinstance(payload["order"], dict):
                user_id = payload["order"].get("user_id")
            elif "order_details" in payload and isinstance(payload["order_details"], dict):
                user_id = payload["order_details"].get("user_id")
            elif "trade" in payload and isinstance(payload["trade"], dict):
                user_id = payload["trade"].get("user_id")
        
        # 2. Resolve user's registered telegram_chat_id from database
        telegram_chat_id = None
        if user_id:
            from database.connection import async_session
            from database.models import UserModel
            from sqlalchemy import select
            try:
                async with async_session() as session:
                    res = await session.execute(
                        select(UserModel.telegram_chat_id).where(UserModel.id == user_id)
                    )
                    telegram_chat_id = res.scalar_one_or_none()
            except Exception as db_err:
                self.log_error(f"Error fetching telegram_chat_id for user {user_id}: {db_err}")

        # Fallback to settings.TELEGRAM_CHAT_ID or bot default chat_id if not registered in database
        if not telegram_chat_id:
            from core.config import settings
            telegram_chat_id = settings.TELEGRAM_CHAT_ID or self.bot.chat_id

        # Strict security: if no destination chat_id, do not broadcast to prevent privacy leaks
        if not telegram_chat_id:
            self.log_info(f"Skipping Telegram notification: no telegram_chat_id registered for user_id '{user_id}'")
            return None
        
        if event.event_type == "risk_alert" and self.send_alerts:
            alert = event.payload
            # The scanner re-signals already-held symbols every few seconds, so the
            # Risk Engine BLOCKs each one and emits a risk_alert. Those are routine
            # protection working (duplicate position, cooldown, order dedup), not
            # something to ping the user about — they already show in the dashboard.
            # Only forward genuine risk events (capital / drawdown / exposure / loss).
            _msg = str(alert.get("message", "")).lower()
            if any(k in _msg for k in ("duplicate", "cooldown", "already exists",
                                       "identical order", "must wait")):
                return None
            message = (
                f"⚠️ <b>[SPV RISK ALERT]</b>\n"
                f"<b>Type:</b> {alert.get('alert_type', 'GENERAL')}\n"
                f"<b>Message:</b> {alert.get('message', 'No details available.')}\n"
            )
            
        elif event.event_type == "order_filled" and self.send_trades:
            trade = event.payload
            # Extract flat fields or nested order fields
            symbol = trade.get('symbol') or trade.get('order', {}).get('symbol')
            side = trade.get('side') or trade.get('order', {}).get('side')
            qty = trade.get('quantity') or trade.get('order', {}).get('quantity')
            price = trade.get('price') or trade.get('order', {}).get('price')
            status = trade.get('status') or trade.get('order', {}).get('status')
            broker = trade.get('broker') or trade.get('order', {}).get('broker')
            order_id = trade.get('order_id') or trade.get('order', {}).get('order_id')
            
            message = (
                f"🟢 <b>[TRADE EXECUTED]</b>\n"
                f"<b>Symbol:</b> {symbol}\n"
                f"<b>Side:</b> {side}\n"
                f"<b>Qty:</b> {qty}\n"
                f"<b>Price:</b> {price}\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Broker:</b> {broker}\n"
                f"<b>ID:</b> <code>{order_id}</code>"
            )
            
        elif event.event_type == "execution_failed" and self.send_alerts:
            err = event.payload
            message = (
                f"🔴 <b>[EXECUTION FAILED]</b>\n"
                f"<b>Reason:</b> {err.get('reason', 'Unknown error')}\n"
                f"<b>Order Details:</b> {err.get('order_details', {})}\n"
            )
            
        elif event.event_type == "news_update" and self.send_news:
            headlines = payload.get("headlines", []) if isinstance(payload, dict) else []
            if not headlines:
                return None
            lines = ["📰 <b>[MARKET NEWS]</b>"]
            for h in headlines:
                title = h.get("title") or "—"
                src = h.get("source") or "news"
                syms = ", ".join(h.get("symbols") or [])
                tag = f" <i>({syms})</i>" if syms else ""
                lines.append(f"• {title}{tag}\n<code>{src}</code>")
            message = "\n".join(lines)

        elif event.event_type in ("trade_rejected", "trade_blocked") and self.send_alerts:
            reason = payload.get("reason", "Unknown restriction")
            symbol = payload.get("symbol", "UNKNOWN")
            confidence = payload.get("confidence", 0.0)
            rec_strategy = payload.get("strategy_name", "None")
            message = (
                f"🚫 <b>[SPV TRADE REJECTED]</b>\n"
                f"<b>Symbol:</b> {symbol}\n"
                f"<b>Reason:</b> {reason}\n"
                f"<b>Confidence:</b> {confidence}%\n"
                f"<b>Strategy:</b> {rec_strategy}\n"
            )

        if message:
            success = await self.bot.send_message(message, chat_id=telegram_chat_id)
            processing_time = (time.perf_counter() - start_time) * 1000.0
            return AgentResultModel(
                agent_name=self.agent_name,
                signal="NONE",
                confidence=100.0,
                reason="Telegram notification broadcast completed.",
                processing_time=processing_time,
                metadata={"sent": success}
            )
            
        return None
