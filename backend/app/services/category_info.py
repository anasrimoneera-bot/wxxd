"""WeChat category details used by 一键申请类目 and by automatic applications when publishing."""
from datetime import datetime, timedelta

from ..models import CategoryInfo

CACHE_DAYS = 7


def _find_deposit(d) -> int | None:
    """保证金 in fen. The response layout is not documented clearly, so look where it may be:
    info.deposit, attr.deposit, top-level deposit (first number found)."""
    for part in (d.get("info") or {}, d.get("attr") or {}, d):
        v = part.get("deposit") if isinstance(part, dict) else None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def _needs_qualification(d) -> bool:
    """A category that needs 准入/资质 cannot be applied for without uploading documents."""
    attr = d.get("attr") or {}
    if attr.get("access_permit_required"):
        return True
    qua = d.get("qua") or (d.get("info") or {}).get("qua") or {}
    if isinstance(qua, dict) and (qua.get("need_to_apply") or qua.get("mandatory")):
        return True
    return False


def parse_detail(cat_id: str, d: dict) -> dict:
    return {"cat_id": str(cat_id), "name": (d.get("info") or {}).get("name") or "",
            "deposit": _find_deposit(d), "needs_qualification": _needs_qualification(d)}


def info_out(c: CategoryInfo | None) -> dict | None:
    if not c:
        return None
    return {"cat_id": c.cat_id, "name": c.name, "deposit": c.deposit, "needs_qualification": c.needs_qualification}


def get_infos(db, client, cat_ids: list[str], refresh: bool = False) -> dict[str, dict]:
    """Category details from the cache, fetching (and caching) the missing or stale ones."""
    out: dict[str, dict] = {}
    stale = datetime.now() - timedelta(days=CACHE_DAYS)
    for cid in dict.fromkeys(str(c) for c in cat_ids):
        row = db.get(CategoryInfo, cid)
        if row and not refresh and row.fetched_at > stale:
            out[cid] = info_out(row)
            continue
        try:
            p = parse_detail(cid, client.category_detail(cid))
        except Exception as e:  # keep the old value if there is one
            out[cid] = info_out(row) or {"cat_id": cid, "name": "", "deposit": None,
                                         "needs_qualification": False, "error": str(e)[:200]}
            continue
        if row is None:
            row = CategoryInfo(cat_id=cid)
            db.add(row)
        row.name, row.deposit, row.needs_qualification = p["name"][:128], p["deposit"], p["needs_qualification"]
        row.fetched_at = datetime.now()
        out[cid] = info_out(row)
    db.commit()
    return out
