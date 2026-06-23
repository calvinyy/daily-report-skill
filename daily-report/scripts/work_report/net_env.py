"""Network environment helpers for subprocess calls.

The launchd plist injects HTTP(S)_PROXY=127.0.0.1:10080 so a headless `claude -p`
can reach Anthropic, but Feishu is domestic-direct and `claude -p` also works
direct for this user. When that proxy is dead, blindly inheriting it makes every
lark-cli call fail and `claude -p` hang to timeout (the 2026-06-03 outage). These
helpers reproduce the monolith's proven behavior:

  - lark-cli  -> ALWAYS strip proxy vars (Feishu never needs them).
  - claude -p -> probe the proxy port; alive -> inherit, dead -> strip.

Codex is left to inherit the ambient env (OpenAI may genuinely need the proxy);
if it fails, the Claude fallback's probe-then-strip handles connectivity.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable
from urllib.parse import urlparse

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def lark_env() -> dict[str, str]:
    """Ambient env minus proxy vars — safe for any domestic-direct CLI (lark-cli)."""
    return {k: v for k, v in os.environ.items() if k.upper() not in _PROXY_VARS}


def claude_env(log: Callable[[str], None] | None = None) -> dict[str, str] | None:
    """Env for `claude -p`. Returns None to inherit the ambient env (no proxy
    configured, or the proxy is alive), or a proxy-stripped env when the
    configured proxy is unreachable so claude falls back to a direct connection."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy:
        return None
    parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    host = parsed.hostname
    if not host:
        return None
    try:
        with socket.create_connection((host, parsed.port or 80), timeout=2):
            return None
    except OSError:
        if log:
            log(f"  代理 {proxy} 不可达，claude 改为直连")
        return lark_env()
