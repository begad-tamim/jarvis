"""Talk to JARVIS from anywhere through a private Telegram bot.

Long polling, so no public URL or open port is needed. Only chat ids listed in
TELEGRAM_CHAT_ID (comma-separated) are answered; everyone else is ignored.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..http import request_json

log = logging.getLogger("jarvis.telegram")
HELP = (
    "I'm JARVIS. Just talk to me.\n\n"
    "/brief  daily briefing\n/projects  list projects\n/open <name>  switch project\n"
    "/markets  portfolio\n/note <text>  remember something\n\nAnything else goes to my brain."
)


class TelegramBot:
    def __init__(self, token: str, allowed_chats: str, assistant: Any):
        self.token = token
        self.allowed = {c.strip() for c in allowed_chats.split(",") if c.strip()}
        self.assistant = assistant
        self.offset = 0
        self.stop = threading.Event()

    @property
    def ready(self) -> bool:
        return bool(self.token and self.allowed)

    def _api(self, method: str, **body: Any) -> Any:
        return request_json("POST", f"https://api.telegram.org/bot{self.token}/{method}", body=body, timeout=40, retries=1)

    def send(self, chat_id: str, text: str) -> None:
        for i in range(0, len(text), 3900):
            try:
                self._api("sendMessage", chat_id=chat_id, text=text[i : i + 3900])
            except Exception as e:  # noqa: BLE001 - never crash the bot on a send error
                log.warning("telegram send failed: %s", type(e).__name__)  # the URL holds the token: don't log it

    def broadcast(self, text: str) -> None:
        for c in self.allowed:
            self.send(c, text)

    def handle(self, update: dict[str, Any]) -> str | None:
        msg = update.get("message") or update.get("edited_message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return None
        if chat not in self.allowed:
            log.warning("ignored message from unknown chat")
            return None
        cmd, _, rest = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            reply = HELP
        elif cmd == "/brief":
            reply = self.assistant.ask("brief me", source="telegram").text
        elif cmd == "/projects":
            items = self.assistant.registry.all()
            reply = (
                "\n".join(f"{'●' if i['status']['state'] == 'attention' else '○'} {i['name']}: {i['status']['reason']}" for i in items)
                or "No projects yet."
            )
        elif cmd == "/open":
            reply = self.assistant.ask(f"open {rest}", source="telegram").text
        elif cmd == "/markets":
            reply = self.assistant.ask("markets", source="telegram").text
        elif cmd == "/note":
            reply = self.assistant.ask(f"remember {rest}", source="telegram").text
        else:
            reply = self.assistant.ask(text, source="telegram").text
        self.send(chat, reply)
        return reply

    def poll_once(self) -> int:
        res = self._api("getUpdates", offset=self.offset, timeout=25, allowed_updates=["message", "edited_message"])
        updates = (res or {}).get("result", [])
        for u in updates:
            self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)
            try:
                self.handle(u)
            except Exception:  # noqa: BLE001
                log.exception("telegram update failed")
        return len(updates)

    def run(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                self.poll_once()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 - network blips: back off and retry
                log.warning("telegram poll failed: %s", type(e).__name__)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.run, name="telegram", daemon=True)
        th.start()
        return th
