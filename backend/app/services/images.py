"""Image list rules and main-image watermark styles."""
import io
import os

from PIL import Image, ImageDraw, ImageFont

from .template_config import DeleteRange, Watermark

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def apply_delete(imgs: list[str], rule: DeleteRange, keep_min: int = 1) -> list[str]:
    if not rule.enabled:
        return imgs
    end = len(imgs) - rule.back if rule.back else len(imgs)
    out = imgs[rule.front:end]
    return out if len(out) >= keep_min else imgs[:keep_min]


def _font(size: int):
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def add_watermark(data: bytes, wm: Watermark, fallback_text: str) -> bytes:
    text = (wm.text or fallback_text or "").strip()[:10]
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if not text:
        return data
    w, h = img.size
    alpha = max(5, min(100, wm.opacity)) * 255 // 100
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    style = wm.style

    if style == "center_light":  # 居中浅印
        f = _font(max(w // 9, 16))
        box = d.textbbox((0, 0), text, font=f)
        d.text(((w - box[2]) / 2, (h - box[3]) / 2), text, font=f, fill=(255, 255, 255, alpha))
    elif style == "tiled":  # 平铺暗纹
        f = _font(max(w // 22, 12))
        tile = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        step_x, step_y = max(w // 4, 60), max(h // 6, 40)
        for y in range(0, h * 2, step_y):
            for x in range(0, w * 2, step_x):
                td.text((x, y), text, font=f, fill=(128, 128, 128, alpha))
        tile = tile.rotate(30, expand=False)
        layer = tile.crop((w // 2, h // 2, w // 2 + w, h // 2 + h))
    elif style == "half_label":  # 半透标签
        f = _font(max(w // 20, 14))
        box = d.textbbox((0, 0), text, font=f)
        pad = 10
        x, y = w - box[2] - pad * 3, h - box[3] - pad * 3
        d.rounded_rectangle((x - pad, y - pad, x + box[2] + pad, y + box[3] + pad), radius=8, fill=(0, 0, 0, max(alpha, 90)))
        d.text((x, y), text, font=f, fill=(255, 255, 255, 230))
    elif style == "outline_badge":  # 描边徽标
        f = _font(max(w // 18, 14))
        box = d.textbbox((0, 0), text, font=f)
        x, y = 20, 20
        d.rounded_rectangle((x - 8, y - 8, x + box[2] + 8, y + box[3] + 8), radius=10, outline=(255, 255, 255, 230), width=3)
        d.text((x, y), text, font=f, fill=(255, 255, 255, 230), stroke_width=2, stroke_fill=(0, 0, 0, alpha))
    elif style == "vertical_corner":  # 竖排角标
        f = _font(max(w // 22, 12))
        y = 20
        for ch in text:
            box = d.textbbox((0, 0), ch, font=f)
            d.text((w - box[2] - 20, y), ch, font=f, fill=(255, 255, 255, max(alpha, 160)), stroke_width=1, stroke_fill=(0, 0, 0, 120))
            y += box[3] + 4
    else:  # simple_text 简约文字
        f = _font(max(w // 24, 12))
        box = d.textbbox((0, 0), text, font=f)
        d.text((w - box[2] - 16, h - box[3] - 16), text, font=f, fill=(255, 255, 255, max(alpha, 160)))

    out = Image.alpha_composite(img, layer).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


MAX_UPLOAD_BYTES = 2 * 1024 * 1024 - 64 * 1024  # stay safely under WeChat's 2 MB image limit


def normalize_for_upload(data: bytes) -> bytes:
    """Convert any source image (webp, png with alpha, huge jpg) to a JPEG under the size limit."""
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    if max(img.size) > 3000:
        img.thumbnail((3000, 3000))
    for quality in (90, 82, 74, 66, 58):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= MAX_UPLOAD_BYTES:
            return buf.getvalue()
        if quality == 74:
            img.thumbnail((int(img.width * 0.8), int(img.height * 0.8)))
    return buf.getvalue()
