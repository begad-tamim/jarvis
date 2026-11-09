"""A realistic sample workspace for `jarvis demo`: seven small git repos with real history.

Nothing is faked at read time: JARVIS reads these repos with git exactly like
it reads yours. They live in ~/.jarvis/demo-workspace and are safe to delete.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

AUTHOR = ["-c", "user.name=Demo Dev", "-c", "user.email=demo@jarvis.local", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]

# name, readme line, files, commits [(days_ago, message)], state tweaks
PROJECTS = [
    ("orbit-api", "Patient-scheduling REST API with FastAPI and Postgres.",
     {"pyproject.toml": "[project]\nname = 'orbit-api'\n", "app/main.py": "# TODO: rate-limit /bookings before launch\n# FIXME: timezone bug when clinics span DST\nfrom fastapi import FastAPI\napp = FastAPI()\n",
      "app/auth.py": "# TODO: rotate JWT signing keys monthly\n", "Dockerfile": "FROM python:3.12-slim\n"},
     [(9, "init: FastAPI skeleton"), (6, "feat: bookings endpoint"), (3, "feat: JWT auth"), (1, "test: booking conflicts"), (0.2, "feat: SMS reminders (wip)")],
     {"dirty": 9}),
    ("nova-web", "Marketing site and dashboard in Next.js.",
     {"package.json": '{"name":"nova-web"}\n', "next.config.mjs": "export default {}\n", "app/page.tsx": "// TODO: hero video poster image\nexport default function Page(){return null}\n"},
     [(12, "init: next app"), (5, "feat: pricing page"), (2, "feat: dark mode"), (0.4, "fix: mobile nav overflow")],
     {"dirty": 3}),
    ("ledger-pay", "Payments gateway in Go: Stripe webhooks, idempotent charges.",
     {"go.mod": "module ledger\n", "main.go": "package main\n// TODO: retry with jitter on 429\nfunc main(){}\n"},
     [(20, "init"), (8, "feat: idempotency keys"), (1, "feat: refunds"), (0.5, "chore: bump deps")],
     {"ahead": 2}),
    ("atlas-ml", "Demand-forecasting pipeline: features, training, batch scoring.",
     {"pyproject.toml": "[project]\nname='atlas-ml'\n", "pipeline/train.py": "# TODO: early stopping\n# TODO: log feature importances\n"},
     [(15, "init: pipeline"), (7, "feat: feature store"), (4, "feat: xgboost baseline")],
     {"behind": 3}),
    ("pulse-mobile", "Flutter app for iOS and Android with offline sync.",
     {"pubspec.yaml": "name: pulse\n", "lib/main.dart": "// TODO: push notification deep links\nvoid main(){}\n"},
     [(18, "init: flutter"), (9, "feat: offline sync"), (2, "feat: biometric login"), (1, "release: 1.4.0")],
     {}),
    ("beacon-infra", "Terraform + Docker for staging and production.",
     {"Dockerfile": "FROM alpine\n", "main.tf": "# FIXME: staging DB has no backups\n"},
     [(30, "init"), (10, "feat: staging env"), (3, "feat: blue/green deploys")],
     {}),
    ("folio-site", "Personal portfolio built with Vite.",
     {"package.json": '{"name":"folio"}\n', "vite.config.ts": "export default {}\n"},
     [(60, "init"), (40, "feat: projects grid"), (21, "content: case studies")],
     {}),
]  # fmt: skip


def _git(cwd: Path, *args: str, when: float | None = None) -> None:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    if when:
        stamp = f"{int(when)} +0000"
        env |= {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", *AUTHOR, *args], cwd=cwd, env=env, check=True, capture_output=True)  # noqa: S603, S607


def _commit(repo: Path, msg: str, when: float, n: int) -> None:
    (repo / "CHANGELOG.md").write_text(
        (repo / "CHANGELOG.md").read_text() + f"- {msg}\n" if (repo / "CHANGELOG.md").exists() else f"- {msg}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, when=when)


def build(root: Path | None = None, fresh: bool = False) -> Path:
    root = (root or Path.home() / ".jarvis" / "demo-workspace").expanduser()
    marker = root / ".jarvis-demo-v4"
    if marker.exists() and not fresh:
        return root
    if root.exists():
        shutil.rmtree(root)
    (root / ".remotes").mkdir(parents=True)
    now = time.time()
    for name, desc, files, commits, tweak in PROJECTS:
        repo = root / name
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "README.md").write_text(f"# {name}\n\n{desc}\n")
        for f, body in files.items():
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            (repo / f).write_text(body)
        for i, (days, msg) in enumerate(commits):
            _commit(repo, msg, now - days * 86400, i)
        if tweak.get("ahead") or tweak.get("behind"):
            remote = root / ".remotes" / f"{name}.git"
            _git(root, "init", "-q", "--bare", str(remote))
            _git(repo, "remote", "add", "origin", str(remote))
            _git(repo, "push", "-q", "-u", "origin", "main")
        for k in range(tweak.get("ahead", 0)):
            _commit(repo, ["feat: payout reports", "fix: currency rounding"][k % 2], now - 3600 * (5 - k), 10 + k)
        if tweak.get("behind"):
            other = root / ".remotes" / f"{name}-teammate"
            _git(root, "clone", "-q", str(root / ".remotes" / f"{name}.git"), str(other))
            for k, msg in enumerate(["feat: holiday features", "fix: leakage in split", "perf: parquet cache"][: tweak["behind"]]):
                _commit(other, msg, now - 3600 * (6 - k), 20 + k)
            _git(other, "push", "-q", "origin", "main")
            _git(repo, "fetch", "-q", "origin")
        wip = WIP.get(name, [])
        for k in range(tweak.get("dirty", 0)):
            rel = wip[k] if k < len(wip) else f"notes/scratch_{k}.md"
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((target.read_text() if target.exists() else "") + f"# edited {k}\n")
    marker.write_text("ok\n")
    return root


WIP = {
    "orbit-api": [
        "app/main.py",
        "app/auth.py",
        "app/sms.py",
        "app/reminders.py",
        "app/models.py",
        "tests/test_sms.py",
        "tests/test_reminders.py",
        "migrations/0007_reminders.sql",
        "README.md",
    ],
    "nova-web": ["app/page.tsx", "app/pricing/page.tsx", "app/globals.css"],
}

SEED_TASKS = {
    "orbit-api": ["Ship SMS reminders", "Load-test /bookings at 500 rps", "Write the on-call runbook"],
    "nova-web": ["Record the hero video", "Lighthouse > 95 on mobile"],
    "ledger-pay": ["Push refunds to staging"],
}
SEED_NOTES = {
    "orbit-api": ["Clinic pilot starts Monday: keep /bookings stable.", "Postgres is on 16.4 in prod."],
    "atlas-ml": ["Teammate pushed holiday features, pull before retraining."],
}
