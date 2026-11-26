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
from ..timeutil import cn_time
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
    """A WeChat timestamp as China time (the time 微信小店后台 shows), whatever the server's time zone."""
    t = _int(v)
    return cn_time(t) if t > 0 else None


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
    return {
        "order_id": str(o.get("order_id") or ""),
        "status": _int(o.get("status")),
        "create_time": _time(o.get("create_time")), "update_time": _time(o.get("update_time")),
        "pay_time": _time((d.get("pay_info") or {}).get("pay_time")),
        "product_price": _int(price.get("product_price")), "freight": _int(price.get("freight")),
        "order_price": _int(price.get("order_price")),
        **address_fields(addr),
        "buyer_note": str(ext.get("customer_notes") or "")[:512],
        "merchant_note": str(ext.get("merchant_notes") or "")[:512],
        "items": items,
    }


def is_masked(v: str) -> bool:
    return "*" in (v or "")


def address_fields(addr: dict) -> dict:
    """Receiver columns from an address_info (获取订单详情 or 解码敏感数据)."""
    address = "".join(str(addr.get(k) or "") for k in ("province_name", "city_name", "county_name", "detail_info"))
    if addr.get("house_number"):
        address += f" {addr['house_number']}"
    return {"receiver_name": str(addr.get("user_name") or "")[:64],
            "receiver_phone": str(addr.get("tel_number") or addr.get("virtual_order_tel_number") or "")[:64],
            "receiver_address": address[:512]}


def virtual_number(resp: dict) -> tuple[str, datetime | None]:
    """The buyer's 虚拟号 from a 解码敏感数据 response (virtual_number_info; field names VERIFY):
    the number, '转' and the extension when there is one, and when it expires."""
    vn = resp.get("virtual_number_info") or (resp.get("address_info") or {}).get("tel_number_ext_info") or {}
    if not isinstance(vn, dict):
        return "", None
    num = next((str(vn[k]) for k in ("virtual_tel_number", "virtual_number", "virtual_tel", "tel_number", "number")
                if vn.get(k)), "")
    ext = next((str(vn[k]) for k in ("extension", "ext_number", "virtual_tel_ext", "extension_number", "ext")
                if vn.get(k)), "")
    exp = next((_time(vn[k]) for k in ("virtual_tel_expire_time", "expire_time", "expired_time", "valid_end_time")
                if vn.get(k)), None)
    return (f"{num}转{ext}" if num and ext else num)[:64], exp


def money(raw: dict, order_price: int = 0) -> dict:
    """What the seller gets from an order (fen), from the 订单详情 price / settle / commission info:
    买家实付; 平台券 / 补贴 the platform pays (the seller does not bear them); 商家实收 (= 实付 + those);
    技术服务费 (actual once settled, else the estimate); 达人佣金; 到手 = 商家实收 − 服务费 − 佣金."""
    d = ((raw or {}).get("order") or raw or {}).get("order_detail") or {}
    pi, si = d.get("price_info") or {}, d.get("settle_info") or {}
    paid = _int(pi.get("order_price")) or order_price
    platform = _int(pi.get("cash_coupon_discounted_price")) + _int(pi.get("national_subsidy_discounted_price"))
    receive = _int(pi.get("merchant_receieve_price")) if pi.get("merchant_receieve_price") is not None else paid + platform
    settled = si.get("commission_fee") is not None
    fee = _int(si.get("commission_fee")) if settled else _int(si.get("predict_commission_fee"))
    talent = sum(_int(c.get("amount")) for c in d.get("commission_infos") or [])
    return {"buyer_paid": paid, "platform_discount": platform, "merchant_receive": receive,
            "commission_fee": fee, "commission_settled": settled, "talent_commission": talent,
            "net_income": receive - fee - talent, "settle_time": _time(si.get("settle_time"))}


def deliveries(raw: dict) -> list[dict]:
    """Shipments of an order (delivery_info.delivery_product_info): company, waybill, time, products."""
    d = ((raw.get("order") or raw).get("order_detail") or {}).get("delivery_info") or {}
    out = []
    for p in d.get("delivery_product_info") or []:
        out.append({"delivery_id": p.get("delivery_id") or "", "waybill_id": p.get("waybill_id") or "",
                    "deliver_type": p.get("deliver_type"), "time": _time(p.get("delivery_time")),
                    "products": [{"product_id": str(x.get("product_id") or ""), "sku_id": str(x.get("sku_id") or ""),
                                  "qty": _int(x.get("product_cnt"))} for x in p.get("product_infos") or []]})
    return out


def shipped_qty(raw: dict) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for d in deliveries(raw):
        for p in d["products"]:
            k = (p["product_id"], p["sku_id"])
            out[k] = out.get(k, 0) + p["qty"]
    return out


def upsert(db, store: Store, resp: dict) -> StoreOrder:
    data = parse_order(resp)
    row = db.scalar(select(StoreOrder).where(StoreOrder.store_id == store.id, StoreOrder.order_id == data["order_id"]))
    if row is None:
        row = StoreOrder(tenant_id=store.tenant_id, store_id=store.id, order_id=data["order_id"])
        db.add(row)
    if row.revealed_at:  # keep the decoded buyer info; a re-sync only brings the masked version
        for k in ("receiver_name", "receiver_phone", "receiver_address"):
            data.pop(k, None)
    for k, v in data.items():
        setattr(row, k, v)
    row.raw = resp
    o = (resp or {}).get("order") or resp or {}
    row.buyer_openid = str(o.get("openid") or "")[:128] or row.buyer_openid or ""  # 买家消息 → this buyer's orders
    row.net_income = money(resp, data.get("order_price") or 0)["net_income"]
    row.synced_at = datetime.now()
    return row


# ---- 代发 (订单分配给供货商): its status shows in 订单管理 as the 采购 state -------------------------
DS_STATUS = {100: "待发货", 199: "部分发货", 200: "已发货", 300: "已取消"}
DS_FINAL = (200, 300)
DS_PER_SYNC = 300  # 代发单 read per sync at most


def ds_order_ids(raw: dict) -> list[str]:
    """The 代发单号 of an order's lines (dropship_info / dropship_info_list)."""
    d = ((raw or {}).get("order") or raw or {}).get("order_detail") or {}
    ids = []
    for p in d.get("product_infos") or []:
        for x in [p.get("dropship_info") or {}, *(p.get("dropship_info_list") or [])]:
            i = str((x or {}).get("ds_order_id") or "")
            if i and i != "0" and i not in ids:
                ids.append(i)
    return ids


def dropship_summary(orders: list[dict]) -> dict:
    live = [o for o in orders if o["status"] != 300]
    if not live:
        status = 300
    elif all(o["status"] == 200 for o in live):
        status = 200
    elif any(o["status"] in (199, 200) for o in live):
        status = 199
    else:
        status = 100
    suppliers = list(dict.fromkeys(o["supplier"] for o in (live or orders) if o["supplier"]))
    return {"ids": [o["id"] for o in orders], "orders": orders, "status": status,
            "status_name": "已分配" if status == 100 else DS_STATUS.get(status, str(status)),
            "supplier": "、".join(suppliers), "synced_at": datetime.now().isoformat(timespec="seconds")}


def refresh_dropship(db, store: Store, client, days: int = 45) -> int:
    """Read the 代发单 of the store's recent orders that were given to a 供货商 (and are not final yet)."""
    since = datetime.now() - timedelta(days=days)
    n = 0
    for o in db.scalars(select(StoreOrder).where(StoreOrder.store_id == store.id, StoreOrder.create_time >= since)):
        ids = ds_order_ids(o.raw or {})
        cur = o.dropship or {}
        if not ids:
            if cur:
                o.dropship = None
            continue
        if cur.get("ids") == ids and cur.get("status") in DS_FINAL:
            continue
        if n >= DS_PER_SYNC:
            break
        orders = []
        for i in ids:
            r = client.dropship_get(i)  # an API error (e.g. no 代发 permission) stops the refresh
            n += 1
            orders.append({"id": i, "status": int(r.get("status") or 0), "supplier": r.get("supplier_name") or ""})
        o.dropship = dropship_summary(orders)
    return n


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
        store.order_sync_heartbeat = datetime.now()
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
                store.order_sync_heartbeat = datetime.now()
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
                    store.order_sync_heartbeat = datetime.now()
                    db.commit()
            try:  # 代发: the 供货商 and its status
                store.order_sync_message = "正在读取代发单…"
                db.commit()
                refresh_dropship(db, store, client)
            except Exception as e:
                db.rollback()
                store = db.get(Store, store_id)
                log.warning("dropship refresh of store %s failed: %s", store_id, e)
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
