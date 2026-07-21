# -*- coding: utf-8 -*-
"""
Заказы: приём заявки → фиксированная цена → исполнение → сдача.

ВАЖНО: заказ — независимый продукт клиента.
  Не встраивать в Вагго / @DirectorVaggobot / канал.
  Боты → client_bots/<id>/ (свой token, свой процесс).
  Клиенту — готовый результат, не «наш сервис».

Хранилище: media/orders.json (не state.json — polling не затирает).
Бесплатных слотов нет — все заказы платные (прайс ниже).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from state import new_id

ROOT = Path(__file__).resolve().parent
ORDERS_PATH = ROOT / "media" / "orders.json"
_LOCK = threading.Lock()

# Free-слоты отключены. Цена = услуга → фикс. сумма (для банка/Platega).
FREE_LIMIT = 0
PRICE_MAX = 500
PRICE_MIN = 100

# Услуга → цена. Всё. Без «оценок» и плавающих сумм.
ORDER_TYPES: dict[str, dict[str, Any]] = {
    "design": {
        "title": "Дизайн / обложки",
        "price": 100,
        "hint": "аватар, обложка, 1–3 картинки",
        "includes": "макеты PNG/JPG по ТЗ",
        "not_includes": "хостинг, печать, брендбук",
    },
    "script": {
        "title": "Скрипт / автоматизация",
        "price": 150,
        "hint": "парсер, утилита, мелкий скрипт",
        "includes": "скрипт + короткая инструкция",
        "not_includes": "сервер, платные API",
    },
    "bot": {
        "title": "Telegram-бот",
        "price": 200,
        "hint": "меню, логика, простая админка",
        "includes": "код бота + README",
        "not_includes": "хостинг, VPS",
    },
    "site": {
        "title": "Сайт / лендинг",
        "price": 200,
        "hint": "1–5 страниц, HTML/простая сборка",
        "includes": "вёрстка/исходники по ТЗ",
        "not_includes": "домен, хостинг, SEO",
    },
    "app": {
        "title": "Приложение (MVP)",
        "price": 300,
        "hint": "базовый MVP по ТЗ",
        "includes": "функционал по ТЗ",
        "not_includes": "сторы, сервер 24/7",
    },
    "other": {
        "title": "Другое",
        "price": 200,
        "hint": "задача вне списка",
        "includes": "объём по ТЗ",
        "not_includes": "хостинг, домен, реклама",
    },
}

STATUS_LABELS = {
    "new": "🆕 новый — ждём принятия",
    "accepted": "✅ принят",
    "in_progress": "🛠 в работе",
    "done": "✔️ готов",
    "cancelled": "❌ отменён",
}

# Опрос ТЗ: после «что сделать» — цвета, функционал, пример…
# 4 шага — коротко; Grok допишет остальное. Пропуск = «на усмотрение».
TZ_STEPS: list[dict[str, Any]] = [
    {
        "id": "what",
        "key": "what",
        "min": 1,
        "title": "Что сделать",
        "ask": (
            "✍️ <b>1/4 · Что сделать?</b>\n\n"
            "Хоть 2 слова: «бот для кафе», «лендинг».\n"
            "<i>Grok допишет ТЗ сам.</i>"
        ),
        "skip": "бот/сайт по ТЗ",
    },
    {
        "id": "audience",
        "key": "audience",
        "min": 1,
        "title": "Для кого",
        "ask": (
            "👥 <b>2/4 · Для кого?</b>\n\n"
            "Клиенты / гости / админ…\n"
            "Или жми «Пропустить»."
        ),
        "skip": "на усмотрение",
    },
    {
        "id": "features",
        "key": "features",
        "min": 1,
        "title": "Что важно",
        "ask": (
            "⚙️ <b>3/4 · Что обязательно?</b>\n\n"
            "Кнопки, меню, форма… или «как обычно»."
        ),
        "skip": "базовый набор",
    },
    {
        "id": "deadline",
        "key": "deadline",
        "min": 1,
        "title": "Срок",
        "ask": (
            "⏱ <b>4/4 · Срок?</b>\n\n"
            "«2 дня» / «не горит»."
        ),
        "skip": "не горит",
    },
]


def order_step_keyboard() -> dict:
    """На каждом шаге ТЗ — пропуск / отмена / меню."""
    return {
        "inline_keyboard": [
            [{"text": "⏭ Пропустить", "callback_data": "ord:skip"}],
            [
                {"text": "❌ Отмена", "callback_data": "ord:cancel"},
                {"text": "🏠 Меню", "callback_data": "menu:userhome"},
            ],
        ]
    }


def is_tz_too_vague(text: str) -> tuple[bool, str]:
    """
    Устарело для опроса: Grok дополняет ТЗ.
    Оставляем только «совсем пусто» на случай внешних вызовов.
    """
    t = (text or "").strip()
    if not t:
        return True, "Пусто. Напиши хоть пару слов."
    return False, ""


def tz_step_index(step_id: str) -> int:
    for i, s in enumerate(TZ_STEPS):
        if s["id"] == step_id:
            return i
    return 0


def tz_step(step_id: str) -> dict[str, Any]:
    for s in TZ_STEPS:
        if s["id"] == step_id:
            return s
    return TZ_STEPS[0]


def next_tz_step(step_id: str) -> dict[str, Any] | None:
    i = tz_step_index(step_id)
    if i + 1 < len(TZ_STEPS):
        return TZ_STEPS[i + 1]
    return None


def build_brief_from_answers(kind: str, answers: dict) -> str:
    """Собрать ТЗ из ответов (короткий опрос 4 шага + Grok-дополнения)."""
    meta = ORDER_TYPES.get(kind) or ORDER_TYPES["other"]
    lines = [
        f"Тип: {meta.get('title') or kind}",
        f"Что сделать: {(answers.get('what') or 'по типу услуги').strip()}",
        f"Для кого: {(answers.get('audience') or 'на усмотрение').strip()}",
        f"Важно: {(answers.get('features') or 'базовый набор').strip()}",
        f"Срок: {(answers.get('deadline') or 'не горит').strip()}",
    ]
    # legacy keys if present
    if (answers.get("colors") or "").strip():
        lines.append(f"Стиль: {answers.get('colors')}")
    if (answers.get("example") or "").strip():
        lines.append(f"Пример: {answers.get('example')}")
    if (answers.get("ai_clarify") or "").strip():
        lines.append(f"Уточнения: {(answers.get('ai_clarify') or '').strip()}")
    if (answers.get("ai_notes") or "").strip():
        lines.append(f"Grok: {(answers.get('ai_notes') or '').strip()}")
    return "\n".join(lines)


def _extract_json_obj(text: str) -> dict:
    """Достать JSON-объект из ответа модели."""
    t = (text or "").strip()
    if not t:
        return {}
    # fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL | re.IGNORECASE)
    if m:
        t = m.group(1)
    else:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            t = t[a : b + 1]
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def review_tz_with_ai(
    cfg: dict | None,
    kind: str,
    answers: dict,
    *,
    extra_client_note: str = "",
) -> dict[str, Any]:
    """
    После опроса: Grok собирает единое ТЗ, может добавить/уточнить,
    проверяет законность и реальность выполнения в рамках фикс-тарифа.

    Возвращает:
      brief, summary, additions, questions (list[str]),
      legal_ok, legal_reason, feasible, feasible_reason,
      risk (ok|warn|block), engine (grok|fallback)
    """
    raw_brief = build_brief_from_answers(kind, answers)
    meta = ORDER_TYPES.get(kind) or ORDER_TYPES["other"]
    price = int(meta.get("price") or PRICE_MIN)
    title = str(meta.get("title") or kind)
    includes = str(meta.get("includes") or "")
    not_includes = str(meta.get("not_includes") or "")

    # rule-based legal first (быстро, без AI)
    legal_hits: list[str] = []
    legal_reason = ""
    legal_ok = True
    try:
        import moderation_lib as mod

        illegal, reason, hits = mod.check_tz(raw_brief + "\n" + (extra_client_note or ""))
        if illegal:
            legal_ok = False
            legal_reason = reason
            legal_hits = list(hits or [])
    except Exception:
        pass

    fallback = {
        "brief": raw_brief,
        "summary": "Сводка по твоим ответам (без AI — мозг недоступен).",
        "additions": "",
        "questions": [],
        "legal_ok": legal_ok,
        "legal_reason": legal_reason or ("ок" if legal_ok else "подозрение на запрещённое"),
        "feasible": True,
        "feasible_reason": f"Фикс-тариф {price} ₽ · объём в рамках «{title}».",
        "risk": "block" if not legal_ok else "ok",
        "engine": "fallback",
        "legal_hits": legal_hits,
    }
    if not legal_ok:
        return fallback

    system = (
        "Ты менеджер заказов студии «Вагго». Клиент прошёл опрос ТЗ.\n"
        "Собери единое чистое ТЗ, проверь законность и реалистичность.\n"
        "Ответ — ТОЛЬКО JSON без markdown-ограждений.\n"
        "Схема:\n"
        "{\n"
        '  "brief": "полное ТЗ 800-2000 знаков, структурировано",\n'
        '  "summary": "2-4 предложения: что понял",\n'
        '  "additions": "что добавил от себя как разумные дефолты (или пусто)",\n'
        '  "questions": ["уточнение1", "..."]  // 0-3 шт, только если критично,\n'
        '  "legal_ok": true/false,\n'
        '  "legal_reason": "почему",\n'
        '  "feasible": true/false,\n'
        '  "feasible_reason": "влезает ли в тариф/срок",\n'
        '  "risk": "ok" | "warn" | "block"\n'
        "}\n"
        "block = незаконно / мошенничество / вред / обход закона.\n"
        "warn = слишком большой объём на тариф, неясность, но можно взять с оговорками.\n"
        "ok = можно брать.\n"
        "Не выдумывай факты о клиенте. Не предлагай взлом, скам, malware.\n"
        "Цена фиксирована — не меняй сумму, только оцени объём.\n"
        "legal_ok=false / risk=block ТОЛЬКО при явной незаконности "
        "(взлом, скам, malware, насилие, CSAM, наркотики, пробив…).\n"
        "Обычный сайт/бот/дизайн/скрипт для бизнеса — legal_ok=true.\n"
        "Если объём велик на тариф — feasible=false, risk=warn (не block)."
    )
    user = (
        f"Услуга: {title} ({kind})\n"
        f"Фикс-цена: {price} ₽\n"
        f"Входит: {includes}\n"
        f"Не входит: {not_includes}\n\n"
        f"Ответы опроса:\n{raw_brief}\n"
    )
    if (extra_client_note or "").strip():
        user += f"\nДоп. ответ клиента на уточнения:\n{extra_client_note.strip()}\n"

    try:
        from content import grok_chat

        cfg = cfg or {}
        raw = grok_chat(
            cfg,
            system,
            user,
            model=(cfg.get("grok_order_model") or cfg.get("grok_fast_model") or "grok-4.3"),
            temperature=0.3,
            tools=False,  # без search — быстрее, не жрёт лишний лимит
            max_tokens=700,
        )
        data = _extract_json_obj(raw)
        if not data:
            fallback["summary"] = "AI ответил без JSON — оставил сырое ТЗ."
            fallback["engine"] = "fallback-parse"
            return fallback

        brief = str(data.get("brief") or raw_brief).strip() or raw_brief
        questions = data.get("questions") or []
        if not isinstance(questions, list):
            questions = []
        questions = [str(q).strip() for q in questions if str(q).strip()][:3]

        legal_ok_ai = bool(data.get("legal_ok", True))
        risk = str(data.get("risk") or "ok").lower().strip()
        if risk not in ("ok", "warn", "block"):
            risk = "ok" if legal_ok_ai else "warn"

        # повторная rule-check — только правила банят жёстко
        rule_illegal = False
        try:
            import moderation_lib as mod

            illegal2, reason2, hits2 = mod.check_tz(brief)
            if illegal2:
                rule_illegal = True
                legal_ok_ai = False
                risk = "block"
                data["legal_reason"] = reason2
                legal_hits = list(hits2 or [])
        except Exception:
            pass

        # AI «незаконно», но rule-check чист → не баним, только warn
        if not legal_ok_ai and not rule_illegal:
            legal_ok_ai = True
            risk = "warn"
            data["legal_reason"] = (
                "AI насторожился, автофильтр чист. "
                + str(data.get("legal_reason") or "")
            )[:400]

        feasible = bool(data.get("feasible", True))
        # «не влезает в тариф» — warn, не block
        if not feasible and risk == "block" and not rule_illegal:
            risk = "warn"
        if risk == "block" and rule_illegal:
            feasible = False

        return {
            "brief": brief[:4000],
            "summary": str(data.get("summary") or "")[:800],
            "additions": str(data.get("additions") or "")[:800],
            "questions": questions,
            "legal_ok": legal_ok_ai,
            "legal_reason": str(data.get("legal_reason") or ("ок" if legal_ok_ai else "запрещено"))[
                :400
            ],
            "feasible": feasible,
            "feasible_reason": str(data.get("feasible_reason") or "")[:400],
            "risk": risk,
            "engine": "grok",
            "legal_hits": legal_hits,
        }
    except Exception as e:
        print("review_tz_with_ai fail", type(e).__name__, str(e)[:160], flush=True)
        fallback["summary"] = f"AI недоступен ({type(e).__name__}) — сырое ТЗ."
        return fallback


def validate_step_answer(step: dict, text: str) -> tuple[bool, str]:
    """ok, error_message. Минимумы сняты — Grok допишет ТЗ сам."""
    t = (text or "").strip()
    if not t:
        return False, "Пусто. Напиши хоть слово или «нет» / «на твоё усмотрение»."
    # любые 1+ символы ок (эмодзи тоже)
    return True, ""


def _default() -> dict:
    return {"items": {}, "free_used": 0, "updated_at": 0}


def _migrate_from_state() -> dict:
    """Если в state.json остались заказы — перенести."""
    try:
        from state import load_state

        st = load_state()
        o = st.get("orders")
        if isinstance(o, dict) and (o.get("items") or o.get("free_used")):
            return {
                "items": dict(o.get("items") or {}),
                "free_used": int(o.get("free_used") or 0),
                "updated_at": int(time.time()),
            }
    except Exception:
        pass
    return _default()


def load_orders() -> dict:
    with _LOCK:
        if not ORDERS_PATH.exists():
            data = _migrate_from_state()
            ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
            ORDERS_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return data
        try:
            data = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return _default()
            data.setdefault("items", {})
            data.setdefault("free_used", 0)
            if not isinstance(data["items"], dict):
                data["items"] = {}
            return data
        except Exception:
            return _default()


def save_orders(data: dict) -> None:
    """Только media/orders.json — не через state.json (polling не затрёт)."""
    with _LOCK:
        data["updated_at"] = int(time.time())
        ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = ORDERS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(ORDERS_PATH)


def free_left(state: dict | None = None) -> int:
    """Бесплатных слотов больше нет."""
    return 0


def price_of(kind: str) -> int:
    meta = ORDER_TYPES.get(kind) or ORDER_TYPES["other"]
    return int(meta.get("price") or PRICE_MIN)


def price_catalog_lines() -> list[str]:
    """Только «услуга — цена» (как надо банку)."""
    lines = []
    for _k, meta in ORDER_TYPES.items():
        p = int(meta.get("price") or 0)
        lines.append(f"• {meta['title']} — <b>{p} ₽</b>")
    return lines


def estimate(kind: str, brief: str) -> dict:
    """Цена строго из прайса услуги. brief не меняет сумму."""
    meta = ORDER_TYPES.get(kind) or ORDER_TYPES["other"]
    price = int(meta.get("price") or PRICE_MIN)
    return {
        "complexity": 1,
        "complexity_label": "фикс. тариф",
        "price": price,
        "price_min": price,
        "price_max": price,
        "title": meta["title"],
        "hint": meta.get("hint") or "",
        "includes": meta.get("includes") or "",
        "not_includes": meta.get("not_includes") or "",
    }


def create_order(
    *,
    user_id: int,
    username: str = "",
    name: str = "",
    kind: str,
    brief: str,
) -> dict:
    data = load_orders()
    items = data.setdefault("items", {})
    est = estimate(kind, brief)
    oid = new_id()
    item = {
        "id": oid,
        "user_id": int(user_id),
        "username": (username or "").lstrip("@"),
        "name": name or username or str(user_id),
        "kind": kind if kind in ORDER_TYPES else "other",
        "brief": (brief or "").strip()[:2000],
        "complexity": est["complexity"],
        "complexity_label": est["complexity_label"],
        "price": int(est["price"]),
        "price_list": int(est["price"]),
        "is_free": False,
        "free_slot": None,
        "status": "new",
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "deliver_note": "",
        "result_file_id": None,
    }
    items[oid] = item
    save_orders(data)
    return item


def get_order(oid: str) -> dict | None:
    return (load_orders().get("items") or {}).get(str(oid))


def save_order(item: dict) -> dict:
    data = load_orders()
    item["updated_at"] = int(time.time())
    data.setdefault("items", {})[str(item["id"])] = item
    save_orders(data)
    return item


def delete_order(oid: str, *, restore_free: bool = True) -> dict | None:
    """
    Удалить заказ из media/orders.json.
    Если free и restore_free — вернуть free_used (не ниже 0).
    """
    data = load_orders()
    items = data.setdefault("items", {})
    key = str(oid)
    item = items.pop(key, None)
    if not item:
        return None
    if restore_free and item.get("is_free"):
        # free_slot считался — откатываем счётчик, если заказ ещё не «съел» слот бесполезно
        data["free_used"] = max(0, int(data.get("free_used") or 0) - 1)
        # free_used не ниже числа оставшихся free-заказов
        free_n = sum(1 for x in items.values() if x.get("is_free"))
        data["free_used"] = max(int(data["free_used"]), free_n)
    save_orders(data)
    return item


def delete_orders_by_status(status: str, *, restore_free: bool = True) -> int:
    """Удалить все заказы со статусом. Возвращает число удалённых."""
    data = load_orders()
    items = data.setdefault("items", {})
    to_del = [
        (k, v)
        for k, v in list(items.items())
        if str(v.get("status") or "") == str(status)
    ]
    free_restore = 0
    for k, v in to_del:
        items.pop(k, None)
        if restore_free and v.get("is_free"):
            free_restore += 1
    if free_restore:
        data["free_used"] = max(0, int(data.get("free_used") or 0) - free_restore)
        free_n = sum(1 for x in items.values() if x.get("is_free"))
        data["free_used"] = max(int(data["free_used"]), free_n)
    save_orders(data)
    return len(to_del)


def list_orders(*, status: str | None = None, limit: int = 30) -> list[dict]:
    items = list((load_orders().get("items") or {}).values())
    if status:
        items = [x for x in items if x.get("status") == status]
    items.sort(key=lambda x: int(x.get("created_at") or 0), reverse=True)
    return items[:limit]


def user_pending_order(user_id: int) -> dict | None:
    for x in list_orders(limit=100):
        if int(x.get("user_id") or 0) != int(user_id):
            continue
        if x.get("status") in ("done", "cancelled"):
            continue
        return x
    return None


def list_user_orders(user_id: int, *, limit: int = 20) -> list[dict]:
    out = [
        x for x in list_orders(limit=100) if int(x.get("user_id") or 0) == int(user_id)
    ]
    return out[:limit]


def status_label(status: str) -> str:
    return STATUS_LABELS.get(str(status or ""), str(status or "—"))


def format_user_history(user_id: int) -> str:
    import html as H

    items = list_user_orders(user_id)
    head = "📦 <b>Мои заказы</b>\n\n"
    if not items:
        return head + "Пока пусто. Жми /order — прайс и оформление."
    lines = [head]
    for it in items:
        kind = ORDER_TYPES.get(it.get("kind") or "", {}).get("title") or it.get("kind")
        price = f"{it.get('price')} ₽"
        st = status_label(str(it.get("status") or ""))
        lines.append(
            f"• <code>{H.escape(str(it.get('id')))}</code>\n"
            f"  {H.escape(str(kind))} · {price}\n"
            f"  {st}\n"
            f"  <i>{H.escape((it.get('brief') or '')[:80])}</i>\n"
        )
    lines.append("Обновить: /myorders · Новый: /order · Прайс: /prices")
    return "\n".join(lines)


def order_keyboard_types() -> dict:
    """Кнопки: иконка · услуга · цена (Vaggo 1.0)."""
    icons = {
        "design": "🎨",
        "script": "⚙️",
        "bot": "🤖",
        "site": "🌐",
        "app": "📱",
        "other": "✨",
    }
    rows = [[{"text": "🛠 Выбери услугу", "callback_data": "ord:noop"}]]
    for k, meta in ORDER_TYPES.items():
        p = int(meta.get("price") or 0)
        title = str(meta.get("title") or k)
        short = title if len(title) < 22 else title[:20] + "…"
        ic = icons.get(k, "•")
        rows.append(
            [{"text": f"{ic} {short} · {p} ₽", "callback_data": f"ord:type:{k}"}]
        )
    rows.append(
        [
            {"text": "💰 Прайс", "callback_data": "legal:prices"},
            {"text": "📦 Мои", "callback_data": "ord:mine"},
            {"text": "💳 Баланс", "callback_data": "bal:show"},
        ]
    )
    rows.append(
        [
            {"text": "🏠 Меню", "callback_data": "menu:userhome"},
            {"text": "❌ Отмена", "callback_data": "ord:cancel"},
        ]
    )
    return {"inline_keyboard": rows}


def user_order_actions_keyboard(oid: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔄 Статус", "callback_data": f"ord:status:{oid}"}],
            [
                {"text": "📦 Все заказы", "callback_data": "ord:mine"},
                {"text": "🛠 Новый", "callback_data": "ord:restart"},
            ],
            [
                {"text": "💳 Баланс", "callback_data": "bal:show"},
                {"text": "🏠 Меню", "callback_data": "menu:userhome"},
            ],
        ]
    }


def owner_order_keyboard(oid: str) -> dict:
    oid = str(oid)
    return {
        "inline_keyboard": [
            [
                {"text": "✅ В работу", "callback_data": f"ord:w:{oid}"},
                {"text": "✔️ Готово", "callback_data": f"ord:d:{oid}"},
            ],
            [{"text": "❌ Отменить заказ", "callback_data": f"ord:x:{oid}"}],
        ]
    }


def format_order_card(item: dict, *, for_owner: bool = False) -> str:
    import html as H

    kind = ORDER_TYPES.get(item.get("kind") or "", {}).get("title") or item.get("kind")
    price = f"{item.get('price')} ₽"
    lines = [
        f"🛠 <b>Заказ</b> <code>{H.escape(str(item.get('id')))}</code>",
        f"тип: <b>{H.escape(str(kind))}</b>",
        f"цена: <b>{price}</b> (фикс. тариф)",
        f"статус: {status_label(str(item.get('status') or ''))}",
        "",
        f"<b>ТЗ:</b>\n{H.escape((item.get('brief') or '')[:900])}",
        "",
        "⚠️ <i>Хостинг / VPS / домен — не входят. Даёшь доступы сам.</i>",
        "🛡 <i>Гарантия 2 сут. · правки 1 сут. → /terms · /prices</i>",
    ]
    if for_owner:
        un = item.get("username")
        who = f"@{un}" if un else item.get("name")
        lines.insert(1, f"от: {H.escape(str(who))} · <code>{item.get('user_id')}</code>")
    return "\n".join(lines)


def orders_path() -> str:
    return str(ORDERS_PATH)
