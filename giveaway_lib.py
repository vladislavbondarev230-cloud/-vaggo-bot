"""
Розыгрыши Вагго — квест:
1) подписки (проверяем API)
2) репост посту другу + скрин (Grok сам проверяет скрин)
В розыгрыш идут только complete=True.
Инвайт-ссылки друзей — опционально (require_invites, по умолчанию 0).

Хранилище: media/giveaways.json (как orders) — не затирается polling offset.
Дубль в state.json для совместимости / merge.
"""
from __future__ import annotations

import html
import json
import random
import threading
import time
from pathlib import Path
from typing import Any

from state import load_state, new_id, save_state

ROOT = Path(__file__).resolve().parent
GW_PATH = ROOT / "media" / "giveaways.json"
# аварийный бэкап с GitHub / ПК — подмешивается при старте
RESTORE_PATH = ROOT / "giveaway_restore.json"
_LOCK = threading.Lock()

DEFAULT_MARKER = "🎯"
DEFAULT_HOURS = 72
MIN_CHARS = 12
BTN_JOIN = "🎁 Участвовать"
# авто-итог только при N complete; иначе дедлайн сдвигается
DEFAULT_MIN_COMPLETE = 10
DEFAULT_EXTEND_HOURS = 24
MAX_EXTEND_COUNT = 60  # ~2 мес. по 24ч


def _empty_store() -> dict:
    return {"items": {}, "active_id": None}


def apply_restore_seed(*, force: bool = False) -> dict[str, Any]:
    """
    Восстановить участников из giveaway_restore.json (деплой с ПК).
    force=True — слить даже если уже есть active.
    Возвращает {ok, merged_items, active_id, complete, message}.
    """
    if not RESTORE_PATH.is_file():
        return {"ok": False, "message": "нет giveaway_restore.json"}
    try:
        raw = json.loads(RESTORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "message": f"restore read: {e}"}
    if not isinstance(raw, dict):
        return {"ok": False, "message": "restore не dict"}
    seed_items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    if not seed_items:
        return {"ok": False, "message": "restore пустой"}

    with _LOCK:
        cur = _read_file_store()
        cur_n = sum(
            len((it.get("entries") or {}))
            for it in (cur.get("items") or {}).values()
        )
        seed_n = sum(
            len((it.get("entries") or {}))
            for it in seed_items.values()
        )
        def _pick_active(store: dict) -> dict | None:
            items = store.get("items") or {}
            aid = store.get("active_id")
            if aid and items.get(str(aid)):
                it = items[str(aid)]
                if it.get("status") == "active":
                    return it
            for it in items.values():
                if it.get("status") == "active":
                    return it
            return None

        def _count_complete(it: dict | None) -> tuple[int, int]:
            if not it:
                return 0, 0
            ents = list((it.get("entries") or {}).values())
            all_n = sum(1 for e in ents if not e.get("excluded"))
            ok_n = sum(1 for e in ents if e.get("complete") and not e.get("excluded"))
            return ok_n, all_n

        # не затирать облако, если там уже больше людей (кроме force)
        if not force and cur_n > seed_n:
            act0 = _pick_active(cur)
            ok0, all0 = _count_complete(act0)
            return {
                "ok": True,
                "skipped": True,
                "message": f"на сервере уже {cur_n} записей ≥ seed {seed_n}",
                "active_id": (act0 or {}).get("id") or cur.get("active_id"),
                "complete": ok0,
                "started": all0,
                "prize": (act0 or {}).get("prize"),
                "channel_message_id": (act0 or {}).get("channel_message_id"),
            }
        merged = _merge_stores(
            cur, {"items": seed_items, "active_id": raw.get("active_id")}
        )
        # seed active wins if status active
        aid = raw.get("active_id")
        if aid and str(aid) in (merged.get("items") or {}):
            if merged["items"][str(aid)].get("status") == "active":
                merged["active_id"] = str(aid)
        try:
            _write_file_store(merged)
        except Exception as e:
            return {"ok": False, "message": f"write: {e}"}
        try:
            st = load_state()
            st["giveaways"] = {
                "items": merged.get("items") or {},
                "active_id": merged.get("active_id"),
            }
            save_state(st)
        except Exception as e:
            print("gw restore state", e, flush=True)

    act = None
    items = merged.get("items") or {}
    aid2 = merged.get("active_id")
    if aid2:
        act = items.get(str(aid2))
    if not act or act.get("status") != "active":
        for it in items.values():
            if it.get("status") == "active":
                act = it
                break
    ents = list((act.get("entries") or {}).values()) if act else []
    n_all = sum(1 for e in ents if not e.get("excluded"))
    n_ok = sum(1 for e in ents if e.get("complete") and not e.get("excluded"))
    return {
        "ok": True,
        "merged_items": len(items),
        "active_id": (act or {}).get("id"),
        "complete": n_ok,
        "started": n_all,
        "prize": (act or {}).get("prize"),
        "channel_message_id": (act or {}).get("channel_message_id"),
        "message": f"restore ok: complete={n_ok} started={n_all}",
    }


def _read_file_store() -> dict:
    if not GW_PATH.is_file():
        return _empty_store()
    try:
        raw = json.loads(GW_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _empty_store()
        items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
        return {"items": items, "active_id": raw.get("active_id")}
    except Exception as e:
        print("giveaways read fail", e, flush=True)
        return _empty_store()


def _write_file_store(store: dict) -> None:
    GW_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = GW_PATH.with_suffix(".tmp")
    data = {
        "items": store.get("items") or {},
        "active_id": store.get("active_id"),
        "updated_at": int(time.time()),
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(GW_PATH)


def _merge_entry_dicts(a: dict, b: dict) -> dict:
    out = {**a, **b}
    if a.get("excluded") or b.get("excluded"):
        out["excluded"] = True
        out["complete"] = False
    out["subs_ok"] = bool(a.get("subs_ok") or b.get("subs_ok"))
    out["repost_ok"] = bool(a.get("repost_ok") or b.get("repost_ok"))
    out["complete"] = bool(a.get("complete") or b.get("complete"))
    inv = list(a.get("invites") or [])
    for x in b.get("invites") or []:
        if x not in inv:
            inv.append(x)
    out["invites"] = inv
    if b.get("repost_proof_file_id") or a.get("repost_proof_file_id"):
        out["repost_proof_file_id"] = b.get("repost_proof_file_id") or a.get(
            "repost_proof_file_id"
        )
    return out


def _merge_item(a: dict, b: dict) -> dict:
    out = {**a, **b}
    ea = a.get("entries") if isinstance(a.get("entries"), dict) else {}
    eb = b.get("entries") if isinstance(b.get("entries"), dict) else {}
    merged: dict = {}
    for uid in set(ea) | set(eb):
        if uid in ea and uid in eb:
            merged[uid] = _merge_entry_dicts(ea[uid], eb[uid])
        else:
            merged[uid] = eb.get(uid) or ea.get(uid)
    out["entries"] = merged
    rank = {"cancelled": 3, "ended": 3, "active": 2, "draft": 1}
    sa, sb = a.get("status") or "draft", b.get("status") or "draft"
    out["status"] = sb if rank.get(sb, 0) >= rank.get(sa, 0) else sa
    out["channel_message_id"] = b.get("channel_message_id") or a.get("channel_message_id")
    out["ends_at"] = b.get("ends_at") or a.get("ends_at")
    if b.get("winners_list"):
        out["winners_list"] = b["winners_list"]
    if b.get("winner"):
        out["winner"] = b["winner"]
    return out


def _merge_stores(a: dict, b: dict) -> dict:
    ai = a.get("items") if isinstance(a.get("items"), dict) else {}
    bi = b.get("items") if isinstance(b.get("items"), dict) else {}
    items: dict = {}
    for gid in set(ai) | set(bi):
        if gid in ai and gid in bi:
            items[gid] = _merge_item(ai[gid], bi[gid])
        else:
            items[gid] = bi.get(gid) or ai.get(gid)
    active = None
    for cand in (b.get("active_id"), a.get("active_id")):
        if cand and str(cand) in items and items[str(cand)].get("status") == "active":
            active = str(cand)
            break
    if not active:
        for gid, it in items.items():
            if it.get("status") == "active":
                active = str(gid)
                break
    return {"items": items, "active_id": active}


def _load_store(state: dict | None = None) -> dict:
    """Файл + state (миграция), merge."""
    file_s = _read_file_store()
    try:
        st = state if state is not None else load_state()
        mem = st.get("giveaways") if isinstance(st.get("giveaways"), dict) else {}
    except Exception:
        mem = {}
    store = _merge_stores(file_s, mem)
    # one-time migrate: if file empty but state has data — write file
    if not (file_s.get("items") or {}) and (store.get("items") or {}):
        try:
            _write_file_store(store)
        except Exception as e:
            print("gw migrate write", e, flush=True)
    return store


def _root(state: dict) -> dict:
    """Совместимость: корень giveaways в state dict (in-place)."""
    store = _load_store(state)
    g = state.setdefault("giveaways", {})
    g["items"] = store.get("items") or {}
    g["active_id"] = store.get("active_id")
    if "items" not in g:
        g["items"] = {}
    return g


def list_items(state: dict | None = None) -> list[dict]:
    store = _load_store(state)
    items = (store.get("items") or {}).values()
    return sorted(items, key=lambda x: int(x.get("created_at") or 0), reverse=True)


def get_active(state: dict | None = None) -> dict | None:
    store = _load_store(state)
    aid = store.get("active_id")
    items = store.get("items") or {}
    if aid:
        it = items.get(str(aid))
        if it and it.get("status") == "active":
            return it
    for it in items.values():
        if it.get("status") == "active":
            return it
    return None


def get_by_id(gid: str, state: dict | None = None) -> dict | None:
    store = _load_store(state)
    return (store.get("items") or {}).get(str(gid))


def save_item(item: dict, state: dict | None = None) -> dict:
    """Пишем в media/giveaways.json + state (merge)."""
    with _LOCK:
        store = _load_store(state)
        items = store.setdefault("items", {})
        items[str(item["id"])] = item
        if item.get("status") == "active":
            store["active_id"] = item["id"]
        elif store.get("active_id") == item.get("id") and item.get("status") != "active":
            store["active_id"] = None
            for gid, it in items.items():
                if it.get("status") == "active":
                    store["active_id"] = gid
                    break
        try:
            _write_file_store(store)
        except Exception as e:
            print("gw file save fail", e, flush=True)
        # mirror into state.json
        try:
            st = state if state is not None else load_state()
            st["giveaways"] = {
                "items": store.get("items") or {},
                "active_id": store.get("active_id"),
            }
            save_state(st)
        except Exception as e:
            print("gw state save fail", e, flush=True)
    return item


def create(
    prize: str,
    *,
    hours: int = DEFAULT_HOURS,
    mode: str = "quest",  # quest | button | comments
    auto_draw: bool = True,
    require_sub: bool = True,
    require_repost: bool = True,
    require_invites: int = 0,
    required_channels: list[str] | None = None,
    winners: int = 1,
    button_text: str = BTN_JOIN,
    marker: str = DEFAULT_MARKER,
    require_marker: bool = False,
    min_chars: int = MIN_CHARS,
    min_complete: int = DEFAULT_MIN_COMPLETE,
    extend_hours: int = DEFAULT_EXTEND_HOURS,
) -> dict:
    prize = (prize or "").strip()
    if not prize:
        raise ValueError("Укажи приз")
    hours = max(1, min(int(hours or DEFAULT_HOURS), 24 * 30))
    chans = list(required_channels or [])
    # основной канал всегда в списке, если require_sub
    now = int(time.time())
    item = {
        "id": new_id(),
        "prize": prize,
        "status": "draft",
        "mode": mode if mode in ("quest", "button", "comments", "both") else "quest",
        "auto_draw": bool(auto_draw),
        "require_sub": bool(require_sub),
        "require_repost": bool(require_repost),
        "require_invites": max(0, min(int(require_invites or 0), 20)),
        "required_channels": chans,  # extra @channels; main channel always checked
        "winners": max(1, min(int(winners or 1), 10)),
        "button_text": (button_text or BTN_JOIN).strip()[:40],
        "marker": marker or DEFAULT_MARKER,
        "require_marker": bool(require_marker),
        "min_chars": int(min_chars),
        # итог только когда complete >= min_complete; иначе ends_at += extend_hours
        "min_complete": max(0, min(int(min_complete or 0), 500)),
        "extend_hours": max(1, min(int(extend_hours or DEFAULT_EXTEND_HOURS), 168)),
        "extend_count": 0,
        "hours": hours,
        "created_at": now,
        "starts_at": None,
        "ends_at": None,
        "channel_message_id": None,
        "discuss_root_id": None,
        "entries": {},
        "winner": None,
        "winners_list": [],
        "drawn_at": None,
    }
    save_item(item)
    return item


def activate(item: dict, *, channel_message_id: int | None = None) -> dict:
    if item.get("status") not in ("draft", "active"):
        raise ValueError(f"Нельзя стартовать из статуса {item.get('status')}")
    now = int(time.time())
    hours = int(item.get("hours") or DEFAULT_HOURS)
    item["status"] = "active"
    item["starts_at"] = item.get("starts_at") or now
    item["ends_at"] = now + hours * 3600
    if channel_message_id:
        item["channel_message_id"] = int(channel_message_id)
    st = load_state()
    mid = item.get("channel_message_id")
    if mid:
        roots = st.get("channel_discuss_root") or {}
        root = roots.get(str(mid)) or roots.get(int(mid))
        if root:
            item["discuss_root_id"] = int(root)
    save_item(item, st)
    return item


def bind_channel_post(item: dict, channel_message_id: int) -> dict:
    item["channel_message_id"] = int(channel_message_id)
    st = load_state()
    roots = st.get("channel_discuss_root") or {}
    root = roots.get(str(channel_message_id)) or roots.get(int(channel_message_id))
    if root:
        item["discuss_root_id"] = int(root)
    if item.get("status") == "draft":
        activate(item, channel_message_id=channel_message_id)
    else:
        save_item(item, st)
    return item


def cancel(item: dict) -> dict:
    item["status"] = "cancelled"
    item["ends_at"] = int(time.time())
    st = load_state()
    g = _root(st)
    if g.get("active_id") == item.get("id"):
        g["active_id"] = None
    save_item(item, st)
    return item


def end(item: dict) -> dict:
    item["status"] = "ended"
    item["ends_at"] = int(time.time())
    st = load_state()
    g = _root(st)
    if g.get("active_id") == item.get("id"):
        g["active_id"] = None
    save_item(item, st)
    return item


def is_expired(item: dict) -> bool:
    ends = item.get("ends_at")
    if not ends:
        return False
    return int(time.time()) >= int(ends)


def min_complete_needed(item: dict) -> int:
    """Сколько complete нужно для авто-итога (0 = только по таймеру)."""
    try:
        return max(0, int(item.get("min_complete") or 0))
    except Exception:
        return 0


def complete_ready(item: dict) -> bool:
    need = min_complete_needed(item)
    if need <= 0:
        return True
    return entry_count(item, complete_only=True) >= need


def maybe_extend_for_min_complete(item: dict) -> dict | None:
    """
    Если дедлайн вышел, а complete < min_complete — сдвинуть ends_at.
    Возвращает item если продлили, иначе None.
    """
    if item.get("status") != "active":
        return None
    need = min_complete_needed(item)
    if need <= 0:
        return None
    if not is_expired(item):
        return None
    n_ok = entry_count(item, complete_only=True)
    if n_ok >= need:
        return None
    ext_n = int(item.get("extend_count") or 0)
    if ext_n >= MAX_EXTEND_COUNT:
        return None  # caller may force end / notify
    hours = int(item.get("extend_hours") or DEFAULT_EXTEND_HOURS)
    hours = max(1, min(hours, 168))
    now = int(time.time())
    item["ends_at"] = now + hours * 3600
    item["extend_count"] = ext_n + 1
    item["last_extend_at"] = now
    item["last_extend_reason"] = f"complete {n_ok}/{need}"
    save_item(item)
    return item


def entry_count(item: dict, *, complete_only: bool = False) -> int:
    entries = item.get("entries") or {}
    if not complete_only:
        return len(entries)
    return sum(
        1
        for e in entries.values()
        if (e.get("complete") if complete_only else True) and not e.get("excluded")
    )


def complete_entries(item: dict) -> list[dict]:
    return [
        e
        for e in (item.get("entries") or {}).values()
        if e.get("complete") and not e.get("excluded")
    ]


def get_entry(item: dict, user_id: int) -> dict | None:
    """Участник только если уже в списке (без авто-создания)."""
    return (item.get("entries") or {}).get(str(int(user_id)))


def ensure_entry(
    item: dict,
    *,
    user_id: int,
    username: str = "",
    name: str = "",
    invited_by: int | None = None,
    source: str = "quest",
) -> dict:
    """Создать/вернуть черновик участника (ещё не complete).
    Вызывать ТОЛЬКО после явного «Участвовать» (gw_ / gwref_).
    """
    uid = str(int(user_id))
    entries = item.setdefault("entries", {})
    if uid not in entries:
        entries[uid] = {
            "user_id": int(user_id),
            "username": (username or "").lstrip("@"),
            "name": name or username or str(user_id),
            "text": "",
            "source": source or "quest",
            "ts": int(time.time()),
            "subs_ok": False,
            "repost_ok": False,
            "repost_proof_file_id": None,  # скрин репоста другу
            "invites": [],  # user_ids invited who started/joined
            "invited_by": int(invited_by) if invited_by else None,
            "complete": False,
            "completed_at": None,
            "joined_via": source or "quest",
        }
        # credit inviter
        if invited_by and int(invited_by) != int(user_id):
            inv_uid = str(int(invited_by))
            if inv_uid in entries:
                invs = entries[inv_uid].setdefault("invites", [])
                if int(user_id) not in invs:
                    invs.append(int(user_id))
                # re-evaluate inviter complete
                _recompute_complete(item, entries[inv_uid])
        save_item(item)
    else:
        e = entries[uid]
        if username:
            e["username"] = username.lstrip("@")
        if name:
            e["name"] = name
        if invited_by and not e.get("invited_by") and int(invited_by) != int(user_id):
            e["invited_by"] = int(invited_by)
            inv_uid = str(int(invited_by))
            if inv_uid in entries:
                invs = entries[inv_uid].setdefault("invites", [])
                if int(user_id) not in invs:
                    invs.append(int(user_id))
                _recompute_complete(item, entries[inv_uid])
            save_item(item)
    return entries[uid]


def enrollment_gaps(item: dict, entry: dict) -> list[str]:
    """Что ещё не закрыто для зачисления (по флагам в entry)."""
    gaps = []
    if item.get("require_sub", True) and not entry.get("subs_ok"):
        gaps.append("подписка")
    if item.get("require_repost", True) and not entry.get("repost_ok"):
        gaps.append("репост")
    need_inv = int(item.get("require_invites") or 0)
    have = len(entry.get("invites") or [])
    if need_inv > 0 and have < need_inv:
        gaps.append(f"друзья {have}/{need_inv}")
    return gaps


def _recompute_complete(item: dict, entry: dict) -> bool:
    """
    В розыгрыш (complete) — только если закрыты подписка + репост (+ друзья).
    Без «засчитать в конкурс» пока шаги не проверены.
    """
    gaps = enrollment_gaps(item, entry)
    was = bool(entry.get("complete"))
    entry["complete"] = len(gaps) == 0
    if entry["complete"] and not was:
        entry["completed_at"] = int(time.time())
    if not entry["complete"]:
        # снять зачисление, если отписался / сбросили флаг
        if was:
            entry["completed_at"] = None
    return entry["complete"]


def set_subs_ok(item: dict, user_id: int, ok: bool) -> dict:
    e = ensure_entry(item, user_id=user_id)
    e["subs_ok"] = bool(ok)
    e["subs_checked_at"] = int(time.time())
    _recompute_complete(item, e)
    save_item(item)
    return e


def set_repost_ok(
    item: dict,
    user_id: int,
    ok: bool = True,
    *,
    proof_file_id: str | None = None,
) -> dict:
    e = ensure_entry(item, user_id=user_id)
    e["repost_ok"] = bool(ok)
    e["repost_checked_at"] = int(time.time())
    if proof_file_id:
        e["repost_proof_file_id"] = str(proof_file_id)
    _recompute_complete(item, e)
    save_item(item)
    return e


def eligible_for_draw(item: dict) -> list[dict]:
    """Только complete + есть подписка и репост по флагам (перед live-перепроверкой)."""
    out = []
    for e in complete_entries(item):
        if e.get("excluded"):
            continue
        if item.get("require_sub", True) and not e.get("subs_ok"):
            continue
        if item.get("require_repost", True) and not e.get("repost_ok"):
            continue
        out.append(e)
    return out


def exclude_entry(item: dict, user_id: int, *, reason: str = "excluded") -> bool:
    """Исключить из розыгрыша (тест/владелец). Не удаляем — иначе merge state вернёт."""
    e = (item.get("entries") or {}).get(str(int(user_id)))
    if not e:
        return False
    e["excluded"] = True
    e["complete"] = False
    e["exclude_reason"] = reason
    e["completed_at"] = None
    save_item(item)
    return True


def progress(item: dict, entry: dict) -> dict:
    need_inv = int(item.get("require_invites") or 0)
    inv_n = len(entry.get("invites") or [])
    return {
        "subs": bool(entry.get("subs_ok")) if item.get("require_sub", True) else True,
        "repost": bool(entry.get("repost_ok")) if item.get("require_repost", True) else True,
        "invites": inv_n,
        "invites_need": need_inv,
        "invites_ok": inv_n >= need_inv,
        "complete": bool(entry.get("complete")),
    }


def progress_bar(item: dict, entry: dict) -> str:
    p = progress(item, entry)
    parts = []
    if item.get("require_sub", True):
        parts.append(("Подписки", p["subs"]))
    if item.get("require_repost", True):
        parts.append(("Репост другу", p["repost"]))
    if int(item.get("require_invites") or 0) > 0:
        parts.append((f"Друзья {p['invites']}/{p['invites_need']}", p["invites_ok"]))
    lines = []
    for name, ok in parts:
        lines.append(f"{'✅' if ok else '⬜️'} {name}")
    done = sum(1 for _, ok in parts if ok)
    lines.insert(0, f"Прогресс: <b>{done}/{len(parts)}</b>")
    if p["complete"]:
        lines.append("\n🎉 <b>Ты в розыгрыше!</b> (подписка + репост проверены)")
    else:
        gaps = enrollment_gaps(item, entry)
        if gaps:
            lines.append("\n⏳ В конкурс ещё не зачислен. Нужно: " + ", ".join(gaps))
    return "\n".join(lines)


def all_required_channels(cfg: dict, item: dict) -> list[str]:
    """
    Список каналов для проверки подписки.
    Всегда канонический @Vaggo01 — не личный аккаунт и не чужой «юз».
    """
    # приоритет: username канала, не user id
    uname = (cfg.get("channel_username") or "Vaggo01").strip().lstrip("@")
    if uname.lower() in ("vagdar1", "directorvaggobot", ""):
        uname = "Vaggo01"
    main = f"@{uname}"
    # numeric id канала (супергруппа/канал) — доп. проверка надёжнее username
    try:
        num = int(cfg.get("channel_numeric_id") or -1004445937686)
    except Exception:
        num = -1004445937686

    out: list[str] = []
    if item.get("require_sub", True):
        out.append(main)
        # если username другой — numeric как второй эталон не дублируем в UI,
        # проверку делаем по main; numeric используем в bot._check_all_subs fallback
    for c in item.get("required_channels") or []:
        c = str(c).strip()
        if not c:
            continue
        # отсечь похожие на user-id (положительные) — это не каналы
        if c.lstrip("-").isdigit() and not c.startswith("-100"):
            continue
        if not c.startswith("@") and not c.lstrip("-").isdigit():
            c = "@" + c
        # не подмешивать личку владельца
        if c.lower() in ("@vagdar1", "vagdar1"):
            continue
        if c not in out:
            out.append(c)
    # заглушка для линтера/будущего: num доступен через cfg
    _ = num
    return out


def join_button_label(item: dict) -> str:
    """Без счётчиков — число участников только владельцу (/gstatus)."""
    return (item.get("button_text") or BTN_JOIN).strip() or BTN_JOIN


def join_keyboard(item: dict, *, bot_username: str = "DirectorVaggobot") -> dict:
    """URL в бота (квест) + условия. Без публичной статистики."""
    gid = str(item.get("id") or "")
    uname = (bot_username or "DirectorVaggobot").lstrip("@")
    url = f"https://t.me/{uname}?start=gw_{gid}"
    return {
        "inline_keyboard": [
            [{"text": join_button_label(item), "url": url}],
            [{"text": "📋 Условия", "callback_data": f"gw:rules:{gid}"}],
        ]
    }


def quest_keyboard(item: dict, entry: dict | None = None) -> dict:
    gid = str(item.get("id") or "")
    rows = []
    if item.get("require_sub", True):
        rows.append([{"text": "✅ Проверить подписки", "callback_data": f"gw:chksub:{gid}"}])
    if item.get("require_repost", True):
        rows.append([{"text": "📨 Как сделать репост?", "callback_data": f"gw:rephow:{gid}"}])
    if int(item.get("require_invites") or 0) > 0:
        rows.append([{"text": "👥 Моя ссылка / друзья", "callback_data": f"gw:inv:{gid}"}])
    rows.append([{"text": "🔄 Обновить прогресс", "callback_data": f"gw:prog:{gid}"}])
    return {"inline_keyboard": rows}


def ended_keyboard(item: dict) -> dict:
    n = entry_count(item, complete_only=True)
    return {
        "inline_keyboard": [
            [{"text": f"🏁 Завершён · {n} в розыгрыше", "callback_data": f"gw:ended:{item.get('id')}"}],
        ]
    }


def draw_winner(item: dict, *, seed: int | None = None) -> dict | None:
    w = draw_winners(item, seed=seed)
    return w[0] if w else None


def draw_winners(item: dict, *, seed: int | None = None, pool: list[dict] | None = None) -> list[dict]:
    entries = list(pool) if pool is not None else eligible_for_draw(item)
    if not entries:
        # fallback: if nobody complete, don't use incomplete
        return []
    k = min(int(item.get("winners") or 1), len(entries))
    rng = random.Random(seed if seed is not None else time.time_ns())
    picked = rng.sample(entries, k=k)
    item["winner"] = picked[0]
    item["winners_list"] = picked
    item["drawn_at"] = int(time.time())
    item["status"] = "ended"
    item["ends_at"] = int(time.time())
    st = load_state()
    g = _root(st)
    if g.get("active_id") == item.get("id"):
        g["active_id"] = None
    save_item(item, st)
    return picked


def matches_giveaway_thread(item: dict, msg: dict, state: dict | None = None) -> bool:
    st = state or load_state()
    ch_mid = item.get("channel_message_id")
    if not ch_mid:
        return bool(item.get("loose_match"))
    root = item.get("discuss_root_id")
    if not root:
        roots = st.get("channel_discuss_root") or {}
        root = roots.get(str(ch_mid)) or roots.get(int(ch_mid))
        if root:
            item["discuss_root_id"] = int(root)
            save_item(item, st)
    rt = msg.get("reply_to_message") or {}
    if root and rt.get("message_id") == int(root):
        return True
    if int(rt.get("forward_from_message_id") or 0) == int(ch_mid):
        return True
    thread = msg.get("message_thread_id")
    if root and thread and int(thread) == int(root):
        return True
    return False


def announce_text(item: dict) -> str:
    prize = html.escape(item.get("prize") or "приз")
    hours = int(item.get("hours") or DEFAULT_HOURS)
    ends = item.get("ends_at")
    if ends:
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(int(ends)))
        deadline = f"ориентир <b>{when}</b>"
    else:
        deadline = f"ориентир <b>{hours} ч</b> с публикации"
    nw = int(item.get("winners") or 1)
    need = min_complete_needed(item)
    inv = int(item.get("require_invites") or 0)
    steps = ["1️⃣ Подписка на канал(ы) — проверяем"]
    if item.get("require_repost", True):
        steps.append(
            "2️⃣ Перешли этот пост <b>любому другу</b> → "
            "скрин пришли боту (бот сам проверит)"
        )
    if inv > 0:
        steps.append(
            f"3️⃣ Пригласи <b>{inv}</b> друга(ей) по своей ссылке"
        )
    steps_s = "\n".join(steps)
    min_line = ""
    if need > 0:
        min_line = (
            f"Итог — когда в барабане <b>{need}</b> человек "
            f"(если меньше — срок <b>продлевается</b>).\n"
        )
    return (
        f"🎁 <b>РОЗЫГРЫШ</b>\n\n"
        f"Приз: <b>{prize}</b>\n"
        f"Победителей: <b>{nw}</b>\n"
        f"Срок: {deadline}\n"
        f"{min_line}\n"
        f"<b>Чтобы участвовать:</b>\n"
        f"{steps_s}\n\n"
        f"Жми <b>Участвовать</b> → бот проверит шаги.\n"
        f"В барабан — только кто прошёл проверку 🔥"
    )


def format_status(item: dict | None) -> str:
    if not item:
        return (
            "🎁 <b>Розыгрыш-квест</b>\n\n"
            "Нет активного.\n"
            "/gnew приз → /gpost\n"
            "Шаги: подписки · репост другу · друзья · авто-победитель\n"
            f"Итог с <b>{DEFAULT_MIN_COMPLETE}</b> complete (иначе срок +{DEFAULT_EXTEND_HOURS}ч)"
        )
    n_ok = entry_count(item, complete_only=True)
    n_all = entry_count(item, complete_only=False)
    ends = item.get("ends_at")
    ends_s = time.strftime("%d.%m %H:%M", time.localtime(int(ends))) if ends else "—"
    need = min_complete_needed(item)
    min_s = f"{n_ok}/{need}" if need else str(n_ok)
    ext = int(item.get("extend_count") or 0)
    return (
        f"🎁 <b>Розыгрыш</b> <code>{html.escape(str(item.get('id')))}</code>\n"
        f"статус: <b>{html.escape(str(item.get('status')))}</b>\n"
        f"приз: {html.escape(str(item.get('prize') or ''))}\n"
        f"в барабане: <b>{min_s}</b> complete · начали: {n_all}\n"
        f"мин. для итога: <b>{need or '—'}</b> · продлений: {ext}\n"
        f"инвайтов: {item.get('require_invites', 0)} · "
        f"репост: {item.get('require_repost', True)} · "
        f"auto: {item.get('auto_draw', True)}\n"
        f"пост: <code>{item.get('channel_message_id') or '—'}</code> · до {ends_s}\n"
        f"/gentries · /gdraw · /gend"
    )


def format_entries(item: dict, *, limit: int = 40, only_complete: bool = False) -> str:
    """
    В розыгрыше = complete (все проверки).
    «В процессе» = нажали Участвовать, но шаги не закрыты.
    """
    entries = list((item.get("entries") or {}).values())
    if only_complete:
        entries = [e for e in entries if e.get("complete")]
    entries.sort(key=lambda x: (not x.get("complete"), -int(x.get("ts") or 0)))
    n_ok = entry_count(item, complete_only=True)
    n_all = sum(
        1
        for e in (item.get("entries") or {}).values()
        if not e.get("excluded")
    )
    n_excl = sum(1 for e in (item.get("entries") or {}).values() if e.get("excluded"))
    if only_complete:
        show = [e for e in entries if e.get("complete") and not e.get("excluded")]
    else:
        show = [e for e in entries if not e.get("excluded")]
    if not show and n_excl == 0:
        return (
            f"В розыгрыше (зачислены): <b>{n_ok}</b>\n"
            f"В процессе квеста: {n_all}\n\n"
            "Пока никого. Ждут «Участвовать» на посте."
        )
    lines = [
        f"✅ <b>В розыгрыше (зачислены): {n_ok}</b>\n"
        f"⏳ В процессе (ещё НЕ в конкурсе): {max(0, n_all - n_ok)}\n"
    ]
    if n_excl:
        lines.append(f"🚫 Исключены (тест/владелец): {n_excl}\n")
    lines.append(
        "<i>В барабан только ✅. Исключённые и «в процессе» — нет.</i>\n"
    )
    for i, e in enumerate(show[:limit], 1):
        un = e.get("username")
        who = f"@{un}" if un else e.get("name") or e.get("user_id")
        mark = "✅ ЗАЧИСЛЕН" if e.get("complete") else "⏳ не в конкурсе"
        inv = len(e.get("invites") or [])
        flags = (
            f"sub={'✓' if e.get('subs_ok') else '✗'} "
            f"rep={'✓' if e.get('repost_ok') else '✗'} "
            f"inv={inv}"
        )
        lines.append(f"{i}. {mark} · {html.escape(str(who))} · {flags}")
    return "\n".join(lines)


def purge_ghost_entries(item: dict) -> int:
    """Убрать тех, кто попал без явного старта квеста и без прогресса."""
    entries = item.get("entries") or {}
    remove = []
    for uid, e in list(entries.items()):
        if e.get("complete"):
            continue
        if e.get("subs_ok") or e.get("repost_ok") or (e.get("invites") or []):
            continue
        # пустой черновик без прогресса
        src = e.get("source") or ""
        if src in ("plain_start", "ghost", "auto", "") or not e.get("joined_via"):
            # keep only if joined_via is set (gw_ / gwref_)
            if not e.get("joined_via"):
                remove.append(uid)
    for uid in remove:
        entries.pop(uid, None)
    if remove:
        save_item(item)
    return len(remove)


def winner_public_text(item: dict, winner: dict | None = None, winners: list | None = None) -> str:
    prize = html.escape(str(item.get("prize") or "приз"))
    n = entry_count(item, complete_only=True)
    wlist = winners or item.get("winners_list") or ([winner] if winner else [])
    if not wlist and item.get("winner"):
        wlist = [item["winner"]]
    lines_w = []
    for w in wlist:
        if not w:
            continue
        un = w.get("username")
        who = f"@{html.escape(un)}" if un else html.escape(str(w.get("name") or w.get("user_id")))
        lines_w.append(who)
    who_s = ", ".join(lines_w) if lines_w else "—"
    return (
        f"🏆 <b>ПОБЕДИТЕЛЬ</b>\n\n"
        f"{who_s}\n\n"
        f"Приз: <b>{prize}</b>\n"
        f"Прошли квест: {n}\n\n"
        f"Победитель — пиши @DirectorVaggobot в личку 🔥\n"
        f"Остальным спасибо — вы сделали этот розыгрыш живым."
    )


def reveal_script(item: dict, winners: list[dict]) -> list[str]:
    """Интересная «выдача»: серия постов перед именем."""
    prize = html.escape(str(item.get("prize") or "приз"))
    n = entry_count(item, complete_only=True)
    n_all = entry_count(item, complete_only=False)
    names = []
    for w in winners:
        un = w.get("username")
        names.append(f"@{html.escape(un)}" if un else html.escape(str(w.get("name") or w.get("user_id"))))
    who = ", ".join(names) if names else "—"
    # honorable: random incomplete or non-winners who completed
    others = [e for e in complete_entries(item) if e not in winners and e.get("user_id") not in {w.get("user_id") for w in winners}]
    random.shuffle(others)
    shout = []
    for e in others[:3]:
        un = e.get("username")
        shout.append(f"@{html.escape(un)}" if un else html.escape(str(e.get("name") or "")))
    shout_s = ", ".join(shout) if shout else "всем, кто прошёл квест"

    return [
        (
            f"⏳ <b>Розыгрыш закрыт</b>\n\n"
            f"Приз: <b>{prize}</b>\n"
            f"Зашли в квест: {n_all}\n"
            f"Дошли до конца: <b>{n}</b>\n\n"
            f"Сейчас вытащим победителя из тех, кто закрыл все шаги…"
        ),
        (
            f"🥁 <b>Барабан крутится</b>\n\n"
            f"Учитывались только:\n"
            f"• подписки (проверены)\n"
            f"• репост другу (скрин проверен)\n\n"
            f"Честный random. Без накрутки."
        ),
        (
            f"👀 Финалисты на месте…\n"
            f"Респект: {shout_s}"
        ),
        (
            f"🏆 <b>И… ПОБЕДИТЕЛЬ!</b>\n\n"
            f"👉 {who}\n\n"
            f"Приз: <b>{prize}</b>\n\n"
            f"Победитель — напиши @DirectorVaggobot в личку в течение 48ч.\n"
            f"Остальным — спасибо, вы огонь 🔥\n"
            f"Скоро ещё розыгрыши."
        ),
    ]


def due_auto_draw(state: dict | None = None) -> list[dict]:
    """
    Кандидаты на авто-действие:
    - дедлайн вышел, или
    - min_complete достигнут (можно тянуть раньше).
    Продление при «мало людей» делает tick в bot.py.
    """
    st = state or load_state()
    out = []
    for it in list_items(st):
        if it.get("status") != "active":
            continue
        if not it.get("auto_draw", True):
            continue
        need = min_complete_needed(it)
        n_ok = entry_count(it, complete_only=True)
        if need > 0 and n_ok >= need:
            out.append(it)
            continue
        if is_expired(it):
            out.append(it)
    return out


# --- legacy shims used by older call sites ---
def try_register_entry(item, **kwargs):
    """Старый API: для quest не засчитывает complete сразу."""
    uid = kwargs.get("user_id")
    if not uid:
        return False, "not_active"
    if item.get("status") != "active":
        return False, "not_active"
    if is_expired(item):
        return False, "expired"
    e = ensure_entry(
        item,
        user_id=int(uid),
        username=kwargs.get("username") or "",
        name=kwargs.get("name") or "",
    )
    if e.get("complete"):
        return False, "duplicate"
    return True, "ok"
