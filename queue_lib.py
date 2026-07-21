"""Очередь постов: media/schedule_today.json"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_PATH = ROOT / "media" / "schedule_today.json"


def load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_queue(items: list[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def cancel_all_queued() -> int:
    q = load_queue()
    n = 0
    for i in q:
        if i.get("status") == "queued":
            i["status"] = "cancelled"
            n += 1
    save_queue(q)
    return n


def cancel_item(item_id: str) -> bool:
    q = load_queue()
    ok = False
    for i in q:
        if i.get("id") == item_id and i.get("status") == "queued":
            i["status"] = "cancelled"
            ok = True
    save_queue(q)
    return ok


def publish_now(item_id: str) -> bool:
    """Поставить publish_ts = сейчас, чтобы паблишер взял сразу."""
    q = load_queue()
    ok = False
    for i in q:
        if i.get("id") == item_id and i.get("status") == "queued":
            i["publish_ts"] = time.time() - 1
            i["publish_at"] = time.strftime("%Y-%m-%dT%H:%M")
            ok = True
    save_queue(q)
    return ok


def summary() -> dict:
    q = load_queue()
    queued = [i for i in q if i.get("status") == "queued"]
    published = [i for i in q if i.get("status") == "published"]
    return {
        "total": len(q),
        "queued": len(queued),
        "published": len(published),
        "next": min(queued, key=lambda x: float(x.get("publish_ts") or 0), default=None),
        "items": q,
    }


def get_item(item_id: str) -> dict | None:
    for i in load_queue():
        if str(i.get("id")) == str(item_id):
            return i
    return None


def due_items(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out = []
    for i in load_queue():
        if i.get("status") != "queued":
            continue
        if float(i.get("publish_ts") or 0) <= now:
            out.append(i)
    out.sort(key=lambda x: float(x.get("publish_ts") or 0))
    return out


def mark_item(item_id: str, **fields) -> bool:
    q = load_queue()
    ok = False
    for i in q:
        if str(i.get("id")) == str(item_id):
            i.update(fields)
            ok = True
    if ok:
        save_queue(q)
    return ok


def format_queue_report() -> str:
    """Краткий отчёт для Telegram (HTML)."""
    import html as _html

    q = load_queue()
    if not q:
        return "📅 Очередь пуста. Файл media/schedule_today.json."

    status_emoji = {
        "queued": "⏳",
        "published": "✅",
        "cancelled": "⏭",
        "error": "❌",
    }
    lines = ["📅 <b>Очередь сегодня</b>\n"]
    now = time.time()
    for i in q:
        st = i.get("status") or "?"
        em = status_emoji.get(st, "·")
        iid = _html.escape(str(i.get("id") or "?"))
        title = _html.escape(str(i.get("title") or i.get("rubric") or "")[:60])
        when = i.get("publish_at") or ""
        mid = i.get("message_id")
        extra = ""
        if st == "queued":
            ts = float(i.get("publish_ts") or 0)
            if ts and ts > now:
                mins = int((ts - now) / 60)
                extra = f" · через ~{mins} мин" if mins > 0 else " · скоро"
            else:
                extra = " · <b>пора выкладывать</b>"
        elif st == "published" and mid:
            extra = f" · <a href=\"https://t.me/Vaggo01/{mid}\">пост</a>"
        elif st == "error":
            extra = f" · {_html.escape(str(i.get('error') or '')[:80])}"
        lines.append(f"{em} <code>{iid}</code> · {when}\n   {title}{extra}")

    s = summary()
    lines.append("")
    lines.append(
        f"Итого: ⏳ {s['queued']} · ✅ {s['published']} · всего {s['total']}"
    )
    lines.append("")
    lines.append(
        "/qnow — выложить следующий сейчас\n"
        "/qnow id — конкретный (пример /qnow ai2)\n"
        "/qskip id — отменить\n"
        "/queue — обновить список"
    )
    return "\n".join(lines)
