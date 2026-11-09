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


# Old model names that now resolve to the same cheapest model (deepseek-flash).
LEGACY_MODELS = {"deepseek-chat": "deepseek-flash", "deepseek-v4-flash": "deepseek-flash"}


def rewrite_with_deepseek(title: str, category_hint: str, max_len: int, raise_errors: bool = False) -> str | None:
    api_key = settings_store.get("deepseek_api_key")
    if not api_key:
        if raise_errors:
            raise RuntimeError("未设置 DeepSeek API Key")
        return None
    base_url = settings_store.get("deepseek_base_url") or settings_store.DEFAULTS["deepseek_base_url"]
    model = settings_store.get("deepseek_model") or settings_store.DEFAULTS["deepseek_model"]
    model = LEGACY_MODELS.get(model, model)
    # Fixed instructions first, product data last: DeepSeek bills a repeated prefix at the much
    # cheaper cache-hit price, so every title after the first reuses the cached instructions.
    system = (
        "你是微信小店商品标题优化助手。改写用户给出的商品标题。要求：保留核心商品词和关键属性；"
        "用词与原标题明显不同；不出现品牌名和广告法禁用词（最、第一、顶级等）；不重复关键词。"
        "只返回 JSON：{\"title\": \"...\"}"
    )
    user = f"不超过{max_len}个汉字。类目：{category_hint or '未知'}\n原标题：{title}"
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                # Thinking mode is on by default and bills hidden reasoning tokens (~7x the cost);
                # title rewriting does not need it.
                "thinking": {"type": "disabled"},
                "temperature": 0.7,  # only honoured in non-thinking mode
                "max_tokens": 200,  # a 30-character title in JSON is ~30 tokens; caps any runaway cost
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


def _room(cfg: TemplateConfig) -> int:
    return max(cfg.title_max_len - len(cfg.title_prefix) - len(cfg.title_suffix), 1)


def _finish(t: str, cfg: TemplateConfig) -> str:
    if cfg.dedupe_title_keywords:
        t = dedupe_keywords(t)
    if cfg.truncate_title:
        t = truncate(t, _room(cfg))
    return t


def generate_title(source_title: str, cfg: TemplateConfig, category_hint: str = "", strict: bool = False) -> str:
    """AI title without prefix/suffix, as shown and edited in the product library.
    strict=True raises when DeepSeek is not configured or fails (used by the 生成标题 button)."""
    t = clean(source_title, cfg)
    new = rewrite_with_deepseek(t, category_hint, max(_room(cfg), 10), raise_errors=strict)
    if new:
        t = clean(new, cfg)
    return _finish(t, cfg)


def build_title(source_title: str, cfg: TemplateConfig, category_hint: str = "", preset: str = "") -> str:
    """Final title for publishing. A title generated or edited in the product library (preset)
    is used as is (only cleaned, deduped and truncated); otherwise DeepSeek is called here."""
    if preset.strip():
        t = _finish(clean(preset, cfg), cfg)
    elif cfg.ai_rewrite_title:
        t = generate_title(source_title, cfg, category_hint)
    else:
        t = _finish(clean(source_title, cfg), cfg)
    return f"{cfg.title_prefix}{t}{cfg.title_suffix}".strip()


def clean_sku_value(value: str, cfg: TemplateConfig) -> str:
    for w in cfg.sku_banned_words:
        if w:
            value = value.replace(w, "")
    return value.strip() or "默认"
