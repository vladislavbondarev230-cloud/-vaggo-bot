"""
Достаём токен Grok Super / Grok Build из локальной сессии (~/.grok/auth.json).
Это OIDC access token после `grok login` — не путать с ключом console.x.ai `xai-...`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def grok_homes() -> list[Path]:
    homes: list[Path] = []
    env = (os.environ.get("GROK_HOME") or "").strip()
    if env:
        homes.append(Path(env))
    homes.append(Path(r"D:\UserData\.grok"))
    homes.append(Path.home() / ".grok")
    # unique existing
    out: list[Path] = []
    seen: set[str] = set()
    for h in homes:
        try:
            key = str(h.resolve()) if h.exists() else str(h)
        except OSError:
            key = str(h)
        if key in seen:
            continue
        seen.add(key)
        if h.is_dir():
            out.append(h)
    return out


def load_session() -> dict | None:
    """Вернуть запись сессии: token, email, expires_at, path."""
    for home in grok_homes():
        path = home / "auth.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for _k, entry in data.items():
            if not isinstance(entry, dict):
                continue
            tok = (entry.get("key") or entry.get("access_token") or "").strip()
            if not tok:
                continue
            return {
                "token": tok,
                "email": entry.get("email") or "",
                "expires_at": entry.get("expires_at") or "",
                "auth_mode": entry.get("auth_mode") or "",
                "path": str(path),
                "user_id": entry.get("user_id") or entry.get("principal_id") or "",
            }
    return None


def session_token() -> str:
    s = load_session()
    return (s or {}).get("token") or ""


def session_info() -> dict:
    s = load_session()
    if not s:
        return {"ok": False, "reason": "нет ~/.grok/auth.json — зайди: grok login"}
    # expires_at ISO
    exp = s.get("expires_at") or ""
    expired = False
    try:
        # 2026-07-15T13:28:46...
        from datetime import datetime

        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        # naive compare via timestamp
        expired = exp_dt.timestamp() < time.time() - 30
    except Exception:
        expired = False
    return {
        "ok": True,
        "email": s.get("email"),
        "expires_at": exp,
        "expired": expired,
        "auth_mode": s.get("auth_mode"),
        "path": s.get("path"),
        "token_len": len(s.get("token") or ""),
    }
