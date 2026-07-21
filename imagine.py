"""
Grok Imagine: фото и видео через xAI API (сессия Super / xai_api_key).
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from content import _grok_bearer
from state import load_config

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"
MEDIA.mkdir(exist_ok=True)

IMG_MODEL = "grok-imagine-image"
IMG_MODEL_HQ = "grok-imagine-image-quality"
VID_MODEL = "grok-imagine-video"


def _headers(cfg: dict) -> dict:
    token, src = _grok_bearer(cfg)
    if not token:
        raise RuntimeError("Нет Grok: grok login или xai_api_key")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Grok-Source": src,
    }


def _base(cfg: dict) -> str:
    return (cfg.get("xai_base_url") or "https://api.x.ai/v1").rstrip("/")


def generate_image(
    prompt: str,
    *,
    quality: bool = False,
    cfg: dict | None = None,
    reference: Path | str | None = None,
) -> Path:
    """Text → image file (jpeg). Optional reference image (mascot etc.)."""
    import base64

    cfg = cfg or load_config()
    # по умолчанию HQ (новая quality-модель), если в конфиге не задано иное
    default_model = IMG_MODEL_HQ if quality or cfg.get("imagine_quality_default", True) else IMG_MODEL
    model = (cfg.get("imagine_image_model") or default_model)
    url = f"{_base(cfg)}/images/generations"
    payload: dict = {
        "model": model,
        "prompt": prompt.strip(),
        "n": 1,
    }
    # ВСЕГДА цепляем оригинал маскота (как в старых нормальных постах)
    ref = reference
    if ref is None and cfg.get("imagine_use_mascot", True):
        for cand in (
            ROOT / "media" / "mascot_admin_pc_1.jpg",  # проверенный стиль постов
            ROOT / "media" / "vaggo_avatar_og.jpg",
            ROOT / "media" / "ref_mascot.jpg",
        ):
            if cand.is_file():
                ref = cand
                break
    if ref:
        ref_path = Path(ref)
        if ref_path.is_file():
            b64 = base64.b64encode(ref_path.read_bytes()).decode()
            payload["images"] = [f"data:image/jpeg;base64,{b64}"]
            # подсказка модели: не уезжать от рефа
            payload["prompt"] = (
                "Match the reference character EXACTLY (same face, body, outline style, colors). "
                "Only change scene as described. "
                + payload["prompt"]
            )
    resp = requests.post(url, headers=_headers(cfg), json=payload, timeout=150)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"Imagine auth {resp.status_code} — grok login")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"Пустой ответ Imagine: {data}")
    img_url = items[0].get("url")
    if not img_url:
        raise RuntimeError(f"Нет url картинки: {items[0]}")
    return _download(img_url, suffix=".jpg")


def generate_video(prompt: str, *, cfg: dict | None = None, timeout_sec: int = 180) -> Path:
    """Text → video file (mp4), polling."""
    cfg = cfg or load_config()
    model = cfg.get("imagine_video_model") or VID_MODEL
    start = requests.post(
        f"{_base(cfg)}/videos/generations",
        headers=_headers(cfg),
        json={"model": model, "prompt": prompt.strip()},
        timeout=60,
    )
    if start.status_code in (401, 403):
        raise RuntimeError(f"Video auth {start.status_code} — grok login")
    start.raise_for_status()
    rid = start.json().get("request_id")
    if not rid:
        raise RuntimeError(f"Нет request_id: {start.text[:200]}")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        st = requests.get(
            f"{_base(cfg)}/videos/{rid}",
            headers=_headers(cfg),
            timeout=30,
        )
        if st.status_code == 202:
            time.sleep(3)
            continue
        if st.status_code != 200:
            raise RuntimeError(f"Video status {st.status_code}: {st.text[:200]}")
        body = st.json()
        if body.get("status") == "done":
            vurl = (body.get("video") or {}).get("url")
            if not vurl:
                raise RuntimeError(f"done без url: {body}")
            return _download(vurl, suffix=".mp4")
        if body.get("status") in ("failed", "error"):
            raise RuntimeError(f"Video failed: {body}")
        time.sleep(3)
    raise RuntimeError("Video timeout — подожди и попробуй ещё")


def _download(url: str, *, suffix: str) -> Path:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    name = f"imagine_{int(time.time())}_{abs(hash(url)) % 10**8}{suffix}"
    path = MEDIA / name
    path.write_bytes(r.content)
    return path


def style_prompt_for_channel(topic: str, rubric: str = "") -> str:
    """Промпт картинки под Вагго + маскот (без текста на картинке).

    Правило бренда: персонаж всегда flat 2D; обстановка может быть с глубиной.
    """
    rub = rubric or "tech / self-improvement"
    return (
        f"CHARACTER must be pure flat 2D cartoon like the reference: bold outlines, "
        f"flat cel colors, NOT 3D, NOT CGI, NOT plastic Pixar render. "
        f"Same cute white round-headed mascot (teal vein lines on white shirt, blue pants, "
        f"simple friendly teal eyes). "
        f"BODY: average balanced cartoon proportions — NOT skinny, NOT fat, NOT chubby. "
        f"Theme: {topic}. Mood/rubric: {rub}. "
        f"Background/environment may have soft depth and lighting, but the character stays 2D. "
        f"Premium social media cover, character clearly visible, no text, no watermark, no logo."
    )
