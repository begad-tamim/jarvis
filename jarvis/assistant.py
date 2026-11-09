"""The assistant: one conversation, every channel.

The HUD, your phone, Telegram and a phone call all go through `Assistant.ask`.
Simple, frequent requests ("brief me", "open clinic-api", "remember …",
"markets") are answered locally in milliseconds for free. Everything else goes
to the configured brain, running inside the active project's folder with a
short, fenced context block about your workspace.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .brains import Answer, Brain, BrainError
from .bus import Bus
from .projects import Registry, ago, briefing

log = logging.getLogger("jarvis.assistant")
MAX_TEXT = 4000


@dataclass
class Message:
    id: str
    role: str  # user | jarvis
    text: str
    t: float
    project: str | None = None
    source: str = "hud"  # hud | phone | voice | telegram | call | cli
    brain: str = ""
    ms: int = 0
    action: dict[str, Any] = field(default_factory=dict)


class Assistant:
    def __init__(self, registry: Registry, brain: Brain, journal: Any, bus: Bus, engine: Any = None, voice: Any = None):
        self.registry, self.brain, self.journal, self.bus = registry, brain, journal, bus
        self.engine, self.voice = engine, voice
        self.history: list[Message] = []
        self.active: str | None = None
        self.sessions: dict[str, str] = {}
        self._lock = threading.Lock()
        self.busy = 0

    # ------------------------------------------------------------------ notes & tasks (stored in the journal)
    def notes(self, pid: str) -> list[dict[str, Any]]:
        return self.journal.get(f"notes:{pid}", [])

    def add_note(self, pid: str, text: str) -> dict[str, Any]:
        n = {"id": uuid.uuid4().hex[:8], "text": text.strip()[:500], "t": time.time()}
        notes = [n, *self.notes(pid)][:100]
        self.journal.set(f"notes:{pid}", notes)
        self.bus.publish("notes", {"project": pid, "notes": notes})
        return n

    def tasks(self, pid: str) -> list[dict[str, Any]]:
        return self.journal.get(f"tasks:{pid}", [])

    def add_task(self, pid: str, text: str) -> dict[str, Any]:
        t = {"id": uuid.uuid4().hex[:8], "text": text.strip()[:300], "done": False, "t": time.time()}
        tasks = [*self.tasks(pid), t][-100:]
        self.journal.set(f"tasks:{pid}", tasks)
        self.bus.publish("tasks", {"project": pid, "tasks": tasks})
        return t

    def toggle_task(self, pid: str, tid: str) -> list[dict[str, Any]]:
        tasks = self.tasks(pid)
        for t in tasks:
            if t["id"] == tid:
                t["done"] = not t["done"]
        self.journal.set(f"tasks:{pid}", tasks)
        self.bus.publish("tasks", {"project": pid, "tasks": tasks})
        return tasks

    def open_tasks(self) -> int:
        return sum(1 for pid in self.registry.projects for t in self.tasks(pid) if not t["done"])

    # ------------------------------------------------------------------ views
    def briefing(self, force: bool = False) -> dict[str, Any]:
        b = briefing(self.registry.all(force), self.open_tasks())
        if self.engine is not None:
            s = self.engine.snapshot()
            b["markets"] = {
                "equity": s["account"]["equity"],
                "day_pnl": s["account"]["day_pl"],
                "positions": len(s["positions"]),
                "status": s["status"],
            }
        return b

    def project_view(self, pid: str) -> dict[str, Any] | None:
        p = self.registry.get(pid)
        if p is None:
            return None
        st = self.registry.status(pid, force=True)
        return {
            **asdict(p), "status": asdict(st), "last_commit_ago": ago(st.last_commit_at),
            "notes": self.notes(pid), "tasks": self.tasks(pid),
            "messages": [asdict(m) for m in self.history if m.project == pid][-40:],
        }  # fmt: skip

    def recent(self, n: int = 60) -> list[dict[str, Any]]:
        return [asdict(m) for m in self.history[-n:]]

    # ------------------------------------------------------------------ the one entry point
    def ask(self, text: str, project: str | None = None, source: str = "hud") -> Message:
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError("empty message")
        pid = project if self.registry.get(project) else self.active
        user = self._push(Message(uuid.uuid4().hex[:10], "user", text, time.time(), pid, source))
        self.busy += 1
        self.bus.publish("busy", {"busy": self.busy, "project": pid})
        try:
            reply = self._local(text, pid, source) or self._think(text, pid, source)
        finally:
            self.busy -= 1
            self.bus.publish("busy", {"busy": self.busy, "project": pid})
        reply.id, reply.t = uuid.uuid4().hex[:10], time.time()
        reply.project = reply.project if reply.project is not None else user.project
        return self._push(reply)

    def _push(self, m: Message) -> Message:
        with self._lock:
            self.history.append(m)
            self.history = self.history[-400:]
        self.bus.publish("message", asdict(m))
        return m

    # ------------------------------------------------------------------ fast local intents (free, instant)
    def _local(self, text: str, pid: str | None, source: str) -> Message | None:
        t = text.lower().strip(" ?!.")
        reply = lambda s, **a: Message("", "jarvis", s, 0, pid, source, "JARVIS", 0, a)  # noqa: E731

        if re.fullmatch(
            r"(brief me|briefing|daily briefing|morning( briefing)?|what'?s (up|my day|new)|status report|good morning( jarvis)?)", t
        ):
            b = self.briefing(force=True)
            extra = ""
            if "markets" in b:
                m = b["markets"]
                extra = f" Markets: equity ${m['equity']:,.0f}, {'+' if m['day_pnl'] >= 0 else '-'}${abs(m['day_pnl']):,.0f} today, {m['positions']} open positions."
            return reply(b["headline"] + extra + (f" {b['tasks_open']} open tasks." if b["tasks_open"] else ""), view="home")

        m = re.match(r"^(?:jarvis[, ]+)?(?:open|switch to|go to|jump to|work on|load)\s+(.+)$", t)
        if m:
            p = self.registry.find(m.group(1))
            if p:
                self.active = p.id
                st = self.registry.status(p.id, force=True)
                msg = Message(
                    "",
                    "jarvis",
                    f"{p.name} is open. {st.reason.capitalize()}. Last commit {ago(st.last_commit_at)}: {st.last_commit or 'none'}.",
                    0,
                    p.id,
                    source,
                    "JARVIS",
                    0,
                    {"view": "project", "project": p.id},
                )
                return msg
            if "market" in m.group(1) or "trading" in m.group(1) or "stocks" in m.group(1):
                return reply(
                    "Markets view is up." if self.engine else "The markets module is off. Start it with `jarvis up --markets`.",
                    view="markets",
                )

        m = re.match(r"^(?:what'?s the )?status (?:of|on|for)\s+(.+)$", t)
        if m and (p := self.registry.find(m.group(1))):
            st = self.registry.status(p.id, force=True)
            return reply(
                f"{p.name}: {st.reason}. Branch {st.branch or '?'}, {st.commits_7d} commits this week, {st.todos} TODOs. Last commit {ago(st.last_commit_at)}: {st.last_commit or 'none'}.",
                project=p.id,
            )

        m = re.match(r"^(?:remember|note|take a note)(?: that)?[:,]?\s+(.+)$", text, re.I)
        if m:
            target = pid or "_inbox"
            self.add_note(target, m.group(1))
            where = self.registry.get(target).name if self.registry.get(target) else "your inbox"
            return reply(f"Noted in {where}.")

        m = re.match(r"^(?:add (?:a )?task|todo|remind me to)[:,]?\s+(.+)$", text, re.I)
        if m and pid:
            self.add_task(pid, m.group(1))
            return reply(f"Added to {self.registry.get(pid).name}'s tasks.")

        if re.fullmatch(r"(markets?|portfolio|how('?s| is| are) (the )?(markets?|portfolio|trading)( doing)?|p&?l|stocks)", t):
            if self.engine is None:
                return reply("The markets module is off. Start it with `jarvis up --markets` or try `jarvis demo`.")
            s = self.engine.snapshot()
            a = s["account"]
            pos = (
                ", ".join(f"{p['symbol']} {'+' if p['pl'] >= 0 else '-'}${abs(p['pl']):,.0f}" for p in s["positions"][:4])
                or "no open positions"
            )
            return reply(
                f"Equity ${a['equity']:,.0f} ({'+' if a['day_pl'] >= 0 else '-'}${abs(a['day_pl']):,.0f} today). {pos}. Status: {s['status']}.",
                view="markets",
            )

        if re.fullmatch(r"(pause|stop) trading", t) and self.engine is not None:
            self.engine.pause()
            return reply("Trading paused. Exits and stops are still managed.", view="markets")
        if re.fullmatch(r"(resume|start) trading", t) and self.engine is not None:
            self.engine.resume()
            return reply("Trading resumed.", view="markets")
        if t in ("kill switch", "kill everything", "flatten everything"):
            return reply("For safety the kill switch needs a deliberate action: hold the Kill button in the HUD, or run `jarvis kill`.")

        m = re.match(r"^call me(?: (?:and|to) (?:say|tell me))?[:,]?\s*(.*)$", t)
        if m:
            if self.voice is None or not self.voice.ready:
                return reply("Phone calls aren't set up yet: add your Twilio keys to .env (see the README, 'JARVIS can call you').")
            self.voice.call(m.group(1) or "This is JARVIS. You asked me to call you.")
            return reply("Calling your phone now.")
        return None

    # ------------------------------------------------------------------ the brain
    def _context(self, pid: str | None) -> str:
        b = self.briefing()
        lines = [f"Workspace: {b['projects']} projects. {b['headline']}"]
        p = self.registry.get(pid)
        if p:
            st = self.registry.status(p.id)
            lines.append(
                f"Active project: {p.name} ({', '.join(p.stack) or 'unknown stack'}) at {p.path}. {st.reason}. Branch {st.branch}. Last commit {ago(st.last_commit_at)}: {st.last_commit}."
            )
            notes = self.notes(p.id)[:5]
            if notes:
                lines.append("Recent notes: " + " | ".join(n["text"] for n in notes))
            tasks = [t["text"] for t in self.tasks(p.id) if not t["done"]][:8]
            if tasks:
                lines.append("Open tasks: " + " | ".join(tasks))
        if "markets" in b:
            m = b["markets"]
            lines.append(
                f"Trading module: equity ${m['equity']:,.0f}, day P&L ${m['day_pnl']:,.0f}, {m['positions']} positions, {m['status']}."
            )
        recent = [m for m in self.history if m.project == pid][-7:-1]
        if recent:
            lines.append("Recent conversation: " + " | ".join(f"{m.role}: {m.text[:200]}" for m in recent))
        return "\n".join(lines)

    def _think(self, text: str, pid: str | None, source: str) -> Message:
        p = self.registry.get(pid)
        spoken = source in ("voice", "call")
        prompt = (
            f"<context>\n{self._context(pid)}\n</context>\n\n"
            + ("Reply in two or three short spoken sentences, no lists or symbols.\n\n" if spoken else "")
            + f"User: {text}"
        )
        key = pid or "_global"
        try:
            ans: Answer = self.brain.ask(prompt, cwd=p.path if p else None, session=self.sessions.get(key))
            if ans.session:
                self.sessions[key] = ans.session
            return Message("", "jarvis", ans.text or "(no answer)", 0, pid, source, ans.brain, ans.ms)
        except BrainError as e:
            log.warning("brain failed: %s", e)
            return Message("", "jarvis", f"My brain ({self.brain.label}) failed: {e}", 0, pid, source, self.brain.label, 0, {"error": True})
