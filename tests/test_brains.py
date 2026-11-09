"""Brains are tested against fake CLIs that record their argv, so the contract is checked without any API spend."""

import json
import os
import stat
import sys

import pytest

from jarvis.brains import ClaudeCodeBrain, CodexBrain, CommandBrain, GeminiBrain, OfflineBrain, make_brain

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake CLIs are shebang scripts; Windows can't exec them")


def fake_cli(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(
        f"#!{sys.executable}\nimport sys, json, os\nargv = sys.argv[1:]\nopen(os.environ['ARGV_LOG'], 'w').write(json.dumps(argv))\n{body}\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


@pytest.fixture
def path_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ARGV_LOG", str(tmp_path / "argv.json"))
    return tmp_path


def argv(tmp_path):
    return json.loads((tmp_path / "argv.json").read_text())


@posix_only
def test_claude_code_is_read_only_by_default(path_env):
    fake_cli(path_env, "claude", "print(json.dumps({'result': 'all good', 'session_id': 's1'}))")
    ans = ClaudeCodeBrain().ask("hi", cwd=str(path_env))
    a = argv(path_env)
    assert ans.text == "all good" and ans.session == "s1"
    assert a[a.index("--permission-mode") + 1] == "plan" and "--disallowedTools" in a and "Bash" in a[a.index("--disallowedTools") + 1]


@posix_only
def test_claude_code_allow_edits_and_resume(path_env):
    fake_cli(path_env, "claude", "print(json.dumps({'result': 'ok', 'session_id': 's2'}))")
    ClaudeCodeBrain(allow_edits=True, model="claude-sonnet-5-5").ask("hi", session="s1")
    a = argv(path_env)
    assert a[a.index("--permission-mode") + 1] == "acceptEdits" and a[a.index("--resume") + 1] == "s1"
    assert a[a.index("--model") + 1] == "claude-sonnet-5-5"


@posix_only
def test_codex_reads_last_message_file(path_env):
    fake_cli(path_env, "codex", "open(argv[argv.index('--output-last-message') + 1], 'w').write('codex says hi')")
    ans = CodexBrain().ask("hi")
    a = argv(path_env)
    assert ans.text == "codex says hi" and a[0] == "exec" and "--sandbox" not in a  # default sandbox is read-only
    CodexBrain(allow_edits=True).ask("hi")
    assert "workspace-write" in argv(path_env)


@posix_only
def test_gemini_json_response(path_env):
    fake_cli(path_env, "gemini", "print(json.dumps({'response': 'gemini says hi'}))")
    assert GeminiBrain().ask("hi").text == "gemini says hi"
    assert "--approval-mode" not in argv(path_env)


@posix_only
def test_command_brain_never_uses_a_shell(path_env):
    fake_cli(path_env, "myagent", "print('custom: ' + argv[-1][-5:])")
    b = CommandBrain(command="myagent --msg {prompt}")
    assert b.ask("; rm -rf ~ hello").text == "custom: hello"
    assert argv(path_env)[0] == "--msg"


def test_auto_falls_back_to_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert make_brain("auto").name == "offline"


def test_offline_brain_reads_todos(tmp_path):
    (tmp_path / "a.py").write_text("# FIXME: crash on empty input\n")
    ans = OfflineBrain().ask("<context>\nActive project: x (Python) at /tmp/x. clean.\n</context>\nUser: what's left?", cwd=str(tmp_path))
    assert "a.py:1 FIXME crash on empty input" in ans.text
