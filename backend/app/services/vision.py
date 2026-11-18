"""视觉 AI (Gemini): is a 1688 candidate the same product as ours?

One request per check: our main image + title, then the candidates' images + titles; Gemini answers
in JSON with, per candidate, verdict 同款 / 相似 / 不同, a confidence 0-100 and a short reason.
Gemini API: POST {base}/v1beta/models/{model}:generateContent?key=KEY, images as inline_data.
"""
import base64
import io
import json
import logging

import httpx
from PIL import Image

from . import settings_store

log = logging.getLogger(__name__)
BASE_URL = "https://generativelanguage.googleapis.com"
MAX_CANDIDATES = 20
IMAGE_SIDE = 512  # px: enough to judge the product, small enough to keep the request cheap

PROMPT = (
    "你是电商找货源助手。第 1 张图是我店铺里的商品（标题：{title}）。后面的图依次是 1688 上的候选货源，"
    "编号和标题如下：\n{cands}\n"
    "判断每个候选是不是和我的商品同一款（同一个工厂款式：外观、结构、材质、图案一致；颜色或拍摄角度不同、"
    "带水印或文字不影响）。verdict 只能是：同款、相似（同类但不是同一款）、不同。confidence 是 0-100 的把握。"
    "reason 用一句中文说明依据。只返回 JSON：{{\"results\": [{{\"key\": \"编号\", \"verdict\": \"同款\", "
    "\"confidence\": 90, \"reason\": \"...\"}}]}}"
)


class VisionError(Exception):
    pass


def configured() -> bool:
    return bool(settings_store.get("gemini_api_key"))


def _image_part(data: bytes) -> dict:
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=85)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(out.getvalue()).decode()}}


def _download(url: str) -> bytes:
    from .wechat import download
    if url.startswith("//"):
        url = "https:" + url
    return download(url, timeout=20)


def compare(main_image: str, title: str, candidates: list[dict], http: httpx.Client | None = None) -> list[dict]:
    """candidates: [{key, image_url, title}] -> [{key, verdict, confidence, reason}] (same order)."""
    key = settings_store.get("gemini_api_key")
    if not key:
        raise VisionError("未设置 Gemini API Key：请管理员在「系统设置 → 视觉 AI」中填写")
    model = settings_store.get("gemini_model") or "gemini-2.5-flash"
    candidates = candidates[:MAX_CANDIDATES]
    parts, lines, usable = [], [], []
    try:
        parts.append(_image_part(_download(main_image)))
    except Exception as e:
        raise VisionError(f"读取商品主图失败：{e}")
    for c in candidates:
        try:
            parts.append(_image_part(_download(c["image_url"])))
        except Exception as e:  # an image that cannot be read is reported, not fatal
            log.info("candidate image %s: %s", c.get("image_url"), e)
            continue
        usable.append(c)
        lines.append(f"{len(usable) + 1}. 编号 {c['key']}：{(c.get('title') or '')[:80]}")
    if not usable:
        raise VisionError("候选货源的图片都读取不到")
    parts.insert(0, {"text": PROMPT.format(title=title[:120], cands="\n".join(lines))})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    client = http or httpx.Client(timeout=90)
    try:
        r = client.post(f"{BASE_URL}/v1beta/models/{model}:generateContent", params={"key": key}, json=body)
    except httpx.HTTPError as e:
        raise VisionError(f"连接 Gemini 失败：{e}")
    if r.status_code != 200:
        msg = r.text[:300]
        try:
            msg = r.json().get("error", {}).get("message", msg)
        except Exception:
            pass
        raise VisionError(f"Gemini 返回错误（{r.status_code}）：{msg}")
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        rows = json.loads(text).get("results") or []
    except Exception as e:
        raise VisionError(f"Gemini 返回内容无法解析：{e}")
    by_key = {str(x.get("key")): x for x in rows if isinstance(x, dict)}
    out = []
    for c in candidates:
        x = by_key.get(str(c["key"]))
        if x is None:
            out.append({"key": c["key"], "verdict": "未判断", "confidence": 0,
                        "reason": "图片读取失败" if c not in usable else "AI 没有给出结果"})
            continue
        verdict = x.get("verdict") if x.get("verdict") in ("同款", "相似", "不同") else "相似"
        try:
            conf = max(0, min(100, int(float(x.get("confidence") or 0))))
        except (TypeError, ValueError):
            conf = 0
        out.append({"key": c["key"], "verdict": verdict, "confidence": conf, "reason": str(x.get("reason") or "")[:200]})
    return out
