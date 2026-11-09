"""Push trade alerts to your phone: Telegram, Discord or Slack. All optional."""

from __future__ import annotations

import logging
import os
import threading

from .http import request_json

log = logging.getLogger("jarvis.notify")


class Notifier:
    def __init__(self) -> None:
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.discord = os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.slack = os.environ.get("SLACK_WEBHOOK_URL", "")

    @property
    def channels(self) -> list[str]:
        return [
            n
            for n, on in (("telegram", self.telegram_token and self.telegram_chat), ("discord", self.discord), ("slack", self.slack))
            if on
        ]

    def send(self, text: str) -> None:
        if self.channels:
            threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text: str) -> None:
        jobs = []
        if self.telegram_token and self.telegram_chat:
            jobs.append(
                (
                    "telegram",
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    {"chat_id": self.telegram_chat, "text": text},
                )
            )
        if self.discord.startswith("https://"):
            jobs.append(("discord", self.discord, {"content": text[:1900]}))
        if self.slack.startswith("https://"):
            jobs.append(("slack", self.slack, {"text": text}))
        for name, url, body in jobs:
            try:
                request_json("POST", url, body=body, retries=1, timeout=10)
            except Exception as e:  # noqa: BLE001 - alerts are best effort
                log.warning("%s alert failed: %s", name, type(e).__name__)  # never log the URL: it contains the secret
