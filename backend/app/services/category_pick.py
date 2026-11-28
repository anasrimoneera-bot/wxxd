"""The WeChat category for a 1688 product listed straight from the 1688 search page (no 商品云库 data
package, so no category of its own).

Candidates: the leaf categories of the category library (一键申请类目 → 同步类目库) whose names share the
most words with the 1688 category (e.g. 服饰配件、饰品>帽子/头巾>帽子) and the title; categories that need
qualifications rank lower. With a DeepSeek key (系统设置) the model picks the best of the top candidates;
without one the best-scoring candidate is used."""
import json
import logging
import re
import time

import httpx
from sqlalchemy import select

from ..models import CategoryInfo
from . import settings_store

log = logging.getLogger(__name__)
_cache: dict = {"at": 0.0, "rows": []}
TOP = 25


def _leaves(db) -> list[tuple[str, str, str, bool]]:
    """(leaf name, path names, path ids, needs qualification), cached for 10 minutes."""
    if time.time() - _cache["at"] > 600 or not _cache["rows"]:
        _cache["rows"] = [(c.name or "", c.path_names or "", c.path or "", bool(c.needs_qualification))
                          for c in db.scalars(select(CategoryInfo).where(CategoryInfo.leaf.is_(True)))]
        _cache["at"] = time.time()
    return _cache["rows"]


def _grams(text: str) -> set[str]:
    """Words of a Chinese text as 2-character pieces (and single ones for very short parts)."""
    out = set()
    for part in re.split(r"[^一-鿿A-Za-z0-9]+", text or ""):
        if len(part) == 1 and "一" <= part <= "鿿":
            out.add(part)
        out.update(part[i:i + 2] for i in range(len(part) - 1))
    return out


def candidates(db, title: str, hint: str, n: int = TOP) -> list[dict]:
    hint_g, title_g = _grams(hint), _grams(title)
    hint_leaf = _grams(re.split(r"[>/／]", hint)[-1]) if hint else set()
    scored = []
    for name, names, ids, qua in _leaves(db):
        if not ids:
            continue
        leaf_g, path_g = _grams(name), _grams(names)
        s = 3 * len(leaf_g & hint_leaf) + 2 * len(leaf_g & (hint_g | title_g)) + len(path_g & hint_g) + 0.5 * len(path_g & title_g)
        if not s:
            continue
        scored.append((s - (1.5 if qua else 0), name, names, ids))
    scored.sort(key=lambda x: -x[0])
    return [{"name": nm, "path_names": pn, "cats": ids.split("/"), "score": s} for s, nm, pn, ids in scored[:n]]


def _ask(title: str, hint: str, cands: list[dict]) -> int | None:
    api_key = settings_store.get("deepseek_api_key")
    if not api_key:
        return None
    from .titles import LEGACY_MODELS
    base_url = settings_store.get("deepseek_base_url") or settings_store.DEFAULTS["deepseek_base_url"]
    model = settings_store.get("deepseek_model") or settings_store.DEFAULTS["deepseek_model"]
    listing = "\n".join(f"{i}. {c['path_names']}" for i, c in enumerate(cands))
    prompt = (f"商品标题：{title}\n1688 类目：{hint or '（无）'}\n\n下面是微信小店的类目，选一个最适合发布这个商品的（必须是商品本身的类目）：\n"
              f"{listing}\n\n只返回 JSON：{{\"index\": 序号}}")
    try:
        r = httpx.post(f"{base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={
            "model": LEGACY_MODELS.get(model, model), "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
            "temperature": 0, "max_tokens": 50}, timeout=30)
        r.raise_for_status()
        i = int(json.loads(r.json()["choices"][0]["message"]["content"]).get("index"))
        return i if 0 <= i < len(cands) else None
    except Exception as e:  # the best-scoring one then
        log.warning("category pick by DeepSeek failed: %s", e)
        return None


def pick(db, title: str, hint: str = "") -> dict | None:
    """{cats: [ids root→leaf], path_names, how} or None when the category library has nothing close."""
    cands = candidates(db, title, hint)
    if not cands:
        return None
    i = _ask(title, hint, cands)
    c = cands[i if i is not None else 0]
    return {"cats": c["cats"], "path_names": c["path_names"], "how": "AI" if i is not None else "关键词"}
