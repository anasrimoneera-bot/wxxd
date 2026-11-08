"""Title rules: keyword replacement, banned words, DeepSeek rewrite, keyword dedupe,
prefix/suffix and truncation at a word boundary."""
import json
import logging
import re

import httpx
import jieba

from . import settings_store
from .template_config import TemplateConfig

log = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)

# Words banned by the Advertising Law (广告法) that commonly cause rejections.
AD_LAW_WORDS = ["最", "第一", "国家级", "顶级", "极品", "首选", "独家", "万能", "100%", "绝对", "史无前例", "全网最低"]
_SYMBOLS = re.compile(r"[^\w一-鿿%/+\-.· ]+")


def clean(title: str, cfg: TemplateConfig) -> str:
    t = title
    for r in cfg.keyword_replacements:
        if r.source:
            t = t.replace(r.source, r.target)
    for w in cfg.title_banned_words + AD_LAW_WORDS:
        if w:
            t = t.replace(w, "")
    t = _SYMBOLS.sub(" ", t)
    t = re.sub(r"(?<=[一-鿿])[A-Za-z]$", "", t.strip())  # trailing shop code like "…清洁刷K"
    return re.sub(r"\s+", " ", t).strip()


def dedupe_keywords(title: str) -> str:
    seen: set[str] = set()
    out = []
    for w in jieba.cut(title):
        key = w.strip().lower()
        if not key:
            out.append(w)
            continue
        if len(key) > 1 and key in seen:
            continue
        seen.add(key)
        out.append(w)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def truncate(title: str, max_len: int) -> str:
    if len(title) <= max_len:
        return title
    out = ""
    for w in jieba.cut(title):
        if len(out) + len(w) > max_len:
            break
        out += w
    return (out or title[:max_len]).strip()


def short_title(title: str, max_len: int = 10) -> str:
    words = [w for w in jieba.cut(title) if len(w.strip()) > 1]
    out = ""
    for w in words:
        if len(out) + len(w) > max_len:
            break
        out += w
    return out or title[:max_len]


def rewrite_with_deepseek(title: str, category_hint: str, max_len: int, raise_errors: bool = False) -> str | None:
    api_key = settings_store.get("deepseek_api_key")
    if not api_key:
        if raise_errors:
            raise RuntimeError("未设置 DeepSeek API Key")
        return None
    base_url = settings_store.get("deepseek_base_url") or settings_store.DEFAULTS["deepseek_base_url"]
    model = settings_store.get("deepseek_model") or settings_store.DEFAULTS["deepseek_model"]
    prompt = (
        f"你是微信小店商品标题优化助手。请改写下面的商品标题：\n原标题：{title}\n类目：{category_hint or '未知'}\n"
        f"要求：保留核心商品词和关键属性；用词与原标题明显不同；不出现品牌名和广告法禁用词（最、第一、顶级等）；"
        f"不重复关键词；不超过{max_len}个汉字。只返回 JSON：{{\"title\": \"...\"}}"
    )
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            },
            timeout=30,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        new = (json.loads(content).get("title") or "").strip()
        return new or None
    except Exception as e:  # never block publishing on the model
        log.warning("DeepSeek rewrite failed: %s", e)
        if raise_errors:
            raise
        return None


def build_title(source_title: str, cfg: TemplateConfig, category_hint: str = "") -> str:
    t = clean(source_title, cfg)
    if cfg.ai_rewrite_title:
        room = max(cfg.title_max_len - len(cfg.title_prefix) - len(cfg.title_suffix), 10)
        new = rewrite_with_deepseek(t, category_hint, room)
        if new:
            t = clean(new, cfg)
    if cfg.dedupe_title_keywords:
        t = dedupe_keywords(t)
    if cfg.truncate_title:
        room = max(cfg.title_max_len - len(cfg.title_prefix) - len(cfg.title_suffix), 1)
        t = truncate(t, room)
    return f"{cfg.title_prefix}{t}{cfg.title_suffix}".strip()


def clean_sku_value(value: str, cfg: TemplateConfig) -> str:
    for w in cfg.sku_banned_words:
        if w:
            value = value.replace(w, "")
    return value.strip() or "默认"
