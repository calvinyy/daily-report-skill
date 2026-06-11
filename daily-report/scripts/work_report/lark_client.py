from __future__ import annotations

import json
import subprocess
from typing import Any


def run_json(command: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "_error": str(exc), "_command": command}
    raw = result.stdout.strip()
    if not raw:
        return {"ok": False, "_error": result.stderr.strip() or f"command exited {result.returncode}", "_command": command}
    try:
        data = json.loads(raw)
    except Exception:
        return {"ok": False, "_error": result.stderr.strip() or raw[:500], "_command": command, "_returncode": result.returncode}
    if not isinstance(data, dict):
        return {
            "ok": False,
            "_error": "JSON output was not an object",
            "_command": command,
            "_returncode": result.returncode,
            "_raw_json": data,
        }
    if result.returncode != 0:
        return {
            **data,
            "ok": False,
            "_error": data.get("_error") or result.stderr.strip() or f"command exited {result.returncode}",
            "_command": command,
            "_returncode": result.returncode,
        }
    return data


class LarkClient:
    def __init__(self, binary: str):
        self.binary = binary
        self._auth_status: dict[str, Any] | None = None

    def call(self, args: list[str], timeout: int = 45) -> dict[str, Any]:
        return run_json([self.binary] + args, timeout=timeout)

    def auth_status(self) -> dict[str, Any]:
        if self._auth_status is None:
            self._auth_status = self.call(["auth", "status"], timeout=15)
        return self._auth_status

    def current_user(self) -> dict[str, str]:
        status = self.auth_status()
        user_name = str(status.get("userName") or "")
        user_open_id = str(status.get("userOpenId") or "")
        if user_name and user_open_id:
            return {"name": user_name, "open_id": user_open_id}
        data = self.call(["contact", "+get-user"], timeout=20)
        user = data.get("data", {}).get("user", {}) if data.get("ok") else {}
        return {
            "name": str(user.get("name") or user.get("en_name") or user_name or "未知用户"),
            "open_id": str(user.get("open_id") or user_open_id or ""),
        }
