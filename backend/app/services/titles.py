"""Title rules: keyword replacement, banned words, DeepSeek rewrite, keyword dedupe,
prefix/suffix and truncation at a word boundary."""
import json
import logging
import re
import time

import httpx
import jieba

from . import settings_store
from .template_config import TemplateConfig

log = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)

# Words banned by the Advertising Law (广告法) that commonly cause rejections.
AD_LAW_WORDS = ["最", "第一", "国家级", "顶级", "极品", "首选", "独家", "万能", "100%", "绝对", "史无前例", "全网最低"]
_SYMBOLS = re.compile(r"[^\w一-鿿%/+\-.· ]+")


_shared: dict = {"at": 0.0, "words": []}


def shared_banned_words() -> list[str]:
    """标题违禁词 shared by every account: those in the 上货模板 of the platform admin's account (cached 1 min)."""
    if time.time() - _shared["at"] > 60:
        try:
            from sqlalchemy import select

            from ..db import SessionLocal
            from ..models import PublishTemplate, User
            with SessionLocal() as db:
                admins = select(User.tenant_id).where(User.is_admin.is_(True))
                cfgs = db.scalars(select(PublishTemplate.config).where(PublishTemplate.tenant_id.in_(admins))).all()
            _shared["words"] = list(dict.fromkeys(w for c in cfgs for w in (c or {}).get("title_banned_words") or [] if w))
        except Exception as e:  # keep the last list
            log.warning("shared banned words not read: %s", e)
        _shared["at"] = time.time()
    return _shared["words"]


def clean(title: str, cfg: TemplateConfig) -> str:
    t = title
    for r in cfg.keyword_replacements:
        if r.source:
            t = t.replace(r.source, r.target)
    for w in sorted(set(cfg.title_banned_words + shared_banned_words() + AD_LAW_WORDS), key=len, reverse=True):
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


WECHAT_TITLE_MAX = 60  # 微信小店 商品标题 at most 60 characters


def min_len_for(max_len: int) -> int:
    """AI titles should nearly fill the room: at least ~90% of it (54 of 60, 26 of 30)."""
    return max(min(max_len, 5), max_len - max(3, round(max_len * 0.1)))


def rewrite_with_deepseek(title: str, category_hint: str, max_len: int, raise_errors: bool = False,
                          avoid: list[str] | None = None, attrs: str = "") -> str | None:
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
        "用词与原标题明显不同；不出现品牌名和广告法禁用词（最、第一、顶级等）；不重复关键词；"
        "标题要写满规定的字数：用原标题、类目和商品属性里真实有的信息（材质、规格、尺寸、适用人群、"
        "使用场景、风格、功能卖点）把标题补充完整，不编造参数，不堆砌无关词，不用标点符号分隔堆词。"
        "只返回 JSON：{\"title\": \"...\"}"
    )
    min_len = min_len_for(max_len)
    user = (f"标题长度必须在{min_len}到{max_len}个字之间（越接近{max_len}个字越好，绝对不能超过{max_len}个字）。"
            f"类目：{category_hint or '未知'}\n原标题：{title}")
    if attrs:
        user += f"\n商品属性：{attrs[:300]}"
    if avoid:  # the same product in other stores: each store gets its own title
        user += "\n已在其他店铺使用的标题（新标题必须与它们明显不同）：\n" + "\n".join(avoid[:10])
    def ask(messages):
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
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
        return (json.loads(content).get("title") or "").strip(), content

    try:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        new, content = ask(messages)
        if new and len(new) < min_len:  # too short: once more, saying so
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"这个标题只有{len(new)}个字，太短了。请补充真实的卖点词，"
                                                     f"写到{min_len}到{max_len}个字。只返回 JSON。"}]
            again, _ = ask(messages)
            if again and len(again) > len(new):
                new = again
        if new and len(new) > max_len:
            new = truncate(new, max_len)
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


def generate_title(source_title: str, cfg: TemplateConfig, category_hint: str = "", strict: bool = False,
                   avoid: list[str] | None = None, attrs: str = "") -> str:
    """AI title without prefix/suffix, as shown and edited in the product library, nearly as long as
    the template allows (at most WeChat's 60). strict=True raises when DeepSeek is not configured or
    fails (used by the 生成标题 button)."""
    t = clean(source_title, cfg)
    new = rewrite_with_deepseek(t, category_hint, max(min(_room(cfg), WECHAT_TITLE_MAX), 10), raise_errors=strict,
                                avoid=avoid, attrs=attrs)
    if new:
        t = clean(new, cfg)
    return _finish(t, cfg)


def _key(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t or "").lower()


def reorder(t: str, n: int) -> str:
    """The same words in another order (used when no new title can be generated): the word list is
    rotated by n, so every store gets a different order."""
    words = [w for w in jieba.cut(t) if w.strip()]
    if len(words) < 2:
        return t
    k = n % len(words) or 1
    return "".join(words[k:] + words[:k])


def build_title(source_title: str, cfg: TemplateConfig, category_hint: str = "", preset: str = "",
                used: list[str] | None = None) -> str:
    """Final title for publishing.
    - First store: a title generated or edited in the product library (preset) is used as is (only
      cleaned, deduped and truncated); otherwise DeepSeek is called here (when the template asks).
    - Every further store (used = titles this product already has in other stores): a new title is
      generated, different from all of them; without DeepSeek the words are put in another order."""
    used = [u for u in (used or []) if u]
    taken = {_key(u) for u in used}

    def full(t: str) -> str:
        return f"{cfg.title_prefix}{t}{cfg.title_suffix}".strip()

    if not used:  # first store
        if preset.strip():
            return full(_finish(clean(preset, cfg), cfg))
        if cfg.ai_rewrite_title:
            return full(generate_title(source_title, cfg, category_hint))
        return full(_finish(clean(source_title, cfg), cfg))
    # another store already has this product: always a new title, unlike every one in use
    t = _finish(clean(preset or source_title, cfg), cfg)
    for _ in range(2):
        new = generate_title(source_title, cfg, category_hint, avoid=used)
        if _key(full(new)) not in taken:
            return full(new)
    base = t
    for i in range(1, 8):
        t = _finish(reorder(base, len(used) + i - 1), cfg)
        if _key(full(t)) not in taken:
            return full(t)
    return full(t)


def clean_sku_value(value: str, cfg: TemplateConfig) -> str:
    for w in cfg.sku_banned_words:
        if w:
            value = value.replace(w, "")
    return value.strip() or "默认"
