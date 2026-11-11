"""WeChat category library for 一键申请类目, and the 保证金 helper used when publishing.

- 获取所有类目 (/channels/ec/category/all): the whole tree. cats_v2 is the new multi-level tree
  (leaf=true marks a leaf category); cats is the old 3-level tree. Each node also carries its
  类目资质 (qua: need_to_apply / mandatory / tips).
- 获取保证金类目规则 (getcategoryrule, rule_id=1): the leaf's 保证金, unit fen.
  Fallback: 获取类目信息 (category/detail) also has a deposit field, unit fen.
The library is the same for every store, so it is stored once (category_info) and refreshed
by a background sync (catalog_sync row id=1).
"""
import logging
import time
from datetime import datetime, timedelta

from ..db import SessionLocal
from ..models import CatalogSync, CategoryInfo

log = logging.getLogger(__name__)
STALE_DAYS = 7
PAUSE = 0.1  # seconds between deposit calls, to stay well under WeChat's rate limits


# ------------------------------------------------------------------ parsing
def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def find_deposit(d) -> int | None:
    """保证金 (fen) in a getcategoryrule or category/detail response. The documented field is
    `deposit`; it is searched at any depth (first match), since the wrapping object differs."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k.lower() in ("deposit", "deposit_amount", "category_deposit") and _num(v) is not None:
                return _num(v)
        for v in d.values():
            found = find_deposit(v)
            if found is not None:
                return found
    elif isinstance(d, list):
        for v in d:
            found = find_deposit(v)
            if found is not None:
                return found
    return None


_find_deposit = find_deposit  # used by the publisher


def _nodes(raw: dict):
    """All {cat, qua} items of the tree; cats_v2 when present (new tree), else cats."""
    groups = raw.get("cats_v2") or raw.get("cats") or []
    for g in groups:
        items = g.get("cat_and_qua") if isinstance(g, dict) and "cat_and_qua" in g else [g]
        for it in items if isinstance(items, list) else [items]:
            if isinstance(it, dict) and isinstance(it.get("cat"), dict):
                yield it


def parse_tree(raw: dict) -> list[dict]:
    """Leaf categories with their path (ids and names, root to leaf) and qualification need."""
    nodes = {}
    for it in _nodes(raw):
        c = it["cat"]
        cid = str(c.get("cat_id") or "")
        if not cid:
            continue
        q = it.get("qua") or {}
        nodes[cid] = {"cat_id": cid, "name": c.get("name") or "", "parent": str(c.get("f_cat_id") or "0"),
                      "level": c.get("level"), "leaf": c.get("leaf"),
                      "needs_qualification": bool(q.get("need_to_apply") or (q.get("qua_id") and q.get("mandatory"))),
                      "qua_tips": (q.get("tips") or "")[:255]}
    parents = {n["parent"] for n in nodes.values()}
    leaves = []
    for cid, n in nodes.items():
        is_leaf = n["leaf"] if n["leaf"] is not None else cid not in parents
        if not is_leaf:
            continue
        ids, names, cur, guard = [], [], n, 0
        while cur and guard < 10:
            ids.insert(0, cur["cat_id"])
            names.insert(0, cur["name"])
            cur = nodes.get(cur["parent"])
            guard += 1
        leaves.append({**n, "path": ids, "path_names": names})
    return leaves


# ------------------------------------------------------------------ sync
def status(db) -> dict:
    s = db.get(CatalogSync, 1)
    total_leaves = db.query(CategoryInfo).filter(CategoryInfo.leaf.is_(True)).count()
    with_deposit = db.query(CategoryInfo).filter(CategoryInfo.leaf.is_(True), CategoryInfo.deposit.is_not(None)).count()
    if not s:
        return {"status": "", "total": 0, "done": 0, "message": "", "leaves": total_leaves,
                "with_deposit": with_deposit, "finished_at": None}
    running = s.status == "running" and s.started_at and s.started_at > datetime.now() - timedelta(hours=3)
    return {"status": "running" if running else ("failed" if s.status == "running" else s.status),
            "total": s.total, "done": s.done, "message": s.message, "leaves": total_leaves,
            "with_deposit": with_deposit,
            "finished_at": s.finished_at.strftime("%Y-%m-%d %H:%M:%S") if s.finished_at else None}


def start(db) -> bool:
    """Mark the sync as running; False if one is already running."""
    s = db.get(CatalogSync, 1)
    if s and status(db)["status"] == "running":
        return False
    if not s:
        s = CatalogSync(id=1)
        db.add(s)
    s.status, s.total, s.done, s.message = "running", 0, 0, "读取类目树…"
    s.started_at, s.finished_at = datetime.now(), None
    db.commit()
    return True


def deposit_of(client, cat_id: str) -> tuple[int | None, str]:
    try:
        d = find_deposit(client.category_deposit_rule(cat_id))
        if d is not None:
            return d, "rule"
    except Exception as e:
        log.info("getcategoryrule %s: %s", cat_id, e)
    try:
        d = find_deposit(client.category_detail(cat_id))
        if d is not None:
            return d, "detail"
    except Exception as e:
        log.info("category detail %s: %s", cat_id, e)
    return None, ""


def run_sync(client, full: bool = False) -> None:
    """Background job: store the tree, then the 保证金 of every leaf (missing or stale ones unless full)."""
    with SessionLocal() as db:
        s = db.get(CatalogSync, 1)
        try:
            leaves = parse_tree(client.all_categories())
            if not leaves:
                raise RuntimeError("微信返回的类目列表为空")
            existing = {c.cat_id: c for c in db.query(CategoryInfo).all()}
            for leaf in leaves:
                row = existing.get(leaf["cat_id"])
                if row is None:
                    row = CategoryInfo(cat_id=leaf["cat_id"], fetched_at=datetime(2000, 1, 1))
                    db.add(row)
                    existing[row.cat_id] = row
                row.name, row.leaf = leaf["name"][:128], True
                row.path, row.path_names = "/".join(leaf["path"])[:255], " / ".join(leaf["path_names"])[:512]
                row.needs_qualification, row.qua_tips = leaf["needs_qualification"], leaf["qua_tips"]
            leaf_ids = {leaf["cat_id"] for leaf in leaves}
            for cid, row in existing.items():  # no longer a leaf (or removed by WeChat)
                if cid not in leaf_ids:
                    row.leaf = False
            db.commit()
            stale = datetime.now() - timedelta(days=STALE_DAYS)
            todo = [existing[c] for c in leaf_ids
                    if full or existing[c].deposit is None or existing[c].fetched_at < stale]
            s.total, s.done, s.message = len(todo), 0, f"共 {len(leaves)} 个叶子类目，读取保证金 {len(todo)} 个…"
            db.commit()
            for i, row in enumerate(todo, 1):
                row.deposit, row.deposit_source = deposit_of(client, row.cat_id)
                row.fetched_at = datetime.now()
                s.done = i
                if i % 20 == 0:
                    db.commit()
                time.sleep(PAUSE)
            s.status, s.finished_at = "done", datetime.now()
            s.message = f"完成：{len(leaves)} 个叶子类目"
            db.commit()
        except Exception as e:
            log.exception("category catalog sync failed")
            db.rollback()
            s = db.get(CatalogSync, 1)
            s.status, s.finished_at, s.message = "failed", datetime.now(), f"同步失败：{e}"[:500]
            db.commit()
