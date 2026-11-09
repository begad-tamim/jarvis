import time

import pytest

from jarvis import demo_workspace
from jarvis.projects import Registry, Status, briefing, classify, discover


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = demo_workspace.build(tmp_path_factory.mktemp("ws") / "demo")
    return Registry(discover([str(root)]))


def test_discovers_all_demo_repos(ws):
    assert set(ws.projects) == {p[0] for p in demo_workspace.PROJECTS}


def test_reads_real_git_state(ws):
    orbit = ws.status("orbit-api")
    assert orbit.state == "attention" and orbit.dirty + orbit.untracked >= 8
    assert ws.status("ledger-pay").ahead == 2
    assert ws.status("atlas-ml").behind == 3 and ws.status("atlas-ml").state == "attention"
    assert ws.status("folio-site").state == "quiet"
    assert ws.status("orbit-api").todos >= 3


def test_briefing_buckets(ws):
    b = briefing(ws.all())
    assert {x["id"] for x in b["buckets"]["attention"]} == {"orbit-api", "atlas-ml"}
    assert b["projects"] == 7 and "need attention" in b["headline"]


def test_fuzzy_find(ws):
    assert ws.find("open the orbit api").id == "orbit-api"
    assert ws.find("orbit").id == "orbit-api"
    assert ws.find("ledger").id == "ledger-pay"
    assert ws.find("something unrelated") is None


def test_classify_rules():
    now = time.time()
    assert classify(Status(behind=1, last_commit_at=now), now)[0] == "attention"
    assert classify(Status(dirty=2, last_commit_at=now), now)[0] == "active"
    assert classify(Status(ahead=1, last_commit_at=now), now)[0] == "ready"
    assert classify(Status(last_commit_at=now - 30 * 86400), now)[0] == "quiet"


def test_non_repo_is_unknown(tmp_path):
    (tmp_path / "x").mkdir()
    from jarvis.projects import project_from_path, read_status

    assert read_status(project_from_path(tmp_path / "x")).state == "unknown"
