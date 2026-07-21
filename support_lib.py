# -*- coding: utf-8 -*-
"""
Тикеты поддержки (для Platega/банка — не группа, а система обращений).
Хранение: media/support_tickets.json
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from state import new_id

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "media" / "support_tickets.json"
_LOCK = threading.Lock()

STATUS = {
    "open": "🟢 открыт",
    "answered": "💬 есть ответ",
    "closed": "⚫ закрыт",
}


def _default() -> dict:
    return {"items": {}, "updated_at": 0}


def load() -> dict:
    with _LOCK:
        if not PATH.exists():
            data = _default()
            PATH.parent.mkdir(parents=True, exist_ok=True)
            PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return data
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _default()
            data.setdefault("items", {})
            return data
        except Exception:
            return _default()


def save(data: dict) -> None:
    with _LOCK:
        data["updated_at"] = int(time.time())
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PATH)


def create_ticket(
    user_id: int,
    text: str,
    *,
    username: str = "",
    name: str = "",
) -> dict:
    data = load()
    tid = new_id()[:8]
    now = int(time.time())
    item = {
        "id": tid,
        "user_id": int(user_id),
        "username": (username or "").lstrip("@"),
        "name": name or "",
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "messages": [
            {
                "from": "user",
                "text": (text or "").strip()[:3500],
                "ts": now,
            }
        ],
    }
    data["items"][tid] = item
    save(data)
    return item


def get_ticket(tid: str) -> dict | None:
    data = load()
    it = (data.get("items") or {}).get(str(tid))
    return it if isinstance(it, dict) else None


def add_message(tid: str, *, from_role: str, text: str) -> dict | None:
    data = load()
    it = (data.get("items") or {}).get(str(tid))
    if not isinstance(it, dict):
        return None
    if it.get("status") == "closed":
        return None
    now = int(time.time())
    it.setdefault("messages", []).append(
        {
            "from": from_role,  # user | staff
            "text": (text or "").strip()[:3500],
            "ts": now,
        }
    )
    it["updated_at"] = now
    if from_role == "staff":
        it["status"] = "answered"
    elif from_role == "user" and it.get("status") == "answered":
        it["status"] = "open"
    data["items"][str(tid)] = it
    save(data)
    return it


def close_ticket(tid: str) -> dict | None:
    data = load()
    it = (data.get("items") or {}).get(str(tid))
    if not isinstance(it, dict):
        return None
    it["status"] = "closed"
    it["updated_at"] = int(time.time())
    data["items"][str(tid)] = it
    save(data)
    return it


def reopen_ticket(tid: str) -> dict | None:
    data = load()
    it = (data.get("items") or {}).get(str(tid))
    if not isinstance(it, dict):
        return None
    it["status"] = "open"
    it["updated_at"] = int(time.time())
    data["items"][str(tid)] = it
    save(data)
    return it


def list_user_tickets(user_id: int, *, limit: int = 10) -> list[dict]:
    data = load()
    items = [
        it
        for it in (data.get("items") or {}).values()
        if isinstance(it, dict) and int(it.get("user_id") or 0) == int(user_id)
    ]
    items.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return items[:limit]


def list_open_tickets(*, limit: int = 30) -> list[dict]:
    data = load()
    items = [
        it
        for it in (data.get("items") or {}).values()
        if isinstance(it, dict) and it.get("status") in ("open", "answered")
    ]
    items.sort(key=lambda x: int(x.get("updated_at") or 0), reverse=True)
    return items[:limit]


def open_ticket_for_user(user_id: int) -> dict | None:
    """Один «живой» тикет на пользователя (удобно писать без кнопок)."""
    for it in list_user_tickets(user_id, limit=20):
        if it.get("status") in ("open", "answered"):
            return it
    return None


def format_ticket_card(it: dict, *, for_staff: bool = False) -> str:
    tid = it.get("id")
    st = STATUS.get(str(it.get("status")), it.get("status"))
    uname = it.get("username") or "—"
    name = it.get("name") or ""
    msgs = it.get("messages") or []
    last = (msgs[-1].get("text") or "")[:400] if msgs else "—"
    head = f"🎫 <b>Тикет</b> <code>{tid}</code> · {st}\n"
    if for_staff:
        head += f"От: {name} (@{uname}) · id <code>{it.get('user_id')}</code>\n"
    head += f"Сообщений: <b>{len(msgs)}</b>\n\n"
    head += f"<b>Последнее:</b>\n{last}"
    return head


def user_ticket_list_html(user_id: int) -> str:
    items = list_user_tickets(user_id, limit=8)
    if not items:
        return (
            "📋 <b>Мои обращения</b>\n\n"
            "Пока пусто. Нажми «Новый тикет» и опиши вопрос."
        )
    lines = ["📋 <b>Мои обращения</b>\n"]
    for it in items:
        st = STATUS.get(str(it.get("status")), "?")
        n = len(it.get("messages") or [])
        preview = ""
        if it.get("messages"):
            preview = (it["messages"][0].get("text") or "")[:60]
        lines.append(
            f"• <code>{it.get('id')}</code> {st} · {n} сообщ.\n"
            f"  <i>{preview}</i>"
        )
    lines.append("\nОткрытый тикет: просто напиши сюда — сообщение добавится.")
    return "\n".join(lines)


def staff_list_html() -> str:
    items = list_open_tickets(limit=15)
    if not items:
        return "🎫 <b>Открытых тикетов нет</b>"
    lines = ["🎫 <b>Открытые тикеты</b>\n"]
    for it in items:
        st = STATUS.get(str(it.get("status")), "?")
        un = it.get("username") or it.get("user_id")
        last = ""
        if it.get("messages"):
            last = (it["messages"][-1].get("text") or "")[:50]
        lines.append(
            f"• <code>{it.get('id')}</code> {st} · @{un}\n  {last}"
        )
    lines.append("\nОтветить: кнопка под уведомлением или /treply КОД текст")
    return "\n".join(lines)


def support_home_html() -> str:
    return (
        "🆘 <b>Поддержка · 1.0</b>\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "Пиши <b>тикетом в боте</b> — так удобнее (не группа).\n\n"
        "1) «Новый тикет» → вопрос\n"
        "2) Получишь номер\n"
        "3) Ответ придёт сюда\n"
        "4) Пока открыт — просто пиши дальше\n\n"
        "Обычно в течение суток."
    )


def support_keyboard(*, has_open: bool = False) -> dict:
    rows = [
        [{"text": "✉️ Новый тикет", "callback_data": "sup:new"}],
        [{"text": "📋 Мои обращения", "callback_data": "sup:mine"}],
    ]
    if has_open:
        rows.append(
            [{"text": "✍️ Дописать в открытый", "callback_data": "sup:continue"}]
        )
    rows.append(
        [
            {"text": "🔒 Политика", "url": "https://telegra.ph/Politika-konfidencialnosti-06-21-31"},
            {"text": "📜 Соглашение", "url": "https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"},
        ]
    )
    rows.append(
        [
            {"text": "💰 Прайс", "callback_data": "legal:prices"},
            {"text": "🏠 Меню", "callback_data": "menu:userhome"},
        ]
    )
    return {"inline_keyboard": rows}


def ticket_user_keyboard(tid: str, *, closed: bool = False) -> dict:
    if closed:
        return {
            "inline_keyboard": [
                [{"text": "✉️ Новый тикет", "callback_data": "sup:new"}],
                [{"text": "🆘 Поддержка", "callback_data": "sup:home"}],
            ]
        }
    return {
        "inline_keyboard": [
            [{"text": "✍️ Дописать", "callback_data": f"sup:write:{tid}"}],
            [{"text": "✅ Закрыть тикет", "callback_data": f"sup:uclose:{tid}"}],
            [{"text": "🆘 Поддержка", "callback_data": "sup:home"}],
        ]
    }


def ticket_staff_keyboard(tid: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "💬 Ответить", "callback_data": f"sup:reply:{tid}"},
                {"text": "✅ Закрыть", "callback_data": f"sup:close:{tid}"},
            ],
            [{"text": "📋 Все открытые", "callback_data": "sup:stafflist"}],
        ]
    }
