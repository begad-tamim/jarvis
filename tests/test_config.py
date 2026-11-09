import pytest

from jarvis.config import Config, live_unlocked, load_config, load_dotenv, validate


def test_defaults_valid():
    validate(Config())


def test_rejects_margin_and_unknown(tmp_path):
    p = tmp_path / "j.toml"
    p.write_text("[risk]\nmax_gross_exposure_pct = 2.0\n")
    with pytest.raises(ValueError, match="margin"):
        load_config(p)
    p.write_text("[risk]\nlevrage = 3\n")
    with pytest.raises(ValueError, match="unknown"):
        load_config(p)


def test_live_lock(monkeypatch):
    monkeypatch.delenv("JARVIS_LIVE_TRADING", raising=False)
    assert not live_unlocked()
    monkeypatch.setenv("JARVIS_LIVE_TRADING", "yes")
    assert not live_unlocked()
    monkeypatch.setenv("JARVIS_LIVE_TRADING", "I_UNDERSTAND_THE_RISKS")
    assert live_unlocked()


def test_dotenv_warns_on_open_permissions(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("JARVIS_TEST_X=1\n")
    f.chmod(0o644)
    monkeypatch.delenv("JARVIS_TEST_X", raising=False)
    w = load_dotenv(f)
    assert w and "chmod 600" in w[0]


def test_http_client_refuses_unsafe_schemes():
    from jarvis.http import request_json

    for url in ("file:///etc/passwd", "http://example.com/x", "ftp://x/y"):
        with pytest.raises(ValueError):
            request_json("GET", url)
