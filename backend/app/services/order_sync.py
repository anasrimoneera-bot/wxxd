"""订单管理: read a store's orders from the WeChat Store API into store_order.

获取订单列表 (/channels/ec/order/list/get) by update time, in windows of at most 7 days, then
获取订单详情 (/channels/ec/order/get) for each order. The first sync reads the last FIRST_DAYS days;
later syncs continue from where the last one finished (orders_synced_to, minus a small overlap).
The field names follow the documentation and are marked VERIFY: the raw response is kept on each
order (原始数据) so they can be checked against a real store.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Store, StoreOrder
from ..security import decrypt_secret
from .wechat import explain

log = logging.getLogger(__name__)
FIRST_DAYS = 30
WINDOW = timedelta(days=7)
OVERLAP = timedelta(minutes=10)

STATUS_NAMES = {10: "待付款", 12: "礼物待收下", 13: "凑单中", 20: "待发货", 21: "部分发货", 30: "待收货",
                100: "已完成", 200: "售后取消", 250: "已取消"}


def status_name(status: int) -> str:
    return STATUS_NAMES.get(status, f"状态 {status}")


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _time(v) -> datetime | None:
    t = _int(v)
    return datetime.fromtimestamp(t) if t > 0 else None


def parse_order(resp: dict) -> dict:
    """Map 获取订单详情 into our columns. VERIFY fields against a real response."""
    o = resp.get("order") or resp
    d = o.get("order_detail") or {}
    price = d.get("price_info") or {}
    delivery = d.get("delivery_info") or {}
    addr = delivery.get("address_info") or {}
    ext = d.get("ext_info") or {}
    items = []
    for p in d.get("product_infos") or []:
        items.append({
            "product_id": str(p.get("product_id") or ""), "sku_id": str(p.get("sku_id") or ""),
            "title": p.get("title") or "", "thumb": p.get("thumb_img") or "",
            "attrs": "；".join(f"{a.get('attr_key')}:{a.get('attr_value')}" for a in p.get("sku_attrs") or []),
            "qty": _int(p.get("sku_cnt")) or 1, "sale_price": _int(p.get("sale_price")),
            "real_price": _int(p.get("real_price")),
        })
    address = "".join(str(addr.get(k) or "") for k in ("province_name", "city_name", "county_name", "detail_info"))
    if addr.get("house_number"):
        address += f" {addr['house_number']}"
    return {
        "order_id": str(o.get("order_id") or ""),
        "status": _int(o.get("status")),
        "create_time": _time(o.get("create_time")), "update_time": _time(o.get("update_time")),
        "pay_time": _time((d.get("pay_info") or {}).get("pay_time")),
        "product_price": _int(price.get("product_price")), "freight": _int(price.get("freight")),
        "order_price": _int(price.get("order_price")),
        "receiver_name": str(addr.get("user_name") or "")[:64],
        "receiver_phone": str(addr.get("tel_number") or addr.get("virtual_order_tel_number") or "")[:64],
        "receiver_address": address[:512],
        "buyer_note": str(ext.get("customer_notes") or "")[:512],
        "merchant_note": str(ext.get("merchant_notes") or "")[:512],
        "items": items,
    }


def upsert(db, store: Store, resp: dict) -> StoreOrder:
    data = parse_order(resp)
    row = db.scalar(select(StoreOrder).where(StoreOrder.store_id == store.id, StoreOrder.order_id == data["order_id"]))
    if row is None:
        row = StoreOrder(tenant_id=store.tenant_id, store_id=store.id, order_id=data["order_id"])
        db.add(row)
    for k, v in data.items():
        setattr(row, k, v)
    row.raw = resp
    row.synced_at = datetime.now()
    return row


def windows(start: datetime, end: datetime):
    while start < end:
        stop = min(start + WINDOW, end)
        yield start, stop
        start = stop


def sync_orders(store_id: int, client_factory, now: datetime | None = None) -> None:
    """Background job: read the store's new and changed orders. Progress goes to the store row."""
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if store is None:
            return
        store.order_sync_status, store.order_sync_message = "running", "正在读取订单列表…"
        db.commit()
        try:
            client = client_factory(store.appid, decrypt_secret(store.secret_enc))
            end = now or datetime.now()
            start = (store.orders_synced_to - OVERLAP) if store.orders_synced_to else end - timedelta(days=FIRST_DAYS)
            ids: list[str] = []
            for a, b in windows(start, end):
                next_key = ""
                while True:
                    page = client.list_orders(a.timestamp(), b.timestamp(), next_key)
                    ids.extend(str(i) for i in page.get("order_id_list") or [])
                    next_key = page.get("next_key") or ""
                    if not page.get("has_more") or not next_key:
                        break
                store.order_sync_message = f"已读取 {len(ids)} 个订单号…"
                db.commit()
            failed = 0
            ids = list(dict.fromkeys(ids))
            for n, oid in enumerate(ids, 1):
                try:
                    upsert(db, store, client.get_order(oid))
                except Exception as e:  # one bad order must not stop the sync
                    failed += 1
                    log.warning("order %s failed: %s", oid, e)
                if n % 20 == 0:
                    store.order_sync_message = f"已同步 {n}/{len(ids)}"
                    db.commit()
            store.orders_synced_to = end
            store.order_sync_status = "done"
            store.order_sync_message = (f"更新 {len(ids) - failed} 个订单" + (f"，{failed} 个读取失败" if failed else "")
                                        + f"（{end:%m-%d %H:%M}）")
            db.commit()
        except Exception as e:
            db.rollback()
            store = db.get(Store, store_id)
            store.order_sync_status, store.order_sync_message = "failed", f"同步失败：{explain(e)}"[:255]
            db.commit()
