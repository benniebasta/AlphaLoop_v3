"""Async Telegram alert sender."""

import html
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


def _e(v) -> str:
    """Escape a dynamic value for Telegram HTML."""
    return html.escape(str(v))


class TelegramNotifier:
    """Async Telegram notifier — reads credentials dynamically."""

    def __init__(
        self,
        token: str = "",
        chat_id: str = "",
        enabled: bool = True,
        settings_service=None,
    ):
        self._token = token
        self._chat_id = chat_id
        self._enabled = enabled
        self._settings_service = settings_service

    async def _get_creds(self) -> tuple[str, str, bool]:
        if self._settings_service:
            try:
                token = await self._settings_service.get("TELEGRAM_TOKEN", self._token)
                chat_id = await self._settings_service.get("TELEGRAM_CHAT_ID", self._chat_id)
                enabled_str = await self._settings_service.get("TELEGRAM_ENABLED", "true")
                return token, chat_id, enabled_str.lower() != "false"
            except Exception:
                pass
        return self._token, self._chat_id, self._enabled

    async def send(self, text: str) -> bool:
        token, chat_id, enabled = await self._get_creds()
        if not enabled or not token or not chat_id:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False

    async def alert_trade_opened(
        self,
        direction: str,
        symbol: str,
        entry: float,
        sl: float,
        tp1: float,
        lots: float,
        confidence: float,
        setup: str,
        session: str,
    ) -> None:
        emoji = "BUY" if direction == "BUY" else "SELL"
        text = (
            f"<b>{emoji} Trade Opened</b>\n"
            f"Symbol: {_e(symbol)}\n"
            f"Setup: {_e(setup)} ({_e(session)})\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f} | TP: {tp1:.2f}\n"
            f"Size: {lots} lots\n"
            f"Confidence: {confidence:.0%}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        await self.send(text)

    async def alert_trade_closed(
        self,
        direction: str,
        symbol: str,
        outcome: str,
        pnl: float,
        lots: float,
    ) -> None:
        text = (
            f"<b>Trade Closed — {_e(outcome)}</b>\n"
            f"Symbol: {_e(symbol)} | {_e(direction)}\n"
            f"PnL: ${pnl:+.2f}\n"
            f"Size: {lots} lots"
        )
        await self.send(text)

    async def alert_kill_switch(self, reason: str) -> None:
        text = f"<b>KILL SWITCH ACTIVATED</b>\n{_e(reason)}"
        await self.send(text)

    async def alert_signal_rejected(
        self, symbol: str, reasons: list[str]
    ) -> None:
        reasons_text = "\n".join(f"- {_e(r)}" for r in reasons[:5])
        text = f"<b>Signal Rejected</b>\n{_e(symbol)}\n{reasons_text}"
        await self.send(text)

    async def alert_bot_started(
        self, symbol: str, mode: str, instance_id: str
    ) -> None:
        text = (
            f"<b>AlphaLoop Started</b>\n"
            f"Symbol: {_e(symbol)}\n"
            f"Mode: {_e(mode)}\n"
            f"Instance: {_e(instance_id)}"
        )
        await self.send(text)
