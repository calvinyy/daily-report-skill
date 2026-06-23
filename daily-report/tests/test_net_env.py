import socket

from work_report import net_env
from work_report.net_env import claude_env, lark_env


def test_lark_env_strips_proxy_vars(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10080")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:10080")
    monkeypatch.setenv("KEEP_ME", "1")

    env = lark_env()

    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "ALL_PROXY" not in env
    assert env.get("KEEP_ME") == "1"


def test_claude_env_inherits_when_no_proxy(monkeypatch):
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)

    assert claude_env() is None


def test_claude_env_inherits_when_proxy_alive(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10080")
    monkeypatch.setattr(net_env.socket, "create_connection", lambda *a, **k: _DummyConn())

    assert claude_env() is None


def test_claude_env_strips_when_proxy_dead(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10080")
    monkeypatch.setenv("KEEP_ME", "1")

    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(net_env.socket, "create_connection", refuse)
    messages: list[str] = []

    env = claude_env(log=messages.append)

    assert env is not None
    assert "HTTPS_PROXY" not in env
    assert env.get("KEEP_ME") == "1"
    assert messages and "直连" in messages[0]


class _DummyConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
