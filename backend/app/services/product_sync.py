"""商品管理: pull a store's products from WeChat into store_product, and push changes back."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from ..db import SessionLocal
from ..models import Store, StoreProduct
from ..security import decrypt_secret
from .wechat import explain

log = logging.getLogger(__name__)

STATUS_NAMES = {
    0: "草稿箱", 5: "上架", 6: "回收站", 11: "已下架", 13: "违规下架", 14: "保证金不足下架",
    15: "品牌过期下架", 20: "封禁",
}
ON_SALE = 5
DELISTED = 11
DRAFT = 0  # 草稿箱: saved, not yet submitted for review


def status_name(status: int, edit_status: int = 0) -> str:
    name = STATUS_NAMES.get(status, f"状态{status}")
    if edit_status == 2:
        name += "（审核中）"
    elif edit_status == 3:
        name += "（审核失败）"
    return name


def parse_product(resp: dict) -> dict:
    """Map product/get into our columns. Prefers the online version, falls back to the draft."""
    online = resp.get("product") or {}
    draft = resp.get("edit_product") or {}
    p = online or draft
    skus = []
    for s in p.get("skus") or []:
        skus.append({
            "sku_id": str(s.get("sku_id", "")),
            "sku_code": s.get("sku_code") or s.get("out_sku_id") or "",
            "price": int(s.get("sale_price") or 0),
            "stock": int(s.get("stock_num") or 0),
            "attrs": "；".join(f"{a.get('attr_key')}:{a.get('attr_value')}" for a in s.get("sku_attrs") or []),
            "thumb": s.get("thumb_img") or "",  # the spec's picture
        })
    prices = [s["price"] for s in skus if s["price"]] or [0]
    return {
        "wx_product_id": str(p.get("product_id") or resp.get("product_id") or ""),
        "title": (p.get("title") or "")[:255],
        "cover": (p.get("head_imgs") or [""])[0],
        "spu_code": (p.get("spu_code") or p.get("out_product_id") or "")[:128],
        "status": int(p.get("status") or 0),
        "edit_status": int(draft.get("edit_status") or p.get("edit_status") or 0),
        "price_min": min(prices),
        "price_max": max(prices),
        "stock": sum(s["stock"] for s in skus),
        "skus": skus,
    }


def upsert(db, store: Store, data: dict) -> StoreProduct:
    row = db.scalar(select(StoreProduct).where(StoreProduct.store_id == store.id,
                                               StoreProduct.wx_product_id == data["wx_product_id"]))
    if row is None:
        row = StoreProduct(tenant_id=store.tenant_id, store_id=store.id, wx_product_id=data["wx_product_id"])
        db.add(row)
    removed = {s["sku_id"] for s in row.skus or [] if s.get("removed")}  # 删除规格 (货源助手) survives a re-sync
    if removed and data.get("skus"):
        data = {**data, "skus": [{**s, "removed": True} if s["sku_id"] in removed else s for s in data["skus"]]}
    for k, v in data.items():
        setattr(row, k, v)
    row.synced_at = datetime.now()
    return row


STALE = timedelta(minutes=15)  # progress is saved every 20 products; one WeChat call may take 30 s


def live_status(status: str, message: str, heartbeat) -> tuple[str, str]:
    """A sync marked running that has not made progress for STALE was interrupted (e.g. the API
    process restarted during a deploy): report it as failed, so it can be started again."""
    if status == "running" and (heartbeat is None or datetime.now() - heartbeat > STALE):
        return "failed", "同步已中断（服务重启或超时），请重新同步"
    return status, message


def sync_store(store_id: int, client_factory) -> None:
    """Full sync of one store. Runs in the background; progress is written to the store row."""
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if store is None:
            return
        store.sync_status, store.sync_message = "running", "正在读取商品列表…"
        store.sync_heartbeat = datetime.now()
        db.commit()
        try:
            client = client_factory(store.appid, decrypt_secret(store.secret_enc))
            ids: list[str] = []
            # the default list, then the 草稿箱 (products saved but not submitted), which it may leave out
            for status in (None, DRAFT):
                next_key = ""
                try:
                    while True:
                        page = client.list_products(next_key=next_key, status=status)
                        batch = [str(i) for i in page.get("product_ids") or []]
                        ids.extend(batch)
                        next_key = page.get("next_key") or ""
                        store.sync_message = f"已读取 {len(ids)} 个商品 ID…"
                        store.sync_heartbeat = datetime.now()
                        db.commit()
                        if not batch or not next_key:
                            break
                except Exception as e:
                    if status is None:
                        raise
                    log.warning("draft list of store %s failed: %s", store.id, e)  # keep the rest of the sync
            seen, failed = set(), 0
            for n, pid in enumerate(dict.fromkeys(ids), 1):
                try:
                    upsert(db, store, parse_product(client.get_product(pid)))
                    seen.add(pid)
                except Exception as e:  # one bad product must not stop the sync
                    failed += 1
                    log.warning("sync product %s failed: %s", pid, e)
                if n % 20 == 0:
                    store.sync_message = f"已同步 {n}/{len(ids)}"
                    store.sync_heartbeat = datetime.now()
                    db.commit()
            # Products deleted in the WeChat back office disappear here too.
            db.execute(delete(StoreProduct).where(StoreProduct.store_id == store.id,
                                                  StoreProduct.wx_product_id.not_in(seen or {""})))
            try:  # the store's coupons, for the 到手价 shown in 商品管理
                from .coupons import sync_store_coupons
                sync_store_coupons(db, store, client)
            except Exception as e:
                log.warning("coupon sync failed: %s", e)
            store.sync_status = "done"
            store.sync_message = f"共 {len(seen)} 个商品" + (f"，{failed} 个读取失败" if failed else "")
            store.sync_heartbeat = datetime.now()
            store.synced_at = datetime.now()
            db.commit()
        except Exception as e:
            db.rollback()
            store = db.get(Store, store_id)
            store.sync_status, store.sync_message = "failed", f"同步失败：{explain(e)}"[:255]
            db.commit()


def library_products_by_product(db, tenant_id: int, store_products) -> dict:
    """{store product id: its 商品云库 product} for many store products in a few queries (公式改价 of a
    whole list); the same choice of 商品云库 as library_products_for."""
    from sqlalchemy import select, tuple_

    from ..models import LibraryProduct, PublishedRecord
    pairs = [(p.store_id, p.wx_product_id) for p in store_products if p.wx_product_id]
    if not pairs:
        return {}
    key_of = {}
    for i in range(0, len(pairs), 500):
        for store_id, wx, key in db.execute(select(PublishedRecord.store_id, PublishedRecord.wx_product_id,
                                                   PublishedRecord.source_key).where(
                tuple_(PublishedRecord.store_id, PublishedRecord.wx_product_id).in_(pairs[i:i + 500]))):
            key_of[(store_id, wx)] = key
    tenants = [tenant_id] + sorted({p.tenant_id for p in store_products if p.tenant_id != tenant_id})
    rank = {t: n for n, t in enumerate(tenants)}
    source_ids = sorted({k.partition(":")[2] for k in key_of.values()})
    best = {}  # source key -> library product of the first tenant in `tenants`
    for i in range(0, len(source_ids), 500):
        for lp in db.scalars(select(LibraryProduct).where(LibraryProduct.tenant_id.in_(tenants),
                                                          LibraryProduct.source_product_id.in_(source_ids[i:i + 500]))):
            k = f"{lp.platform}:{lp.source_product_id}"
            if k not in best or rank[lp.tenant_id] < rank[best[k].tenant_id]:
                best[k] = lp
    return {p.id: best[key_of[(p.store_id, p.wx_product_id)]] for p in store_products
            if (p.store_id, p.wx_product_id) in key_of and key_of[(p.store_id, p.wx_product_id)] in best}


def library_products_for(db, tenant_id: int, store_products) -> list:
    """The 商品云库 products that store products were published from (via PublishedRecord)."""
    from sqlalchemy import select, tuple_

    from ..models import LibraryProduct, PublishedRecord
    pairs = [(p.store_id, p.wx_product_id) for p in store_products if p.wx_product_id]
    if not pairs:
        return []
    keys = set(db.scalars(select(PublishedRecord.source_key).where(
        tuple_(PublishedRecord.store_id, PublishedRecord.wx_product_id).in_(pairs))))
    # this account's 商品云库 first; for a store assigned in 用户管理, the store owner's
    tenants = [tenant_id] + sorted({p.tenant_id for p in store_products if p.tenant_id != tenant_id})
    out = []
    for key in keys:
        platform, _, source_id = key.partition(":")
        for t in tenants:
            p = db.scalar(select(LibraryProduct).where(LibraryProduct.tenant_id == t,
                                                       LibraryProduct.platform == platform,
                                                       LibraryProduct.source_product_id == source_id))
            if p:
                out.append(p)
                break
    return out
