"""Стиль канала Vaggo + мозги: Grok (xAI API) → Ollama → шаблон."""
from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

from state import load_config

# кэш, чтобы UI не ждал сеть
_ollama_cache: dict[str, Any] = {"t": 0.0, "ok": False}
_brain_cache: dict[str, Any] = {"t": 0.0, "data": None}


SYSTEM_CHANNEL = """Ты — главный автор Telegram-канала «Вагго» (@Vaggo01). Пишешь так, чтобы пост ХОТЕЛОСЬ дочитать и сохранить.

У тебя есть live web/X search — для свежих фактов, цен, дат, релизов ИИ пользуйся им.
Не выдумывай цифры; если нашёл в поиске — опирайся на них.

ГЛАВНОЕ:
- Интересно + полезно. Не канцелярит, не «в этом посте мы разберём», не скучный учебник.
- Крючок с первой строки (сцена, удар, вопрос, парадокс) — не «Друзья, сегодня поговорим».
- Читатель уносит: правило, схему, выбор «когда что», мини-эксперимент.
- Живой ритм: короткие абзацы, воздух, 1–2 сильных формулировки.
- Можно лёгкая ирония и характер. Без токсичности и кликбейта «шок».

ГОЛОС:
- обращение «Друзья» уместно, но не в каждой фразе;
- эмодзи умеренно (заголовок + акценты);
- HTML только: <b> <i> <code> — НЕ markdown **;
- длина обычно 900–1800 знаков (если не просили гайд).

РУБРИКИ:
- Вечерний Вагго — мысль + 1 действие;
- Битва нейросетей — ИИ: честно, practically, «когда брать»;
- Прокачка — тело, дисциплина;
- Кибер-Лайфхак — 1 фишка, сразу;
- Проект — LAB/OS/бот, закулисье без нытья.

ПРАВДА: не выдумывай цены и топ-рейтинги. Неуверен — «на практике» или проверь поиском.

СТРУКТУРА:
1) крючок
2) мясо (история/сравнение/шаги)
3) вывод + цепкий вопрос в комменты
4) в конце ссылка: 👉 <a href=\"https://t.me/Vaggo01\">t.me/Vaggo01</a> если уместно

Ответ = ТОЛЬКО готовый пост, без преамбулы."""


SYSTEM_COMMENT = """Ты — Вагго: живая нейронка Telegram-канала @Vaggo01.
С тобой общаются в комментариях — отвечай как умный собеседник, не как скрипт поддержки.

У тебя есть live-поиск (web + X), когда вопрос про даты, новости, «когда/сколько сейчас».
Пользуйся им для фактов; не выдумывай цифры и даты.

КТО ТЫ:
- характер канала: дружеский, прямой, с лёгкой иронией, без канцелярита;
- можно спорить, шутить, развивать мысль, спрашивать в ответ;
- ты «Вагго», не «помощник OpenAI» и не корпоративный FAQ.

ДВА РЕЖИМА (смотри инструкцию в user-сообщении):
1) ПЕРВЫЙ КОММЕНТ ПОД ПОСТОМ (seed) — пишешь про ТЕМУ ПОСТА, цепляешь разговор.
2) ОТВЕТ ПОДПИСЧИКУ — якорь = его сообщение. Пост только фон, если уместно.

КАК ОТВЕЧАТЬ подписчику:
- сначала по смыслу того, что он написал (вопрос / шутка / мнение);
- если вопрос короткий («когда финал», «кто лучше») — ответь по делу, не отпиской;
- на фактические вопросы дай точный короткий ответ + 1 цепляющая реплика;
- ЗАПРЕЩЕНО: «Слышу», «кинь мысль яснее», «продолжим как нормальный разговор»,
  «на связи», «напиши яснее» — это звучит как бот-заглушка;
- пост подтягивай, если его реплика про пост или без поста ответ пустой;
- свободно: короче на «привет», развёрнутее на вопрос;
- не «отличный вопрос / спасибо за коммент»;
- ссылки — только если просят «где открыть / какую сетку»;
- не токсичь; обычный текст, можно лёгкий markdown **но Telegram лучше plain**.

Ответ = только реплика, без кавычек и без «Вот мой ответ:»."""

SYSTEM_SEED = """Ты — Вагго, нейронка канала @Vaggo01.
Пишешь ПЕРВЫЙ комментарий под свежим постом канала.

Задача:
- 1–3 живых предложения строго ПО ТЕМЕ ПОСТА (не про погоду, не «классный пост» в пустоту);
- цепляешь диалог: мысль, угол, вопрос подписчикам;
- тон Вагго: прямой, дружеский, без канцелярита;
- без markdown, без HTML, без «как ИИ…».

Ответ = только текст комментария."""

SYSTEM_GUIDE = """Ты пишешь ПОЛЕЗНЫЙ гайд для Telegram-канала «Вагго».
Формат HTML (<b> <i> <code>), без markdown.
Объём 2500–3800 символов (лимит сообщения ~4096).
Структура: заголовок рубрики, крючок, разбор пунктами (сила/когда брать/слабее или шаги), связки/чеклист, вопрос в комменты, ссылка t.me/Vaggo01.
Без воды и кликбейта. Факты осторожно. Ответ = только гайд."""


def _xai_key(cfg: dict) -> str:
    """Явный API-ключ console.x.ai (xai-...)."""
    return (
        (cfg.get("xai_api_key") or "").strip()
        or (cfg.get("grok_api_key") or "").strip()
        or (os.environ.get("XAI_API_KEY") or "").strip()
        or (os.environ.get("GROK_API_KEY") or "").strip()
    )


# Маяк URL моста (ПК пишет при старте туннеля). Several mirrors — raw.github кэширует.
_BRIDGE_DISCOVERY_URLS = (
    "https://cdn.jsdelivr.net/gh/vladislavbondarev230-cloud/-vaggo-bot@main/bridge_endpoint.json",
    "https://raw.githubusercontent.com/vladislavbondarev230-cloud/%2Dvaggo-bot/main/bridge_endpoint.json",
    "https://cdn.jsdelivr.net/gh/vladislavbondarev230-cloud/-vaggo-bot@master/bridge_endpoint.json",
)
_bridge_discovery_cache: dict[str, Any] = {"t": 0.0, "url": ""}


def _bridge_url(cfg: dict) -> str:
    """URL моста на домашний ПК (cloudflared / ngrok)."""
    # на самом мосту discovery/loop запрещён
    if cfg.get("grok_bridge_disable") or (os.environ.get("GROK_BRIDGE_DISABLE") or "").strip() in (
        "1",
        "true",
        "yes",
    ):
        return ""
    direct = (
        (cfg.get("grok_bridge_url") or "").strip().rstrip("/")
        or (os.environ.get("GROK_BRIDGE_URL") or "").strip().rstrip("/")
    )
    if direct:
        return direct
    now = time.time()
    if _bridge_discovery_cache["url"] and (now - float(_bridge_discovery_cache["t"])) < 45:
        return str(_bridge_discovery_cache["url"] or "")

    urls: list[str] = []
    custom = (
        (cfg.get("grok_bridge_discovery") or "").strip()
        or (os.environ.get("GROK_BRIDGE_DISCOVERY") or "").strip()
    )
    if custom:
        urls.append(custom)
    urls.extend(_BRIDGE_DISCOVERY_URLS)

    import json as _json

    for disc in urls:
        try:
            r = requests.get(
                disc,
                timeout=8,
                headers={
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "User-Agent": "VaggoBot-BridgeDiscovery/1.1",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            if not r.ok or not r.content:
                print(f"bridge discovery http {r.status_code} {disc[:50]}", flush=True)
                continue
            text = r.content.decode("utf-8-sig", errors="replace")
            data = _json.loads(text) if text.strip() else {}
            u = str((data or {}).get("url") or "").strip().rstrip("/")
            if u.startswith("http"):
                _bridge_discovery_cache["url"] = u
                _bridge_discovery_cache["t"] = now
                print(f"bridge discovery ok: {u}", flush=True)
                return u
        except Exception as e:
            print("bridge discovery fail", disc[:40], e, flush=True)
    return str(_bridge_discovery_cache["url"] or "")


def _bridge_secret(cfg: dict) -> str:
    return (
        (cfg.get("grok_bridge_secret") or "").strip()
        or (os.environ.get("GROK_BRIDGE_SECRET") or "").strip()
    )


def _bridge_chat(
    cfg: dict,
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.55,
    tools: bool | None = None,
    max_tokens: int | None = None,
) -> str:
    url = _bridge_url(cfg)
    if not url:
        raise RuntimeError("no bridge")
    headers = {"Content-Type": "application/json"}
    sec = _bridge_secret(cfg)
    if sec:
        headers["X-Bridge-Secret"] = sec
    body: dict[str, Any] = {
        "system": system,
        "user": user,
        "model": model,
        "temperature": temperature,
    }
    if tools is not None:
        body["tools"] = bool(tools)
    if max_tokens is not None:
        body["max_tokens"] = int(max_tokens)
    # быстрый путь без tools — короткий timeout
    to = 45 if tools is False else 200
    resp = requests.post(
        f"{url}/chat",
        headers=headers,
        json=body,
        timeout=to,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"bridge chat {resp.status_code}: {resp.text[:200]}")
    data = resp.json() if resp.content else {}
    if not data.get("ok"):
        raise RuntimeError(f"bridge chat fail: {data.get('error') or data}")
    return str(data.get("text") or "").strip()


def _bridge_vision(
    cfg: dict,
    system: str,
    user: str,
    image_path: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
) -> str:
    import base64
    from pathlib import Path

    url = _bridge_url(cfg)
    if not url:
        raise RuntimeError("no bridge")
    raw = Path(image_path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    headers = {"Content-Type": "application/json"}
    sec = _bridge_secret(cfg)
    if sec:
        headers["X-Bridge-Secret"] = sec
    resp = requests.post(
        f"{url}/vision",
        headers=headers,
        json={
            "system": system,
            "user": user,
            "image_b64": b64,
            "suffix": Path(image_path).suffix or ".jpg",
            "model": model,
            "temperature": temperature,
        },
        timeout=150,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"bridge vision {resp.status_code}: {resp.text[:200]}")
    data = resp.json() if resp.content else {}
    if not data.get("ok"):
        raise RuntimeError(f"bridge vision fail: {data.get('error') or data}")
    return str(data.get("text") or "").strip()


def _grok_bearer(cfg: dict) -> tuple[str, str]:
    """
    Bearer для api.x.ai.
    source = bridge | session | api_key | ''
    Приоритет: мост (облако) → Super-сессия ПК → xai_api_key.
    Сессия выше битого ключа без кредитов.
    """
    if _bridge_url(cfg):
        return "bridge", "bridge"
    if cfg.get("use_grok_session", True):
        try:
            from grok_auth import session_token

            tok = session_token()
            if tok:
                return tok, "session"
        except Exception:
            pass
    key = _xai_key(cfg)
    if key:
        return key, "api_key"
    return "", ""


def grok_ok(cfg: dict) -> bool:
    tok, src = _grok_bearer(cfg)
    if src == "bridge":
        # быстрый health (кэш 20с)
        return True
    return bool(tok)


def ollama_ok(cfg: dict, *, force: bool = False, timeout: float = 0.35) -> bool:
    """Быстрый/кэшированный пинг Ollama. UI всегда с кэшем, без force."""
    now = time.time()
    if not force and (now - float(_ollama_cache["t"])) < 45:
        return bool(_ollama_cache["ok"])
    try:
        base = (cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
        r = requests.get(f"{base}/api/tags", timeout=timeout)
        ok = bool(r.ok)
    except Exception:
        ok = False
    _ollama_cache["ok"] = ok
    _ollama_cache["t"] = now
    return ok


def brain_status(
    cfg: dict | None = None,
    *,
    probe_ollama: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    probe_ollama=False — не трогать сеть (для UI).
    use_cache=True — вернуть кэш < 20 сек.
    """
    cfg = cfg or load_config()
    now = time.time()
    if use_cache and _brain_cache["data"] and (now - float(_brain_cache["t"])) < 20:
        return dict(_brain_cache["data"])

    mode = (cfg.get("brain") or "auto").lower().strip()
    g = grok_ok(cfg)
    _tok, gsrc = _grok_bearer(cfg)
    if probe_ollama:
        o = ollama_ok(cfg, force=True, timeout=0.35)
    else:
        # только последнее известное, без ожидания
        o = bool(_ollama_cache["ok"]) if _ollama_cache["t"] else False

    if mode == "grok":
        active = "grok" if g else "none"
    elif mode == "ollama":
        active = "ollama" if o else "none"
    elif mode == "template":
        active = "template"
    else:
        if g:
            active = "grok"
        elif o:
            active = "ollama"
        else:
            active = "template"
    sess = {}
    try:
        from grok_auth import session_info

        sess = session_info()
    except Exception:
        sess = {"ok": False}
    data = {
        "mode": mode,
        "active": active,
        "grok": g,
        "grok_source": gsrc,
        "ollama": o,
        "grok_model": cfg.get("grok_model") or cfg.get("grok_full_model") or "grok-4.5",
        "ollama_model": cfg.get("ollama_model") or "qwen2.5:7b",
        "grok_tools": bool(cfg.get("grok_tools", True)),
        "grok_web_search": bool(cfg.get("grok_web_search", True)),
        "grok_x_search": bool(cfg.get("grok_x_search", True)),
        "session": sess,
    }
    _brain_cache["data"] = data
    _brain_cache["t"] = now
    return dict(data)


def _grok_tools_enabled(cfg: dict, tools: bool | None) -> bool:
    if tools is not None:
        return bool(tools)
    return bool(cfg.get("grok_tools", True))


def _parse_responses_text(data: dict) -> str:
    """Достаёт финальный текст из /v1/responses (с tool calls)."""
    texts: list[str] = []
    for o in data.get("output") or []:
        if not isinstance(o, dict):
            continue
        if o.get("type") != "message":
            continue
        for c in o.get("content") or []:
            if not isinstance(c, dict):
                continue
            t = (c.get("text") or "").strip()
            if t:
                texts.append(t)
    if texts:
        # последний message обычно финальный ответ (после search)
        return texts[-1]
    # fallbacks
    for key in ("output_text", "text"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict) and (v.get("text") or "").strip():
            return str(v.get("text")).strip()
    return ""


def _grok_tools_list(cfg: dict) -> list[dict]:
    """Built-in agent tools xAI (web + X + code по конфигу)."""
    tools: list[dict] = []
    if cfg.get("grok_web_search", True):
        tools.append({"type": "web_search"})
    if cfg.get("grok_x_search", True):
        tools.append({"type": "x_search"})
    if cfg.get("grok_code_interpreter", False):
        tools.append({"type": "code_interpreter"})
    return tools


def grok_chat(
    cfg: dict,
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.55,
    tools: bool | None = None,
    max_tokens: int | None = None,
) -> str:
    """xAI: bridge (ПК Super) → Responses+tools → chat/completions."""
    model = (
        model
        or cfg.get("grok_model")
        or cfg.get("grok_full_model")
        or "grok-4.5"
    )
    use_tools = _grok_tools_enabled(cfg, tools)
    # 1) домашний мост (Bothost → твой ПК)
    if _bridge_url(cfg):
        try:
            return _bridge_chat(
                cfg,
                system,
                user,
                model=model,
                temperature=temperature,
                tools=use_tools,
                max_tokens=max_tokens,
            )
        except Exception as e:
            print("bridge chat fail", e, flush=True)
            # fall through to local key/session
    token, source = _grok_bearer(cfg)
    if source == "bridge":
        raise RuntimeError(f"Grok bridge недоступен: {_bridge_url(cfg)}")
    if not token:
        raise RuntimeError(
            "Нет доступа к Grok: grok login / xai_api_key / GROK_BRIDGE_URL"
        )
    base = (cfg.get("xai_base_url") or "https://api.x.ai/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 2) Responses API + built-in tools (live web / X) — флагманский режим
    if use_tools:
        tool_list = _grok_tools_list(cfg)
        if tool_list:
            inp: list[dict] = []
            if (system or "").strip():
                inp.append({"role": "system", "content": system})
            inp.append({"role": "user", "content": user})
            payload_r: dict[str, Any] = {
                "model": model,
                "input": inp,
                "tools": tool_list,
                "temperature": temperature,
            }
            try:
                resp = requests.post(
                    f"{base}/responses",
                    headers=headers,
                    json=payload_r,
                    timeout=180,
                )
                if resp.status_code in (401, 403):
                    raise RuntimeError(
                        f"Grok auth {resp.status_code} (source={source}). "
                        "Перелогинься: grok login  или обнови xai_api_key / credits"
                    )
                if resp.ok:
                    data = resp.json() if resp.content else {}
                    text = _parse_responses_text(data)
                    if text:
                        print(
                            f"grok responses ok model={data.get('model') or model} "
                            f"tools={','.join(t.get('type','') for t in tool_list)}",
                            flush=True,
                        )
                        return text
                    print("grok responses empty text, fallback chat", flush=True)
                else:
                    print(
                        f"grok responses {resp.status_code}: {resp.text[:180]}",
                        flush=True,
                    )
            except RuntimeError:
                raise
            except Exception as e:
                print("grok responses fail", e, flush=True)

    # 3) классический chat/completions (без live tools) — быстрый путь
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    resp = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60 if max_tokens and int(max_tokens) <= 400 else 120,
    )
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Grok auth {resp.status_code} (source={source}). "
            "Перелогинься: grok login  или обнови xai_api_key / credits"
        )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Grok пустой ответ: {data}")
    return ((choices[0].get("message") or {}).get("content") or "").strip()


def grok_vision(
    cfg: dict,
    system: str,
    user: str,
    image_path: str,
    *,
    model: str | None = None,
    temperature: float = 0.1,
) -> str:
    """Grok с картинкой (vision). image_path — локальный файл."""
    import base64
    from pathlib import Path

    model = (
        model
        or cfg.get("grok_vision_model")
        or cfg.get("grok_full_model")
        or cfg.get("grok_post_model")
        or "grok-4.5"
    )
    if _bridge_url(cfg):
        try:
            return _bridge_vision(
                cfg,
                system,
                user,
                image_path,
                model=model,
                temperature=temperature,
            )
        except Exception as e:
            print("bridge vision fail", e, flush=True)

    token, source = _grok_bearer(cfg)
    if source == "bridge" or not token:
        raise RuntimeError("Нет Grok для проверки скрина (bridge/session/api)")
    path = Path(image_path)
    raw = path.read_bytes()
    if len(raw) > 12_000_000:
        raise RuntimeError("Файл слишком большой")
    b64 = base64.b64encode(raw).decode("ascii")
    suf = path.suffix.lower()
    mime = "image/jpeg"
    if suf == ".png":
        mime = "image/png"
    elif suf == ".webp":
        mime = "image/webp"
    base = (cfg.get("xai_base_url") or "https://api.x.ai/v1").rstrip("/")
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ],
    }
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    if resp.status_code in (401, 403):
        raise RuntimeError(f"Grok vision auth {resp.status_code} (source={source})")
    if resp.status_code >= 400:
        raise RuntimeError(f"Grok vision {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Grok vision пустой: {data}")
    return ((choices[0].get("message") or {}).get("content") or "").strip()


def verify_giveaway_repost_screenshot(
    cfg: dict,
    image_path: str,
    *,
    channel_username: str = "Vaggo01",
    prize_hint: str = "",
) -> tuple[bool, str]:
    """
    Жёсткая проверка скрина: пересланный пост розыгрыша ЖИВОМУ человеку в Telegram.
    Отклонять: Избранное, боты, свой канал, рандомные чаты без диалога с человеком.
    Returns (ok, reason_ru).
    """
    import json

    uname = (channel_username or "Vaggo01").lstrip("@")
    system = f"""Ты строгий модератор розыгрыша Telegram. Смотришь ОДИН скриншот.
Ответь ТОЛЬКО JSON (без markdown, без текста вокруг):
{{"ok": true/false, "chat_kind": "person|bot|saved|channel|group|unknown", "has_forward": true/false, "from_vaggo": true/false, "reason": "кратко по-русски"}}

ok=true ТОЛЬКО если ВСЕ пункты верны:
1) Это скрин интерфейса Telegram (шапка чата, пузыри, UI), не просто картинка поста.
2) Видно ПЕРЕСЛАННОЕ сообщение (forward) — «Переслано из …» / Forwarded from … / имя канала.
3) Переслано из канала @{uname} / Vaggo / Vaggo01 / розыгрыш Google AI Pro / Gemini Pro (наш пост).
4) Чат — ЛИЧКА с ЖИВЫМ ЧЕЛОВЕКОМ (имя/аватар человека в шапке, не бот).
5) В чате видно, что сообщение ушло СОБЕСЕДНИКУ (диалог 1-на-1).

ok=false ОБЯЗАТЕЛЬНО, если хоть что-то из:
- «Избранное» / Saved Messages / «Saved» / «Избранные» / заметки себе
- чат с БОТОМ (в имени bot, Bot, «бот», синяя галочка бота, @…bot)
- переслано @DirectorVaggobot или любому другому боту
- только открыт канал @{uname} / лента канала без пересылки другу
- переслано в свой канал / в админку / в «Comments»
- групповой чат без явного личного диалога (если сомнение — false)
- скрин размыт, обрезан, не видно шапку чата
- нет признаков forward из нашего канала
- мем, коллаж, фото экрана без UI Telegram
- не уверен — ok=false (лучше отказать)

Будь параноидален: при сомнении ok=false."""
    user = (
        f"Канал розыгрыша: @{uname}. Тема: {prize_hint or 'Google AI Pro / Gemini 18 мес'}.\n"
        "Вопрос: человек переслал пост розыгрыша именно ДРУГУ (живому), "
        "а не боту, не в Избранное и не «куда попало»? Разбери скрин."
    )
    try:
        raw = grok_vision(cfg, system, user, image_path, temperature=0.0)
    except Exception as e:
        return False, f"не удалось проверить автоматически: {e}"
    text = (raw or "").strip()
    data: dict = {}
    m = re.search(r"\{[^{}]*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:
            data = {}
    reason = str(data.get("reason") or text or "не похоже на репост другу")[:200]
    ok = bool(data.get("ok")) if data else False
    chat_kind = str(data.get("chat_kind") or "unknown").lower()
    has_fwd = data.get("has_forward")
    from_v = data.get("from_vaggo")

    # жёсткие эвристики поверх ответа модели
    reject_kw = (
        "избранн",
        "saved message",
        "saved messages",
        "заметк",
        "бот",
        " bot",
        "@director",
        "directorvaggo",
        "самому себе",
        "себе в",
        "канал открыт",
        "лента канала",
        "не уверен",
        "сомнен",
    )
    low = (reason + " " + text).lower()
    for kw in reject_kw:
        if kw in low and ("не " + kw) not in low:
            # если модель сама пишет «не бот» — не рубим
            if kw in ("бот", " bot") and any(
                x in low for x in ("не бот", "не bot", "живой", "человек", "друг")
            ):
                continue
            if kw in ("избранн", "saved") and "не избран" in low:
                continue
            ok = False
            if "избран" in kw or "saved" in kw:
                reason = "похоже на Избранное / себе — нужен репост другу"
            elif "бот" in kw or "bot" in kw or "director" in kw:
                reason = "похоже на чат с ботом — нужен живой человек"
            break

    if chat_kind in ("bot", "saved", "channel"):
        ok = False
        reason = {
            "bot": "чат с ботом — не засчитываем",
            "saved": "Избранное / себе — не засчитываем",
            "channel": "это канал, не личка с другом",
        }.get(chat_kind, reason)
    if has_fwd is False:
        ok = False
        reason = reason if "пересл" in reason.lower() else "не видно пересланного поста"
    if from_v is False:
        ok = False
        reason = reason if "канал" in reason.lower() or "vaggo" in low else "не наш пост / не @Vaggo01"

    if not data:
        # без JSON — не доверяем
        if '"ok": true' in low or '"ok":true' in low:
            # всё равно требуем не bot/saved
            if any(x in low for x in ("избран", "saved", "бот", "bot")):
                return False, "сомнительный скрин (бот/Избранное?) — пришли другой"
            return True, (reason[:120] or "принято")
        return False, (reason[:200] or "не похоже на репост другу")

    if ok:
        return True, reason[:200] or "репост другу ок"
    return False, reason[:200] or "отклонено"


def ollama_chat(cfg: dict, system: str, user: str, *, model: str | None = None, temperature: float = 0.55) -> str:
    base = (cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    model = model or cfg.get("ollama_model") or "qwen2.5:7b"
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 1800},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    resp = requests.post(f"{base}/api/chat", json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("message") or {}).get("content") or ""
    return text.strip()


def llm_chat(
    cfg: dict,
    system: str,
    user: str,
    *,
    temperature: float = 0.55,
    prefer_fast: bool = False,
    max_tokens: int | None = None,
    tools: bool | None = None,
) -> tuple[str, str]:
    """
    Возвращает (text, engine) где engine = grok|ollama|template.
    Приоритет auto: Grok API → Ollama → template error (caller fallback).
    """
    st = brain_status(cfg)
    active = st["active"]
    mode = st["mode"]

    # explicit force
    if mode == "grok" or active == "grok":
        if grok_ok(cfg):
            model = (
                cfg.get("grok_model")
                or cfg.get("grok_full_model")
                or "grok-4.5"
            )
            if prefer_fast:
                model = (
                    cfg.get("grok_fast_model")
                    or cfg.get("grok_model")
                    or model
                )
            # prefer_fast → быстрая модель + БЕЗ live search
            if tools is None:
                use_tools = not prefer_fast and bool(cfg.get("grok_tools", True))
            else:
                use_tools = bool(tools)
            mt = max_tokens
            if mt is None and prefer_fast:
                mt = int(cfg.get("comment_max_tokens") or 220)
            return (
                grok_chat(
                    cfg,
                    system,
                    user,
                    model=model,
                    temperature=temperature,
                    tools=use_tools,
                    max_tokens=mt,
                ).strip(),
                "grok",
            )
        if mode == "grok":
            raise RuntimeError("brain=grok, но нет API-ключа xAI")

    if mode == "ollama" or active == "ollama" or (mode == "auto" and ollama_ok(cfg, force=True)):
        if ollama_ok(cfg, force=False):
            model = cfg.get("fast_model") if prefer_fast else cfg.get("ollama_model")
            return ollama_chat(cfg, system, user, model=model, temperature=temperature).strip(), "ollama"
        if mode == "ollama":
            raise RuntimeError("brain=ollama, но Ollama не запущена")

    raise RuntimeError("Нет доступного мозга (ни Grok API, ни Ollama)")


def format_html_light(text: str) -> str:
    """Грубый перевод **bold** и *italic* в HTML, если модель вернула markdown."""
    t = text.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", t)
    return t


def pick_reaction_for_text(text: str) -> str:
    """
    Реакция Telegram под смысл поста/коммента.
    Только эмодзи из ALLOWED_REACTIONS бота.
    """
    t = (text or "").lower()
    # порядок: от более специфичного к общему
    rules: list[tuple[tuple[str, ...], str]] = (
        (("😂", "🤣", "хаха", "ахах", "лол", "кек", "смеш", "ору", "угар", "прикол"), "😁"),
        (("🤔", "хм", "сомнев", "не уверен", "странн"), "🤔"),
        (("?", "как ", "почему", "зачем", "что если", "подскаж", "помоги", "вопрос"), "🤔"),
        (("🔥", "огонь", "имба", "пушка", "жестк", "топ", "круто", "класс", "вау", "бомб"), "🔥"),
        (("❤", "❤️", "люблю", "обожаю", "мил", "нрав", "сердц", "обним"), "❤"),
        (("спасибо", "благодар", "респект", "красава", "молодец"), "🙏"),
        (("👏", "браво", "аплод", "уважуха"), "👏"),
        (("🎉", "праздн", "др ", "др,", "с днём", "поздра", "ура"), "🎉"),
        (("⚡", "быстр", "молни", "энерг", "разгон"), "⚡"),
        (("спорт", "трен", "качал", "прокач", "жим", "бег", "зал"), "⚡"),
        (("🤯", "шок", "офиге", "жесть", "не верю", "вау"), "🤯"),
        (("😢", "груст", "жаль", "обид", "слёз", "печал"), "😢"),
        (("🤮", "бее", "фуу", "отврат", "тошн", "фу,"), "🤮"),
        (("💩", "говно", "херн", "отстой", "фигня"), "💩"),
        (("🤬", "злюсь", "бесит", "ненавиж"), "🤬"),
        (("😴", "спать", "устал", "лень", "засыпа"), "😴"),
        (("🤓", "научн", "исследован", "исследова"), "🤓"),
        (("👀", "интересн", "глянь", "посмотри"), "👀"),
        (("вау", "нереально", "офигенн"), "🤩"),
        (("нейросет", "chatgpt", "claude", "grok", "gemini", "промпт", " llm"), "🔥"),
        (("python", "баг ", "github", "cursor", "код "), "👨‍💻"),
        (("деньг", "заработ", "бизнес", "монет"), "💯"),
        (("привет", "здаров", "hello", "здарова"), "❤"),
        (("согласен", "точно", "+1", "плюсую"), "👍"),
        (("не согласен", "спорно"), "🤔"),
    )
    for words, emoji in rules:
        if any(w in t for w in words):
            return emoji
    # дефолт по длине/типу
    if "?" in (text or ""):
        return "🤔"
    if len(t) < 12:
        return "👍"
    if any(w in t for w in ("нейро", "ии", "ai", "бот", "канал")):
        return "🔥"
    return "❤"


def generate_post(topic: str, *, rubric: str = "", full_brain: bool = True) -> str:
    cfg = load_config()
    rubrics = "\n".join(f"- {r}" for r in (cfg.get("style") or {}).get("rubrics") or [])
    user = (
        f"Рубрика: {rubric or 'подбери сам по теме'}\n"
        f"Тема / бриф: {topic}\n\n"
        f"Рубрики канала:\n{rubrics}\n\n"
        f"Напиши пост 1000–1700 знаков: цепкий, живой, с пользой. "
        f"Запрещено: скучный тон, вода, «сегодня поговорим», список ради списка без характера. "
        f"Нужен крючок, мясо и вопрос в комменты."
    )
    try:
        # full_brain: сильнее модель + выше temperature
        if full_brain:
            model = (
                cfg.get("grok_post_model")
                or cfg.get("grok_full_model")
                or "grok-4.5"
            )
            raw = grok_chat(
                cfg,
                SYSTEM_CHANNEL,
                user,
                model=model,
                temperature=0.72,
                tools=True,
            )
            text = format_html_light(raw.strip())
            return text
        raw, engine = llm_chat(cfg, SYSTEM_CHANNEL, user, temperature=0.65)
        text = format_html_light(raw)
        return text
    except Exception:
        title = rubric or "Вагго"
        body = topic.strip()
        return (
            f"👋 <b>Друзья!</b>\n\n"
            f"<b>{html_escape_title(title)}</b>\n\n"
            f"{body}\n\n"
            f"<i>Черновик-заглушка: нет Grok API и Ollama. "
            f"Вставь xai_api_key или запусти Ollama — либо попроси Grok в чате написать пост.</i>\n\n"
            f"👇 Что думаешь?\n— Вагго"
        )


def generate_guide(topic: str, *, rubric: str = "Битва нейросетей") -> str:
    """Длинный полезный гайд 2500–3800 символов."""
    cfg = load_config()
    user = (
        f"Рубрика: {rubric}\n"
        f"Тема гайда: {topic}\n\n"
        f"Сделай развёрнутый практический гайд 2500–3800 символов. "
        f"Списки, сравнения, «когда брать», чеклист в конце."
    )
    try:
        raw, _ = llm_chat(cfg, SYSTEM_GUIDE, user, temperature=0.45)
        text = format_html_light(raw)
        if len(text) > 4090:
            text = text[:4085].rsplit("\n", 1)[0] + "…"
        return text
    except Exception as e:
        return (
            f"📋 <b>{html_escape_title(rubric)}</b>\n"
            f"<i>{html_escape_title(topic)}</i>\n\n"
            f"Не смог сгенерировать гайд: {html_escape_title(str(e)[:120])}\n"
            f"Проверь /brains или grok login."
        )


def html_escape_title(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _clean_comment_text(text: str, max_len: int) -> str:
    text = (text or "").strip().strip('"').strip("'")
    for bad in (
        "Конечно! ",
        "Конечно, ",
        "Отличный вопрос! ",
        "Отличный вопрос. ",
        "Спасибо за комментарий! ",
        "Спасибо за комментарий. ",
        "Как ИИ ",
        "Как языковая модель ",
    ):
        if text.startswith(bad):
            text = text[len(bad) :]
    # citations / markdown → telegram-friendly
    text = re.sub(r"\[\[(\d+)\]\]\((https?://[^)]+)\)", r"(\2)", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 \2", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    if len(text) > max_len:
        cut = text[: max_len - 1]
        n = cut.rfind("\n")
        if n > max_len // 2:
            cut = cut[:n]
        else:
            n = cut.rfind(". ")
            if n > max_len // 2:
                cut = cut[: n + 1]
        text = cut.rstrip() + ("…" if not cut.endswith((".", "!", "?", "…")) else "")
    return text


def generate_seed_comment(post_text: str) -> str:
    """Первый коммент под новым постом — по теме поста."""
    cfg = load_config()
    post = (post_text or "").strip()
    if not post:
        return "Ну что, друзья — кто уже в теме? Кидайте мысли 🔥"
    user = (
        "РЕЖИМ: первый комментарий под постом (seed).\n"
        "Напиши 1–3 предложения строго по теме этого поста, живо, как Вагго.\n\n"
        f"Текст поста:\n{post[:1200]}\n"
    )
    try:
        raw, _ = llm_chat(cfg, SYSTEM_SEED, user, temperature=0.7, prefer_fast=False)
        return _clean_comment_text(raw, 400) or "Ок, пост вышел — что цепляет сильнее всего? 🔥"
    except Exception:
        # короткий фолбэк из начала поста
        line = post.split("\n")[0][:80].replace("<b>", "").replace("</b>", "")
        return f"Поехали 🔥 {line}… кто что думает?"


def _comment_needs_live_facts(text: str) -> bool:
    """Нужен live search (медленно) — только если явно включён comment_search."""
    low = (text or "").lower()
    keys = (
        "когда ",
        "какого числа",
        "дата ",
        "сколько сейчас",
        "курс ",
        "цена ",
        "новост",
        "финал чм",
        "кто выиграл",
        "актуальн",
        "на данный момент",
    )
    if any(k in low for k in keys):
        return True
    if low.startswith(("когда", "сколько", "где проходит", "где будет")):
        return True
    return False


def try_instant_comment(
    comment_text: str,
    *,
    username: str = "",
) -> str | None:
    """
    Мгновенный ответ без Grok (0.05с) для простых реплик.
    None = нужен LLM.
    """
    t = (comment_text or "").strip()
    if not t:
        return None
    low = t.lower()
    hi = ""
    # только супер-короткие / эмоции
    if len(t) <= 40 and any(
        w in low
        for w in (
            "привет",
            "здаров",
            "здарова",
            "хай",
            "hello",
            "йо",
            "ку ",
            "доброе",
            "добрый",
        )
    ):
        return f"{hi}йо 🔥 на связи. Что на уме?"
    if len(t) <= 24 and any(
        w in low for w in ("спасибо", "благодар", "thx", "thanks", "пасиб")
    ):
        return f"{hi}всегда 🔥 заходи ещё"
    if len(t) <= 12 and any(w in t for w in ("🔥", "👍", "❤️", "❤", "💪", "😂", "🤣")):
        return f"{hi}зашло 🔥 а ты сам что думаешь?"
    if len(t) <= 18 and any(
        w in low for w in ("ок", "окей", "ладно", "понял", "ясно", "норм", "ага", "угу")
    ):
        return f"{hi}ок 👍 если что — пиши"
    if len(t) <= 20 and any(
        w in low for w in ("крут", "топ", "класс", "огонь", "имба", "пушка")
    ):
        return f"{hi}огонь 🔥 кидай ещё мысль — разберём"
    return None


SYSTEM_COMMENT_FAST = """Ты Вагго (@Vaggo01) — живой собеседник в комментах.
Ответь коротко (1–3 предложения), по делу, с характером.
Без «отличный вопрос», без «Слышу», без канцелярита, без markdown **.
Только текст реплики."""


def generate_comment_reply(
    comment_text: str,
    *,
    post_context: str = "",
    username: str = "",
) -> str:
    """
    Ответ подписчику. Скорость > энциклопедия:
    instant → fast 4.3 без search → (опц.) search только если comment_search=true.
    """
    cfg = load_config()
    # 0) мгновенно без AI
    if cfg.get("comment_instant_simple", True):
        inst = try_instant_comment(comment_text, username=username)
        if inst:
            print("comment instant (no LLM)", flush=True)
            return inst

    free = bool(cfg.get("comment_free_chat", True))
    max_len = int(cfg.get("comment_max_chars") or (500 if free else 350))
    temp = float(cfg.get("comment_temperature") or 0.55)
    # search в комментах ВЫКЛ по умолчанию (медленно). Вкл: comment_search=true
    allow_search = bool(cfg.get("comment_search", False)) or bool(
        cfg.get("comment_always_search", False)
    )
    needs_facts = allow_search and _comment_needs_live_facts(comment_text)
    prefer_fast = not needs_facts

    user = (
        f"Собеседник: {username or 'человек'}\n"
        f"Написал: {(comment_text or '').strip()}\n"
        "Ответь по его реплике. 1–3 предложения, живо.\n"
    )
    if post_context:
        user += f"Фон поста (не пересказывай): {post_context[:280]}\n"
    try:
        print(
            f"comment path fast={prefer_fast} facts={needs_facts} "
            f"text={(comment_text or '')[:40]!r}",
            flush=True,
        )
        sys_p = SYSTEM_COMMENT_FAST if prefer_fast else SYSTEM_COMMENT
        raw, eng = llm_chat(
            cfg,
            sys_p,
            user,
            temperature=temp,
            prefer_fast=prefer_fast,
            max_tokens=int(cfg.get("comment_max_tokens") or (180 if prefer_fast else 320)),
            tools=bool(needs_facts),
        )
        out = _clean_comment_text(raw, max_len)
        # отсечь «пустые» отписки модели
        bad_markers = (
            "кинь мысль",
            "чуть яснее",
            "нормальный разговор",
            "напиши мысль яснее",
            "продолжим как нормальный",
        )
        low_out = (out or "").lower()
        if out and len(out.strip()) >= 8 and not any(m in low_out for m in bad_markers):
            print(f"comment llm ok engine={eng} len={len(out)}", flush=True)
            return out
        if out:
            print(f"comment llm weak engine={eng}: {out[:80]!r}", flush=True)
    except Exception as e:
        print("comment llm fail", type(e).__name__, str(e)[:160], flush=True)

    # fallback без LLM — умнее шаблонов (облако часто без Grok)
    print("comment fallback (no LLM)", flush=True)
    return _comment_fallback(comment_text, post_context=post_context, username=username)


def _comment_fallback(
    comment_text: str,
    *,
    post_context: str = "",
    username: str = "",
) -> str:
    """Живой запасной ответ, если мозг недоступен (Bothost без ключа и т.п.)."""
    t = (comment_text or "").strip()
    low = t.lower()
    name = (username or "").strip()
    hi = f"{name}, " if name and not name.startswith("?") else ""

    # темы ИИ
    if any(w in low for w in ("chatgpt", "чатгпт", "gpt-4", "gpt4", " gpt")):
        return f"{hi}ChatGPT удобен на быстрый текст/код/план: https://chatgpt.com — что именно пробуешь?"
    if "claude" in low or "клод" in low:
        return f"{hi}Claude часто лучше на длинные тексты: https://claude.ai — сравнишь с тем, что в посте?"
    if "grok" in low or "грок" in low:
        return f"{hi}Grok — https://grok.x.ai · прямой тон и идеи. Чем пользуешься чаще?"
    if "gemini" in low or "джемини" in low or "google ai" in low or "google one" in low:
        return (
            f"{hi}Gemini / Google AI Pro — https://gemini.google.com · "
            "если про розыгрыш — условия в посте и кнопка «Участвовать»."
        )
    if "perplex" in low or "перплекс" in low:
        return f"{hi}Perplexity — https://www.perplexity.ai · факты со ссылками. Что ищешь?"
    if any(w in low for w in ("розыгрыш", "gemini pro", "участв", "приз", "скрины", "скрин")):
        return (
            f"{hi}по розыгрышу: подписка @Vaggo01 + репост другу → скрин в @DirectorVaggobot. "
            "Если бот ругается — кинь скрин ещё раз или напиши владельцу."
        )
    if any(w in low for w in ("заказ", "бот сделай", "сайт", "сколько стоит", "прайс", "цен")):
        return (
            f"{hi}заказы — в @DirectorVaggobot → «Заказать», прайс /prices. "
            "Хостинг/домен отдельно, не в цене."
        )
    if any(w in low for w in ("ссылк", "где открыть", "сайт ", "зайти", "как зайти")):
        return (
            f"{hi}база: chatgpt.com · claude.ai · grok.x.ai · gemini.google.com · "
            "perplexity.ai — что именно нужно?"
        )
    if any(w in low for w in ("привет", "здаров", "здарова", "хай", "ку ", "hello", "йо ")):
        return f"{hi}йо 🔥 на связи. Пиши по делу или в тему поста — разберём."
    if any(w in low for w in ("крут", "огонь", "топ", "класс", "люблю", "огонь", "👍", "🔥")):
        return f"{hi}зашло 🔥 а сам чем из стека чаще всего пользуешься?"
    # спорт / чм — частые короткие вопросы
    if any(w in low for w in ("финал", "чм", "чемпионат мира", "world cup", "месси", "аргентин")):
        return (
            f"{hi}по ЧМ: финал обычно в середине июля в год турнира "
            "(дата плавает — глянь fifa.com / sports). "
            "За кого болеешь — Месси/Аргентина или свой вариант?"
        )
    if "?" in t or any(
        w in low for w in ("как ", "что ", "почему", "зачем", "какой", "можно ли", "когда ")
    ):
        clip = t.replace("\n", " ").strip()
        if len(clip) > 90:
            clip = clip[:87] + "…"
        return (
            f"{hi}по «{clip}» — коротко: зависит от деталей. "
            "Кинь цель (что нужно получить) — разложу по шагам."
        )
    clip = t.replace("\n", " ").strip()
    if len(clip) > 70:
        clip = clip[:67] + "…"
    if clip:
        return (
            f"{hi}понял: «{clip}». "
            "Согласен с постом / свой опыт / вопрос — что из трёх?"
        )
    if post_context:
        return f"{hi}интересный угол. А ты с постом согласен или есть другой взгляд?"
    return f"{hi}на связи. Кинь вопрос или мысль по теме — отвечу по делу."


def rewrite_post(text: str, *, note: str = "") -> str:
    """Переписать готовый пост в стиле канала."""
    cfg = load_config()
    user = f"Перепиши пост в стиле Вагго, сохрани смысл.\n"
    if note:
        user += f"Пожелание: {note}\n"
    user += f"\nИсходник:\n{text}"
    try:
        raw, _ = llm_chat(cfg, SYSTEM_CHANNEL, user, temperature=0.5)
        return format_html_light(raw)
    except Exception:
        return format_html_light(text)


def generate_ideas(count: int = 7, *, rubric: str = "") -> str:
    cfg = load_config()
    count = max(3, min(count, 12))
    user = (
        f"Придумай {count} идей постов для канала Вагго.\n"
        f"Рубрика-фокус: {rubric or 'все рубрики'}.\n"
        "Формат каждой строки:\n"
        "N. [Рубрика] Короткий заголовок — 1 фраза о чём пост\n"
        "Только список, без вступления."
    )
    try:
        raw, _ = llm_chat(cfg, SYSTEM_CHANNEL, user, temperature=0.7, prefer_fast=True)
        return raw
    except Exception:
        base = [
            "🌌 [Вечерний Вагго] Почему скролл убивает глубокие мысли",
            "🤖 [Битва нейросетей] 4 бесплатных ИИ на сегодня — честный тест",
            "💪 [Прокачка] 12 минут дома без зала — схема",
            "⚡️ [Кибер-Лайфхак] Windows: ускорить ПК за 5 кликов",
            "🛠️ [Проект] Как мы пилим своего бота без бюджета",
            "🤖 [Битва нейросетей] Промпт, который пишет посты как человек",
            "🌌 [Вечерний Вагго] Цифровой примитив — короткая вечерняя заметка",
        ]
        return "\n".join(base[:count])


def series_topics() -> list[tuple[str, str]]:
    """Готовый набор (рубрика, тема) на «старт недели»."""
    return [
        ("Вечерний Вагго", "Концентрация в эпоху коротких роликов"),
        ("Битва нейросетей", "Сравнение 3 бесплатных нейросетей для текста"),
        ("Кибер-Лайфхак", "Полезные жесты и скрытые фичи Telegram"),
        ("Прокачка", "Утренняя разминка 10 минут без инвентаря"),
        ("Новости проекта", "Что умеет менеджер канала и как им пользоваться"),
    ]


def week_plan() -> str:
    cfg = load_config()
    brand = (cfg.get("style") or {}).get("brand") or "Вагго"
    lines = [
        f"📅 <b>План канала {brand}</b> (@{cfg.get('channel_username') or 'Vaggo01'})",
        "",
        "По календарю из канала:",
        "• 🌌 <b>Вечерний Вагго</b> — каждый вечер (философия/мистика)",
        "• 🤖 <b>Битва нейросетей</b> — 3× в неделю (тесты + промпты)",
        "• 💪 <b>Прокачка</b> — текст каждые 2 дня, видео 2× в неделю",
        "• ⚡️ <b>Кибер-Лайфхак</b> — часто, короткие фишки",
        "• 🛠️ <b>Проект</b> — бот / Zverki / закулисье по мере готовности",
        "",
        "Команды: /draft · /ideas · /series · /check · /post",
    ]
    return "\n".join(lines)


def status_text(cfg: dict, state: dict) -> str:
    drafts = [d for d in (state.get("drafts") or []) if d.get("status") == "draft"]
    pending = [c for c in (state.get("pending_comments") or []) if c.get("status") == "pending"]
    published = state.get("published") or []
    paused = bool(cfg.get("paused"))
    me_token = "есть" if (cfg.get("bot_token") or "").strip() else "НЕТ — вставь в config.json"
    disc = cfg.get("discussion_group_id") or 0
    need_ok = cfg.get("comment_needs_owner_ok", True)
    auto = bool(cfg.get("auto_reply_comments"))
    auto_mode = "выкл"
    if auto and not need_ok:
        auto_mode = "сразу в комменты"
    elif auto and need_ok:
        auto_mode = "черновик тебе на ок"
    return (
        f"📊 <b>Менеджер Вагго — статус</b>\n\n"
        f"Канал: {cfg.get('channel_id')}\n"
        f"Токен: {me_token}\n"
        f"Группа комментов: {disc or 'не задана'}\n"
        f"Пауза: {'да ⏸' if paused else 'нет ✅'}\n"
        f"Черновиков: {len(drafts)}\n"
        f"Комментов в очереди: {len(pending)}\n"
        f"Опубликовано из пульта/бота: {len(published)}\n"
        f"Режим комментов: {auto_mode}\n"
        f"Мозг: {st_line(cfg)}\n"
    )


def st_line(cfg: dict) -> str:
    st = brain_status(cfg)
    names = {"grok": "Grok API 🚀", "ollama": "Ollama (локально)", "template": "шаблоны ⚠", "none": "нет ⚠"}
    return f"{names.get(st['active'], st['active'])} (mode={st['mode']})"
