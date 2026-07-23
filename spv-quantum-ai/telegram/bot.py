import asyncio
import time
import httpx
from typing import Optional
from core.config import settings
from core.logging import get_logger

logger = get_logger("telegram_bot")

# Telegram allows roughly 20 messages per minute to a channel. Sending faster
# earns 429 "Too Many Requests" and the alert is simply lost — which on
# 2026-07-21 silently dropped 42 notifications in 20 minutes. Pace sends so
# they arrive instead of being rejected.
_MIN_SEND_INTERVAL_SEC = 3.2      # ≈18 msg/min, safely under the channel cap
_MAX_QUEUE_WAIT_SEC = 30.0        # never stall a trading agent longer than this


class TelegramBotClient:
    """
    Wrapper client around Telegram's HTTP API.
    Sends markdown or HTML formatted strings directly to a chat room.

    Sends are serialised and rate-limited: a burst of alerts is spread out
    rather than rejected, and 429 responses are retried once honouring the
    ``retry_after`` the API asks for.
    """
    def __init__(self) -> None:
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.client: Optional[httpx.AsyncClient] = None
        self._send_lock = asyncio.Lock()
        self._next_send_at = 0.0      # monotonic clock; next moment a send is allowed
        self._dropped = 0             # alerts shed because the backlog was too long

        if self.token:
            self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        else:
            self.api_url = None

    async def start(self) -> None:
        """Initializes the HTTP client session."""
        self.client = httpx.AsyncClient()
        if not self.token or not self.chat_id:
            logger.warning("Telegram Bot Token or Chat ID is missing. Notifications will bypass HTTP send.")

    async def close(self) -> None:
        """Closes the HTTP client session."""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def send_message(self, message: str, parse_mode: str = "HTML", chat_id: Optional[str] = None) -> bool:
        """
        Sends a message to the specified Chat ID using Telegram Bot API.
        Args:
            message: Formatted text to send.
            parse_mode: Formatting syntax choice ('HTML' or 'MarkdownV2').
            chat_id: Optional target chat ID. Defaults to configured self.chat_id.
        """
        target_chat_id = chat_id or self.chat_id
        if not self.api_url or not target_chat_id:
            logger.debug("Bypassed sending Telegram message (credentials absent)", telegram_message=message)
            return False

        if not self.client:
            logger.error("Telegram bot client session is closed. Call start() first.")
            return False

        payload = {
            "chat_id": target_chat_id,
            "text": message,
            "parse_mode": parse_mode
        }

        # One sender at a time, paced. Without this the agents post in bursts and
        # Telegram answers 429, losing the alert entirely.
        async with self._send_lock:
            wait = self._next_send_at - time.monotonic()
            if wait > _MAX_QUEUE_WAIT_SEC:
                # Backlog is too deep to be worth delivering — shed this alert
                # rather than blocking the caller (an agent) for minutes.
                self._dropped += 1
                logger.warning(
                    "Dropped Telegram alert — send queue too long",
                    backlog_sec=round(wait, 1),
                    dropped_total=self._dropped,
                )
                return False
            if wait > 0:
                await asyncio.sleep(wait)

            sent = await self._post_once(payload, target_chat_id)
            self._next_send_at = time.monotonic() + _MIN_SEND_INTERVAL_SEC
            return sent

    async def _post_once(self, payload: dict, target_chat_id: str, _retry: bool = True) -> bool:
        """Single POST to Telegram, retrying once when the API asks us to back off."""
        try:
            # 5-second timeout to prevent stalling the async loop on slow APIs
            response = await self.client.post(self.api_url, json=payload, timeout=5.0)

            if response.status_code == 200:
                logger.debug("Telegram message sent successfully.")
                return True

            if response.status_code == 429 and _retry:
                # Telegram tells us exactly how long to wait — honour it.
                try:
                    retry_after = float(response.json()["parameters"]["retry_after"])
                except Exception:
                    retry_after = _MIN_SEND_INTERVAL_SEC
                retry_after = min(retry_after, _MAX_QUEUE_WAIT_SEC)
                logger.warning("Telegram rate limited — backing off", retry_after=retry_after)
                await asyncio.sleep(retry_after + 0.5)
                return await self._post_once(payload, target_chat_id, _retry=False)

            logger.error(
                "Telegram API rejected request",
                status=response.status_code,
                body=response.text[:300],
            )
            return False
        except Exception as e:
            # A failed alert must not spew a full traceback every few seconds —
            # the box only has ~7 GB of free disk.
            logger.warning("Error posting to Telegram API", error=str(e))
            return False
