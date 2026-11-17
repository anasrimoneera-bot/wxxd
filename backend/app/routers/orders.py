"""订单管理: orders read from the WeChat Store API, with each line's 货源 (1688) for procurement,
the buyer's decoded contact (虚拟号 / 真实号码) and shipping (发货 / 批量发货 / 补发货)."""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import ProductSourceLink, Store, StoreOrder, StoreProduct, User
from .. import access
from ..services import library_sync, order_sync
from ..services.product_sync import live_status
from ..security import decrypt_secret
from . import stores as stores_router
from .common import fmt_dt, page_params

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _stores(db, user) -> dict[int, Store]:
    return access.stores(db, user.tenant_id)  # own stores and stores assigned in 用户管理


def _sources(db, tenant_id: int, orders: list[StoreOrder]) -> dict[tuple[int, str], dict]:
    """{(store id, WeChat product id): 货源} for the order lines: the store product's own source,
    else the source of the 商品云库 product it was published from."""
    from ..services.product_sync import library_products_for
    from .sourcing import link_out
    pairs = {(o.store_id, it["product_id"]) for o in orders for it in o.items or [] if it.get("product_id")}
    if not pairs:
        return {}
    sps = db.scalars(select(StoreProduct).where(or_(*[
        (StoreProduct.store_id == s) & (StoreProduct.wx_product_id == p) for s, p in pairs]))).all()
    out = {}
    for sp in sps:
        link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.store_product_id == sp.id))
        if link is None:
            libs = library_products_for(db, tenant_id, [sp])
            if libs:
                link = db.scalar(select(ProductSourceLink).where(
                    ProductSourceLink.tenant_id.in_([p.tenant_id for p in libs if p]),
                    ProductSourceLink.library_product_id.in_([p.id for p in libs if p])))
        if link is not None:
            out[(sp.store_id, sp.wx_product_id)] = link_out(link, library_sync.store_sku_prices(db, link, sp))
    return out


def order_out(o: StoreOrder, names: dict[int, str], sources: dict) -> dict:
    items, cost, known = [], 0, True
    for it in o.items or []:
        src = sources.get((o.store_id, it.get("product_id")))
        if src:  # the 货源价 of the ordered spec (the base 货源价 when it has none)
            unit = (src.get("sku_prices") or {}).get(it.get("sku_id")) or src["price"] or 0
            src = {**src, "price": unit}
            cost += unit * (it.get("qty") or 1)
        else:
            known = False
        items.append({**it, "source": src})
    return {
        "id": o.id, "order_id": o.order_id, "store_id": o.store_id, "store_name": names.get(o.store_id, ""),
        "status": o.status, "status_name": order_sync.status_name(o.status),
        "create_time": fmt_dt(o.create_time) if o.create_time else "", "pay_time": fmt_dt(o.pay_time) if o.pay_time else "",
        "product_price": o.product_price, "freight": o.freight, "order_price": o.order_price,
        "receiver_name": o.receiver_name, "receiver_phone": o.receiver_phone, "receiver_address": o.receiver_address,
        "buyer_note": o.buyer_note, "merchant_note": o.merchant_note, "items": items,
        "masked": not o.revealed_at and any(order_sync.is_masked(v) for v in
                                            (o.receiver_name, o.receiver_phone, o.receiver_address)),
        "revealed": bool(o.revealed_at),
        # the real phone stays masked when the buyer uses a 虚拟号: call that number instead
        "phone_masked": bool(o.revealed_at) and order_sync.is_masked(o.receiver_phone),
        "virtual_phone": o.virtual_phone or "",
        "virtual_phone_expire": fmt_dt(o.virtual_phone_expire) if o.virtual_phone_expire else "",
        "deliveries": [{**d, "time": fmt_dt(d["time"]) if d["time"] else ""} for d in order_sync.deliveries(o.raw or {})],
        "can_ship": o.status in (20, 21),
        # 货源成本 / 毛利 estimate (goods only, shipping not included); only when every line has a source
        "source_cost": cost if known and items else None,
        # 采购实付 entered: the real margin; else the estimate
        "margin": (o.order_price - o.purchase_cost) if o.purchase_cost is not None
        else (o.order_price - cost) if known and items else None,
        "margin_real": o.purchase_cost is not None,
        "purchase_order_no": o.purchase_order_no or "", "purchase_cost": o.purchase_cost,
        "purchase_note": o.purchase_note or "", "purchased_at": fmt_dt(o.purchased_at) if o.purchased_at else "",
    }


@router.get("/stores")
def order_stores(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(StoreOrder.store_id, func.count()).where(
        StoreOrder.store_id.in_(access.store_ids(user.tenant_id)))
                             .group_by(StoreOrder.store_id)).all())
    return [{"id": s.id, "name": s.name, "orders": counts.get(s.id, 0),
             **dict(zip(("sync_status", "sync_message"), live_status(
                 s.order_sync_status, s.order_sync_message, s.order_sync_heartbeat))),
             "synced_to": fmt_dt(s.orders_synced_to) if s.orders_synced_to else ""} for s in _stores(db, user).values()]


class SyncIn(BaseModel):
    store_ids: list[int] = Field(default_factory=list)  # empty = all stores


@router.post("/sync")
def start_sync(body: SyncIn, background: BackgroundTasks, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    stores = _stores(db, user)
    ids = body.store_ids or list(stores)
    started = []
    for sid in ids:
        s = stores.get(sid)
        if not s:
            raise HTTPException(404, "店铺不存在")
        if live_status(s.order_sync_status, s.order_sync_message, s.order_sync_heartbeat)[0] == "running":
            continue
        s.order_sync_status, s.order_sync_message = "running", "排队中…"
        s.order_sync_heartbeat = datetime.now()
        started.append(sid)
    db.commit()
    for sid in started:
        background.add_task(order_sync.sync_orders, sid, stores_router.client_factory)
    return {"started": started}


@router.get("")
def list_orders(page: int = 1, size: int = 20, store_id: int | None = None, status: int | None = None,
                keyword: str = "", date_from: str = "", date_to: str = "",
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(StoreOrder).where(StoreOrder.store_id.in_(access.store_ids(user.tenant_id)))
    if store_id:
        q = q.where(StoreOrder.store_id == store_id)
    if status is not None:
        q = q.where(StoreOrder.status == status)
    if keyword:
        k = keyword.strip()
        q = q.where(or_(StoreOrder.order_id.contains(k), StoreOrder.receiver_name.contains(k),
                        StoreOrder.receiver_phone.contains(k), StoreOrder.purchase_order_no.contains(k)))
    for v, op in ((date_from, "ge"), (date_to, "le")):
        if v:
            try:
                d = datetime.strptime(v[:10], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
            q = q.where(StoreOrder.create_time >= d) if op == "ge" else q.where(
                StoreOrder.create_time <= d.replace(hour=23, minute=59, second=59))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    counts = dict(db.execute(select(StoreOrder.status, func.count()).where(
        StoreOrder.store_id.in_(access.store_ids(user.tenant_id)))
                             .group_by(StoreOrder.status)).all())
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(StoreOrder.create_time.desc(), StoreOrder.id.desc()).offset(off).limit(lim)).all()
    names = {s.id: s.name for s in _stores(db, user).values()}
    src = _sources(db, user.tenant_id, rows)
    return {"total": total, "status_counts": {str(k): v for k, v in counts.items()},
            "items": [order_out(o, names, src) for o in rows]}


def _own_order(db, user, oid: int) -> StoreOrder:
    o = db.get(StoreOrder, oid)
    if not o or not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)):
        raise HTTPException(404, "订单不存在")
    return o


def _client(db, o: StoreOrder):
    store = db.get(Store, o.store_id)
    return stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))


def _out(db, user, o: StoreOrder) -> dict:
    names = {s.id: s.name for s in _stores(db, user).values()}
    return order_out(o, names, _sources(db, user.tenant_id, [o]))


def _refresh(db, o: StoreOrder, client) -> None:
    """Read the order again after a change (发货, 真实号码) so status and shipments are current."""
    try:
        order_sync.upsert(db, db.get(Store, o.store_id), client.get_order(o.order_id))
        db.commit()
        db.refresh(o)
    except Exception:
        db.rollback()


@router.post("/{oid}/reveal")
def reveal_buyer(oid: int, again: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """显示买家信息: decode the masked name / phone / address (解码订单包含的敏感数据) and keep it on the
    order, so it is not decoded again (daily quota) and is ready for shipping. again=true decodes once
    more (WeChat counts the same order once), e.g. for orders decoded before 虚拟号 were shown."""
    from ..services.wechat import explain
    o = _own_order(db, user, oid)
    if not o.revealed_at or again:
        try:
            resp = _client(db, o).decode_order_sensitive(o.order_id)
        except Exception as e:
            raise HTTPException(400, f"获取买家信息失败：{explain(e)}")
        addr = resp.get("address_info") or (resp.get("order") or {}).get("address_info") or {}
        fields = order_sync.address_fields(addr)
        if not fields["receiver_name"] and not fields["receiver_phone"]:
            raise HTTPException(400, f"微信没有返回买家信息：{str(resp)[:300]}")
        for k, v in fields.items():
            setattr(o, k, v)
        o.virtual_phone, o.virtual_phone_expire = order_sync.virtual_number(resp)
        o.decoded = resp
        o.revealed_at = datetime.now()
        db.commit()
    return _out(db, user, o)


@router.post("/{oid}/real-number")
def apply_real_number(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """申请查看订单真实号码: once WeChat approves, the order shows the real phone number."""
    from ..services.wechat import explain
    o = _own_order(db, user, oid)
    try:
        resp = _client(db, o).apply_real_number(o.order_id)
    except Exception as e:
        raise HTTPException(400, f"申请失败：{explain(e)}")
    return {"ok": True, "response": resp}


@router.get("/{oid}/real-number")
def real_number_status(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """查看订单真实号审核状态; when approved, the real number is read into the order."""
    from ..services.wechat import explain
    o = _own_order(db, user, oid)
    client = _client(db, o)
    try:
        resp = client.real_number_audit(o.order_id)
    except Exception as e:
        raise HTTPException(400, f"查询失败：{explain(e)}")
    if o.revealed_at:  # approved: decoding again returns the real number
        try:
            dec = client.decode_order_sensitive(o.order_id)
            phone = order_sync.address_fields(dec.get("address_info") or {})["receiver_phone"]
            if phone and not order_sync.is_masked(phone):
                o.receiver_phone, o.decoded = phone, dec
                db.commit()
        except Exception:
            pass
    return {"response": resp, "order": _out(db, user, o)}


@router.post("/{oid}/virtual-delay")
def delay_virtual_number(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """订单虚拟号延期 (possible from 7 days before it expires)."""
    from ..services.wechat import explain
    o = _own_order(db, user, oid)
    client = _client(db, o)
    try:
        client.delay_virtual_number(o.order_id)
        dec = client.decode_order_sensitive(o.order_id)  # read the new expiry (same order: no extra quota)
        o.virtual_phone, o.virtual_phone_expire = order_sync.virtual_number(dec)
        o.decoded = dec
        db.commit()
    except Exception as e:
        raise HTTPException(400, f"延期失败：{explain(e)}")
    return _out(db, user, o)


# ---- 采购 ------------------------------------------------------------------------
class PurchaseLine(BaseModel):
    source_id: int  # ProductSourceLink id shown on the order line
    unit_price: float = Field(gt=0, le=1_000_000)  # yuan: the real 货源单价
    product_id: str = ""  # the order line: its spec gets this 货源价 (货源价 per spec)
    sku_id: str = ""


class PurchaseIn(BaseModel):
    purchase_order_no: str = Field(default="", max_length=128)  # e.g. the 1688 order number
    purchase_cost: float | None = Field(default=None, ge=0, le=10_000_000)  # yuan, incl. shipping; None = clear
    purchase_note: str = Field(default="", max_length=255)
    source_prices: list[PurchaseLine] = Field(default_factory=list)  # also change these products' 货源价


@router.put("/{oid}/purchase")
def save_purchase(oid: int, body: PurchaseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """采购: the purchase order number and what was really paid (the order's margin uses it).
    source_prices also change the products' 货源价 (like 修改货源价: the 1688 price is kept), so
    later orders and estimates use the real price."""
    from ..services import library_sync
    o = _own_order(db, user, oid)
    o.purchase_order_no = body.purchase_order_no.strip()
    o.purchase_cost = round(body.purchase_cost * 100) if body.purchase_cost is not None else None
    o.purchase_note = body.purchase_note.strip()
    o.purchased_at = datetime.now() if (o.purchase_order_no or o.purchase_cost is not None) else None
    allowed = {src["id"] for src in _sources(db, user.tenant_id, [o]).values() if src}
    for line in body.source_prices:
        if line.source_id not in allowed:
            raise HTTPException(400, "货源不属于这个订单")
        link = db.get(ProductSourceLink, line.source_id)
        new = round(line.unit_price * 100)
        sp = db.scalar(select(StoreProduct).where(StoreProduct.store_id == o.store_id,
                                                  StoreProduct.wx_product_id == line.product_id)) if line.product_id else None
        several = sp is not None and line.sku_id and len(sp.skus or []) > 1
        current = library_sync.store_sku_prices(db, link, sp).get(line.sku_id, link.price) if several else link.price
        if new == current:
            continue
        if link.original_price is None:
            link.original_price = link.price  # keep the 1688 price the first time it changes
        if several:  # a product with several specs: the 货源价 of the ordered spec
            library_sync.set_store_sku_prices(db, link, sp, {line.sku_id: new})
        else:
            link.price = new
        db.flush()
        library_sync.sync_source(db, link)  # a store product's 货源 also goes to 商品云库
        library_sync.propagate_source(db, link)  # ... and to the product in every store
    db.commit()
    return _out(db, user, o)


# ---- 发货 ------------------------------------------------------------------------
_companies: dict[int, tuple[float, list]] = {}


@router.get("/delivery-companies")
def delivery_companies(store_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """快递公司列表 (cached for a day)."""
    import time

    from ..services.wechat import explain
    stores = _stores(db, user)
    store = stores.get(store_id) if store_id else next(iter(stores.values()), None)
    if not store:
        raise HTTPException(400, "请先添加店铺")
    hit = _companies.get(store.id)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    try:
        rows = stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc)).delivery_companies()
    except Exception as e:
        raise HTTPException(400, f"读取快递公司失败：{explain(e)}")
    out = [{"id": str(c.get("delivery_id") or ""), "name": c.get("delivery_name") or c.get("name") or ""}
           for c in rows if c.get("delivery_id")]
    _companies[store.id] = (time.time(), out)
    return out


class ShipLine(BaseModel):
    product_id: str
    sku_id: str
    qty: int = Field(ge=1)


class ShipIn(BaseModel):
    delivery_id: str = ""  # 快递公司编码 (ZTO, STO ...); empty with deliver_type 3
    waybill_id: str = Field(default="", max_length=64)
    deliver_type: int = Field(default=1)  # 1 快递 / 3 无需物流 (virtual goods)
    items: list[ShipLine] | None = None  # None = everything not shipped yet
    compensation: bool = False  # 补发货 (漏发 / 拆包 / 坏损 / 赠品)
    reason: str = Field(default="", max_length=200)


def _ship_lines(o: StoreOrder, body: ShipIn) -> list[dict]:
    if body.items:
        return [{"product_id": i.product_id, "sku_id": i.sku_id, "product_cnt": i.qty} for i in body.items]
    shipped = order_sync.shipped_qty(o.raw or {})
    lines = []
    for it in o.items or []:
        left = (it.get("qty") or 1) - shipped.get((it["product_id"], it["sku_id"]), 0)
        if body.compensation or left > 0:
            lines.append({"product_id": it["product_id"], "sku_id": it["sku_id"],
                          "product_cnt": (it.get("qty") or 1) if body.compensation else left})
    return lines


def ship_order(db, o: StoreOrder, body: ShipIn, client=None) -> None:
    from ..services.wechat import explain
    if body.deliver_type == 1 and not (body.delivery_id and body.waybill_id.strip()):
        raise HTTPException(400, "请选择快递公司并填写运单号")
    if not body.compensation and o.status not in (20, 21):
        raise HTTPException(400, f"订单状态为「{order_sync.status_name(o.status)}」，不能发货")
    lines = _ship_lines(o, body)
    if not lines:
        raise HTTPException(400, "这个订单的商品都已发货")
    entry = {"deliver_type": body.deliver_type, "product_infos": lines}
    if body.deliver_type == 1:
        entry.update(delivery_id=body.delivery_id, waybill_id=body.waybill_id.strip())
    client = client or _client(db, o)
    try:
        if body.compensation:
            client.compensate_delivery(o.order_id, [entry], body.reason)
        else:
            client.send_delivery(o.order_id, [entry])
    except Exception as e:
        raise HTTPException(400, f"{'补发货' if body.compensation else '发货'}失败：{explain(e)}")
    _refresh(db, o, client)


@router.post("/{oid}/ship")
def ship(oid: int, body: ShipIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """订单发货 / 补发货 through the WeChat API; the order is read again afterwards."""
    o = _own_order(db, user, oid)
    ship_order(db, o, body)
    return _out(db, user, o)


class BatchShipIn(BaseModel):
    # one line per order: 订单号, 快递公司 (编码 or name), 运单号 — separated by spaces, tabs or commas
    text: str = Field(min_length=1, max_length=200000)
    store_id: int | None = None


@router.post("/ship-batch")
def ship_batch(body: BatchShipIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """批量发货: paste 订单号 快递公司 运单号 lines (e.g. copied from Excel)."""
    import re
    companies = delivery_companies(body.store_id, user, db)
    by_name = {c["name"]: c["id"] for c in companies}
    by_id = {c["id"].upper(): c["id"] for c in companies}
    results, clients = [], {}
    for line in body.text.splitlines():
        parts = [p for p in re.split(r"[\s,，]+", line.strip()) if p]
        if not parts:
            continue
        if len(parts) < 3:
            results.append({"line": line.strip(), "ok": False, "error": "格式应为：订单号 快递公司 运单号"})
            continue
        order_no, company, waybill = parts[0], parts[1], parts[2]
        cid = by_id.get(company.upper()) or by_name.get(company) or next(
            (v for k, v in by_name.items() if company in k or k in company), None)
        o = db.scalar(select(StoreOrder).where(StoreOrder.store_id.in_(access.store_ids(user.tenant_id)),
                                               StoreOrder.order_id == order_no))
        try:
            if not o:
                raise HTTPException(400, "订单不存在，请先同步订单")
            if not cid:
                raise HTTPException(400, f"不认识的快递公司「{company}」")
            if o.store_id not in clients:
                clients[o.store_id] = _client(db, o)
            ship_order(db, o, ShipIn(delivery_id=cid, waybill_id=waybill), clients[o.store_id])
            results.append({"line": line.strip(), "order_id": order_no, "ok": True})
        except HTTPException as e:
            results.append({"line": line.strip(), "order_id": order_no, "ok": False, "error": e.detail})
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}


@router.get("/{oid}")
def order_detail(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o = db.get(StoreOrder, oid)
    if not o or not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)):
        raise HTTPException(404, "订单不存在")
    names = {s.id: s.name for s in _stores(db, user).values()}
    return {**order_out(o, names, _sources(db, user.tenant_id, [o])), "raw": o.raw, "decoded": o.decoded}
