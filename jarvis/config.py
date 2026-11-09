"""Configuration: `jarvis.toml` for strategy/risk, environment for secrets.

Secrets are only ever read from the environment (or a local `.env`), never
from the TOML file, so the config can be committed and shared safely.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .risk import RiskConfig
from .strategy import StrategyConfig

LIVE_CONFIRM_VALUE = "I_UNDERSTAND_THE_RISKS"

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "COST", "JPM", "V", "LLY", "UNH", "XOM", "PLTR", "CRM", "ORCL", "ADBE",
]  # fmt: skip


@dataclass
class Config:
    mode: str = "paper"  # demo | paper | live
    universe: list[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    benchmark: str = "SPY"
    timeframe: str = "15Min"
    lookback: int = 200
    scan_interval_sec: int = 300
    analyst: str = "rules"  # rules | claude | claude-code | ollama
    analyst_model: str | None = None
    analyst_effort: str = "medium"
    analyst_interval_sec: int = 900
    max_candidates: int = 5
    broker_side_stops: bool = True
    flatten_eod: bool = False
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8777
    data_dir: str = "~/.jarvis"
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    @property
    def data_path(self) -> Path:
        return Path(os.path.expanduser(self.data_dir))

    def public_dict(self) -> dict[str, Any]:
        """Safe to show in the dashboard - contains no secrets by construction."""
        from dataclasses import asdict

        d = asdict(self)
        d.pop("data_dir", None)
        return d


def load_dotenv(path: str | Path = ".env") -> list[str]:
    """Minimal .env loader. Never overrides variables already set. Returns warnings."""
    p = Path(path)
    warnings: list[str] = []
    if not p.is_file():
        return warnings
    mode = p.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        warnings.append(f"{p} is readable by other users - run: chmod 600 {p}")
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
    return warnings


def _apply(obj: Any, values: dict[str, Any]) -> None:
    names = {f.name: f for f in fields(obj)}
    for k, v in values.items():
        if k not in names:
            raise ValueError(f"unknown setting '{k}' in [{type(obj).__name__}]")
        setattr(obj, k, v)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    p = Path(path) if path else Path("jarvis.toml")
    if p.is_file():
        data = tomllib.loads(p.read_text())
        _apply(cfg.strategy, data.pop("strategy", {}))
        _apply(cfg.risk, data.pop("risk", {}))
        _apply(cfg, data.pop("jarvis", {}))
        if data:
            raise ValueError(f"unknown sections in {p}: {', '.join(data)}")
    elif path:
        raise FileNotFoundError(p)
    env = os.environ
    if env.get("JARVIS_MODE"):
        cfg.mode = env["JARVIS_MODE"]
    if env.get("JARVIS_ANALYST"):
        cfg.analyst = env["JARVIS_ANALYST"]
    if env.get("JARVIS_DASHBOARD_HOST"):
        cfg.dashboard_host = env["JARVIS_DASHBOARD_HOST"]
    if env.get("JARVIS_UNIVERSE"):
        cfg.universe = [s.strip().upper() for s in env["JARVIS_UNIVERSE"].split(",") if s.strip()]
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.mode not in ("demo", "paper", "live"):
        raise ValueError("mode must be demo, paper or live")
    r = cfg.risk
    checks = [
        (0 < r.max_position_pct <= 1, "risk.max_position_pct must be in (0, 1]"),
        (0 < r.max_gross_exposure_pct <= 1, "risk.max_gross_exposure_pct must be in (0, 1] - JARVIS never uses margin"),
        (0 < r.risk_per_trade_pct <= 0.05, "risk.risk_per_trade_pct must be in (0, 0.05]"),
        (r.stop_atr_mult > 0, "risk.stop_atr_mult must be > 0 - every trade needs a stop"),
        (0 < r.max_daily_loss_pct < 1, "risk.max_daily_loss_pct must be in (0, 1)"),
        (0 < r.max_drawdown_pct < 1, "risk.max_drawdown_pct must be in (0, 1)"),
        (r.max_positions >= 1, "risk.max_positions must be >= 1"),
        (cfg.scan_interval_sec >= 1, "scan_interval_sec must be >= 1"),
        (cfg.analyst in ("rules", "claude", "claude-code", "ollama"), "analyst must be rules, claude, claude-code or ollama"),
        (len(cfg.universe) > 0, "universe is empty"),
    ]
    for ok, msg in checks:
        if not ok:
            raise ValueError(msg)


def live_unlocked() -> bool:
    return os.environ.get("JARVIS_LIVE_TRADING") == LIVE_CONFIRM_VALUE


def alpaca_keys() -> tuple[str, str]:
    e = os.environ
    return (
        e.get("ALPACA_API_KEY_ID") or e.get("APCA_API_KEY_ID") or "",
        e.get("ALPACA_API_SECRET_KEY") or e.get("APCA_API_SECRET_KEY") or "",
    )
