import pytest

from jarvis import demo_workspace
from jarvis.assistant import Assistant
from jarvis.brains import Answer, Brain
from jarvis.bus import Bus
from jarvis.journal import Journal
from jarvis.projects import Registry, discover


class Recorder(Brain):
    name, label = "rec", "Recorder"

    def __init__(self):
        self.calls = []

    def ask(self, prompt, cwd=None, session=None):
        self.calls.append((prompt, cwd, session))
        return Answer("brain answer", self.label, 5, session="sess-1")


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    return Registry(discover([str(demo_workspace.build(tmp_path_factory.mktemp("ws") / "demo"))]))


@pytest.fixture
def jar(registry):
    return Assistant(registry, Recorder(), Journal(":memory:"), Bus())


def test_brief_is_local_and_free(jar):
    m = jar.ask("brief me")
    assert "need attention" in m.text and not jar.brain.calls and m.action["view"] == "home"


def test_open_project_switches_context(jar):
    m = jar.ask("open the ledger project")
    assert jar.active == "ledger-pay" and m.action == {"view": "project", "project": "ledger-pay"}
    assert "ready to push" in m.text


def test_brain_runs_in_project_folder_with_fenced_context(jar):
    jar.active = "orbit-api"
    jar.ask("what's left before I ship?")
    prompt, cwd, _ = jar.brain.calls[-1]
    assert cwd.endswith("orbit-api") and prompt.startswith("<context>") and "Active project: orbit-api" in prompt
    jar.ask("and after that?")
    assert jar.brain.calls[-1][2] == "sess-1"  # conversation continues in the same agent session


def test_notes_and_tasks(jar):
    jar.active = "nova-web"
    assert "Noted" in jar.ask("remember the CDN key expires in March").text
    assert jar.notes("nova-web")[0]["text"] == "the CDN key expires in March"
    jar.ask("add task ship the pricing page")
    assert jar.tasks("nova-web")[-1]["text"] == "ship the pricing page"
    tid = jar.tasks("nova-web")[-1]["id"]
    assert jar.toggle_task("nova-web", tid)[-1]["done"] is True


def test_every_message_is_broadcast(jar):
    q = jar.bus.subscribe()
    jar.ask("brief me", source="phone")
    kinds = [q.get_nowait()[0] for _ in range(q.qsize())]
    assert kinds.count("message") == 2


def test_markets_off_and_call_not_configured(jar):
    assert "markets module is off" in jar.ask("markets").text
    assert "Twilio" in jar.ask("call me").text
