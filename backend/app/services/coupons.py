"""Store coupons (营销管理 · 优惠券) for the 到手价 of products and the 改价 formula.

获取优惠券ID列表 (/channels/ec/coupon/get_list) + 获取优惠券详情 (/channels/ec/coupon/get).
Coupon types: 1 商品条件折扣券, 2 商品满减券, 3 商品统一折扣券, 4 商品直减券, 101-104 the same for the
whole store. The detail's field layout is not fully documented, so values are searched by name at
any depth (discount_fee, discount_num, product_price / threshold, product_ids) and the raw detail
is kept (原始数据) to check them against a real store.
"""
import logging
from datetime import datetime

from sqlalchemy import delete, select

from ..models import Store, StoreCoupon
from ..security import decrypt_secret
from ..timeutil import cn_time

log = logging.getLogger(__name__)
ACTIVE_STATUS = 2  # 生效中 (VERIFY against a real store)
TYPE_NAMES = {1: "商品条件折扣券", 2: "商品满减券", 3: "商品统一折扣券", 4: "商品直减券",
              101: "店铺条件折扣券", 102: "店铺满减券", 103: "店铺统一折扣券", 104: "店铺直减券"}


def _find(d, *keys):
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] not in (None, "", []):
                return d[k]
        for v in d.values():
            got = _find(v, *keys)
            if got is not None:
                return got
    elif isinstance(d, list):
        for v in d:
            got = _find(v, *keys)
            if got is not None:
                return got
    return None


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_coupon(resp: dict) -> dict:
    c = resp.get("coupon") or resp
    info = c.get("coupon_info") or {}
    end = _int(_find(info, "end_time", "valid_end_time"))
    return {
        "coupon_id": str(c.get("coupon_id") or ""),
        "name": str(info.get("name") or c.get("name") or "")[:128],
        "type": _int(c.get("type")), "status": _int(c.get("status")),
        "discount_fee": _int(_find(info, "discount_fee")),
        "discount_num": _int(_find(info, "discount_num")),
        "threshold": _int(_find(info, "product_price", "threshold", "least_cost")),
        "product_ids": [str(x) for x in (_find(info, "product_ids") or [])],
        "end_time": cn_time(end) if end > 0 else None,
    }


def sync_store_coupons(db, store: Store, client) -> int:
    """Replace the store's coupons with its active ones. Returns how many."""
    ids, page, ctx = [], 1, ""
    while True:
        d = client.list_coupons(ACTIVE_STATUS, page, ctx)
        batch = [str(c.get("coupon_id")) for c in d.get("coupons") or [] if c.get("coupon_id")]
        ids.extend(batch)
        ctx = d.get("page_ctx") or ""
        if len(batch) < 200 or page >= 20:
            break
        page += 1
    db.execute(delete(StoreCoupon).where(StoreCoupon.store_id == store.id))
    for cid in dict.fromkeys(ids):
        try:
            resp = client.get_coupon(cid)
        except Exception as e:  # one bad coupon must not stop the rest
            log.warning("coupon %s: %s", cid, e)
            continue
        data = parse_coupon(resp)
        data["coupon_id"] = data["coupon_id"] or cid
        db.add(StoreCoupon(store_id=store.id, raw=resp, **data))
    db.flush()
    return len(ids)


def sync_coupons(store_id: int, client_factory) -> None:
    from ..db import SessionLocal
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        try:
            sync_store_coupons(db, store, client_factory(store.appid, decrypt_secret(store.secret_enc)))
            db.commit()
        except Exception as e:
            db.rollback()
            log.warning("coupon sync for store %s failed: %s", store_id, e)


def price_after(price: int, c: StoreCoupon) -> int:
    """The price after one coupon (fen); the price itself when the coupon does not apply."""
    if c.threshold and price < c.threshold and c.type in (1, 2, 101, 102):
        return price
    if c.type in (2, 4, 102, 104):  # 满减 / 直减
        return max(price - c.discount_fee, 0)
    if c.type in (1, 3, 101, 103) and 0 < c.discount_num < 10000:  # 折扣, 5100 = 5.1折
        return round(price * c.discount_num / 10000)
    return price


def applies(c: StoreCoupon, product_id: str) -> bool:
    return c.type >= 100 or str(product_id) in (c.product_ids or [])


def best(price: int, coupons: list[StoreCoupon], product_id: str) -> tuple[int, StoreCoupon | None]:
    """(到手价, coupon) with the coupon that takes the most off this price."""
    out, used = price, None
    for c in coupons:
        if applies(c, product_id):
            p = price_after(price, c)
            if p < out:
                out, used = p, c
    return out, used


def by_store(db, store_ids) -> dict[int, list[StoreCoupon]]:
    out: dict[int, list[StoreCoupon]] = {}
    for c in db.scalars(select(StoreCoupon).where(StoreCoupon.store_id.in_(store_ids))):
        out.setdefault(c.store_id, []).append(c)
    return out
