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

    @property
    def input_event_types(self) -> List[str]:
        return ["risk_alert", "order_filled", "execution_failed"]

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
        
        if event.event_type == "risk_alert" and self.send_alerts:
            alert = event.payload
            message = (
                f"⚠️ <b>[SPV RISK ALERT]</b>\n"
                f"<b>Type:</b> {alert.get('alert_type', 'GENERAL')}\n"
                f"<b>Message:</b> {alert.get('message', 'No details available.')}\n"
            )
            
        elif event.event_type == "order_filled" and self.send_trades:
            trade = event.payload
            message = (
                f"🟢 <b>[TRADE EXECUTED]</b>\n"
                f"<b>Symbol:</b> {trade.get('symbol')}\n"
                f"<b>Side:</b> {trade.get('side')}\n"
                f"<b>Qty:</b> {trade.get('quantity')}\n"
                f"<b>Price:</b> {trade.get('price')}\n"
                f"<b>Status:</b> {trade.get('status')}\n"
                f"<b>Broker:</b> {trade.get('broker')}\n"
                f"<b>ID:</b> <code>{trade.get('order_id')}</code>"
            )
            
        elif event.event_type == "execution_failed" and self.send_alerts:
            err = event.payload
            message = (
                f"🔴 <b>[EXECUTION FAILED]</b>\n"
                f"<b>Reason:</b> {err.get('reason', 'Unknown error')}\n"
                f"<b>Order Details:</b> {err.get('order_details', {})}\n"
            )

        if message:
            success = await self.bot.send_message(message)
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
