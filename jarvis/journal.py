"""Append-only trade journal + audit log in SQLite (stdlib)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, kind TEXT NOT NULL, symbol TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, t);
CREATE TABLE IF NOT EXISTS equity (t REAL PRIMARY KEY, equity REAL NOT NULL, cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


class Journal:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)  # trade history is private

    def log(self, kind: str, data: dict[str, Any], symbol: str | None = None, t: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events(t, kind, symbol, data) VALUES (?,?,?,?)",
                (t or time.time(), kind, symbol, json.dumps(data, default=str)),
            )

    def events(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = "SELECT t, kind, symbol, data FROM events"
        args: tuple = ()
        if kind:
            q += " WHERE kind = ?"
            args = (kind,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(q, (*args, limit)).fetchall()
        return [{"t": t, "kind": k, "symbol": s, **json.loads(d)} for t, k, s, d in rows]

    def record_equity(self, equity: float, cash: float, t: float | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO equity(t, equity, cash) VALUES (?,?,?)", (t or time.time(), equity, cash))

    def equity_curve(self, limit: int = 2000) -> list[tuple[float, float]]:
        with self._lock:
            rows = self._db.execute("SELECT t, equity FROM equity ORDER BY t DESC LIMIT ?", (limit,)).fetchall()
        return rows[::-1]

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?,?)", (key, json.dumps(value, default=str)))

    def close(self) -> None:
        with self._lock:
            self._db.close()
