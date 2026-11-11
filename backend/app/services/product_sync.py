"""商品管理: pull a store's products from WeChat into store_product, and push changes back."""
import logging
from datetime import datetime

from sqlalchemy import delete, select

from ..db import SessionLocal
from ..models import Store, StoreProduct
from ..security import decrypt_secret
from .wechat import explain

log = logging.getLogger(__name__)

STATUS_NAMES = {
    0: "草稿", 5: "上架", 6: "回收站", 11: "已下架", 13: "违规下架", 14: "保证金不足下架",
    15: "品牌过期下架", 20: "封禁",
}
ON_SALE = 5
DELISTED = 11


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
    for k, v in data.items():
        setattr(row, k, v)
    row.synced_at = datetime.now()
    return row


def sync_store(store_id: int, client_factory) -> None:
    """Full sync of one store. Runs in the background; progress is written to the store row."""
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if store is None:
            return
        store.sync_status, store.sync_message = "running", "正在读取商品列表…"
        db.commit()
        try:
            client = client_factory(store.appid, decrypt_secret(store.secret_enc))
            ids: list[str] = []
            next_key = ""
            while True:
                page = client.list_products(next_key=next_key)
                batch = [str(i) for i in page.get("product_ids") or []]
                ids.extend(batch)
                next_key = page.get("next_key") or ""
                store.sync_message = f"已读取 {len(ids)} 个商品 ID…"
                db.commit()
                if not batch or not next_key:
                    break
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
                    db.commit()
            # Products deleted in the WeChat back office disappear here too.
            db.execute(delete(StoreProduct).where(StoreProduct.store_id == store.id,
                                                  StoreProduct.wx_product_id.not_in(seen or {""})))
            store.sync_status = "done"
            store.sync_message = f"共 {len(seen)} 个商品" + (f"，{failed} 个读取失败" if failed else "")
            store.synced_at = datetime.now()
            db.commit()
        except Exception as e:
            db.rollback()
            store = db.get(Store, store_id)
            store.sync_status, store.sync_message = "failed", f"同步失败：{explain(e)}"[:255]
            db.commit()
