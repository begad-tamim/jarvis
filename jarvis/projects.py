"""Your projects, at a glance: discovery, live git status and a free daily briefing.

Everything here is read-only. JARVIS runs a handful of `git` plumbing commands
with a timeout and never writes to your repositories. Projects are addressed by
an id (a slug), never by a path that came from a request.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.projects")

STACK_HINTS = [
    ("pyproject.toml", "Python"), ("requirements.txt", "Python"), ("package.json", "Node"),
    ("tsconfig.json", "TypeScript"), ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("pom.xml", "Java"),
    ("build.gradle", "Kotlin/Java"), ("Package.swift", "Swift"), ("pubspec.yaml", "Flutter"),
    ("Gemfile", "Ruby"), ("composer.json", "PHP"), ("Dockerfile", "Docker"), ("next.config.js", "Next.js"),
    ("next.config.mjs", "Next.js"), ("vite.config.ts", "Vite"),
]  # fmt: skip
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".next", "target", "vendor"}
TODO_RE = re.compile(rb"\b(TODO|FIXME|HACK|XXX)\b")
TODO_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".swift", ".rb", ".php", ".md", ".css", ".html"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


@dataclass
class Project:
    id: str
    name: str
    path: str
    description: str = ""
    stack: list[str] = field(default_factory=list)


@dataclass
class Status:
    branch: str = ""
    dirty: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    has_remote: bool = False
    last_commit: str = ""
    last_commit_at: float = 0.0
    commits_7d: int = 0
    todos: int = 0
    state: str = "unknown"  # attention | active | ready | quiet | unknown
    reason: str = ""
    checked_at: float = 0.0


def _git(path: str, *args: str, timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout, check=False,  # noqa: S607
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"},
        )  # fmt: skip
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _describe(p: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README"):
        f = p / name
        if f.is_file():
            try:
                for line in f.read_text(errors="replace").splitlines()[:40]:
                    s = line.strip()
                    if s and not s.startswith(("#", "<", "!", "[", "=", "-", "`", "|", ">")):
                        return s[:160]
            except OSError:
                pass
    return ""


def _stack(p: Path) -> list[str]:
    out: list[str] = []
    for fname, label in STACK_HINTS:
        if (p / fname).exists() and label not in out:
            out.append(label)
    return out[:4]


def _count_todos(p: Path, limit_files: int = 400) -> int:
    n, seen = 0, 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if Path(f).suffix not in TODO_EXT:
                continue
            seen += 1
            if seen > limit_files:
                return n
            try:
                fp = Path(root) / f
                if fp.stat().st_size < 400_000:
                    n += len(TODO_RE.findall(fp.read_bytes()))
            except OSError:
                pass
    return n


def project_from_path(path: str | Path) -> Project:
    p = Path(path).expanduser().resolve()
    return Project(id=slug(p.name), name=p.name, path=str(p), description=_describe(p), stack=_stack(p))


def discover(roots: list[str], explicit: list[str] | None = None, max_projects: int = 60) -> list[Project]:
    """Git repositories directly inside each root (plus any explicit paths)."""
    found: dict[str, Project] = {}
    candidates: list[Path] = [Path(e).expanduser() for e in (explicit or [])]
    for r in roots:
        root = Path(r).expanduser()
        if not root.is_dir():
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        candidates += [c for c in children if c.is_dir() and not c.name.startswith(".") and (c / ".git").exists()]
    for c in candidates:
        if not c.is_dir():
            continue
        proj = project_from_path(c)
        base, k = proj.id, 2
        while proj.id in found:
            proj.id = f"{base}-{k}"
            k += 1
        found[proj.id] = proj
        if len(found) >= max_projects:
            break
    return list(found.values())


def read_status(proj: Project, now: float | None = None) -> Status:
    now = now or time.time()
    s = Status(checked_at=now)
    porcelain = _git(proj.path, "status", "--porcelain=v1", "--branch")
    if not porcelain:
        s.state, s.reason = "unknown", "not a git repository (or git is missing)"
        return s
    lines = porcelain.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    s.branch = head.split("...")[0].replace("No commits yet on ", "")
    s.has_remote = "..." in head
    m = re.search(r"ahead (\d+)", head)
    s.ahead = int(m.group(1)) if m else 0
    m = re.search(r"behind (\d+)", head)
    s.behind = int(m.group(1)) if m else 0
    for ln in lines[1:]:
        if ln.startswith("??"):
            s.untracked += 1
        elif ln.strip():
            s.dirty += 1
    log_line = _git(proj.path, "log", "-1", "--format=%ct%x09%s")
    if log_line.strip():
        ts, _, msg = log_line.strip().partition("\t")
        s.last_commit, s.last_commit_at = msg[:140], float(ts or 0)
    since = _git(proj.path, "rev-list", "--count", "--since=7.days", "HEAD")
    s.commits_7d = int(since.strip() or 0) if since.strip().isdigit() else 0
    s.todos = _count_todos(Path(proj.path))
    s.state, s.reason = classify(s, now)
    return s


def classify(s: Status, now: float) -> tuple[str, str]:
    age_days = (now - s.last_commit_at) / 86400 if s.last_commit_at else 999
    if s.behind:
        return "attention", f"{s.behind} commit{'s' * (s.behind != 1)} behind the remote - pull before you build"
    if s.dirty + s.untracked >= 8:
        return "attention", f"{s.dirty + s.untracked} uncommitted changes - checkpoint your work"
    if s.dirty or s.untracked:
        return "active", f"work in progress on {s.branch or 'HEAD'} ({s.dirty + s.untracked} changed)"
    if s.ahead:
        return "ready", f"clean, {s.ahead} commit{'s' * (s.ahead != 1)} ready to push"
    if age_days > 14:
        return "quiet", f"no commits for {int(age_days)} days"
    return "ready", "clean and in sync"


def ago(ts: float, now: float | None = None) -> str:
    if not ts:
        return "never"
    d = (now or time.time()) - ts
    for unit, sec in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d // sec)}{unit} ago"
    return "just now"


class Registry:
    """Projects + cached status. Thread-safe; refreshes lazily."""

    def __init__(self, projects: list[Project], ttl: float = 20.0):
        self.projects = {p.id: p for p in projects}
        self.ttl = ttl
        self._status: dict[str, Status] = {}
        self._lock = threading.Lock()

    def get(self, pid: str | None) -> Project | None:
        return self.projects.get(pid or "")

    def find(self, text: str) -> Project | None:
        """Best project match for free text ('open the clinic api' -> clinic-api)."""
        t = slug(text)
        if not t:
            return None
        best, score = None, 0
        words = set(t.split("-"))
        for p in self.projects.values():
            for key in {p.id, slug(p.name)}:
                if key in t:
                    sc = 100 + len(key)
                elif len(t) >= 3 and key.startswith(t):
                    sc = 60
                else:
                    sc = sum(8 if len(w) >= 4 else 3 for w in key.split("-") if w in words)
                if sc > score:
                    best, score = p, sc
        return best if score >= 6 else None

    def status(self, pid: str, force: bool = False) -> Status:
        with self._lock:
            st = self._status.get(pid)
        if st and not force and time.time() - st.checked_at < self.ttl:
            return st
        st = read_status(self.projects[pid])
        with self._lock:
            self._status[pid] = st
        return st

    def all(self, force: bool = False) -> list[dict[str, Any]]:
        out = []
        for p in self.projects.values():
            st = self.status(p.id, force)
            out.append({**asdict(p), "status": asdict(st), "last_commit_ago": ago(st.last_commit_at)})
        order = {"attention": 0, "active": 1, "ready": 2, "quiet": 3, "unknown": 4}
        out.sort(key=lambda x: (order.get(x["status"]["state"], 9), -x["status"]["last_commit_at"]))
        return out


def briefing(items: list[dict[str, Any]], tasks_open: int = 0) -> dict[str, Any]:
    """The daily briefing: computed from git, costs zero tokens."""
    buckets: dict[str, list[dict[str, Any]]] = {"attention": [], "active": [], "ready": [], "quiet": []}
    for it in items:
        buckets.setdefault(it["status"]["state"], []).append({"id": it["id"], "name": it["name"], "reason": it["status"]["reason"]})
    commits = sum(it["status"]["commits_7d"] for it in items)
    hot = max(items, key=lambda x: x["status"]["commits_7d"], default=None)
    lines = []
    if buckets["attention"]:
        lines.append(f"{len(buckets['attention'])} need attention: " + ", ".join(b["name"] for b in buckets["attention"][:3]) + ".")
    if buckets["active"]:
        lines.append(f"{len(buckets['active'])} in progress.")
    if buckets["ready"]:
        lines.append(f"{len(buckets['ready'])} clean and ready.")
    if hot and hot["status"]["commits_7d"]:
        lines.append(f"Most active this week: {hot['name']} ({hot['status']['commits_7d']} commits).")
    return {
        "headline": " ".join(lines) or "No projects found yet. Add a folder to project_roots in jarvis.toml.",
        "buckets": buckets,
        "projects": len(items),
        "commits_7d": commits,
        "todos": sum(it["status"]["todos"] for it in items),
        "tasks_open": tasks_open,
    }
