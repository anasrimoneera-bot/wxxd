"""订单管理 (phase 1): orders read from the WeChat Store API, with each line's 货源 (1688) for
procurement. Purchasing and shipping (发货) come next and build on this data."""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import ProductSourceLink, Store, StoreOrder, StoreProduct, User
from ..services import order_sync
from ..security import decrypt_secret
from . import stores as stores_router
from .common import fmt_dt, page_params

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _stores(db, user) -> dict[int, Store]:
    return {s.id: s for s in db.scalars(select(Store).where(Store.tenant_id == user.tenant_id))}


def _sources(db, tenant_id: int, orders: list[StoreOrder]) -> dict[tuple[int, str], dict]:
    """{(store id, WeChat product id): 货源} for the order lines: the store product's own source,
    else the source of the 商品云库 product it was published from."""
    from ..services.product_sync import library_products_for
    from .sourcing import link_out
    pairs = {(o.store_id, it["product_id"]) for o in orders for it in o.items or [] if it.get("product_id")}
    if not pairs:
        return {}
    sps = db.scalars(select(StoreProduct).where(StoreProduct.tenant_id == tenant_id, or_(*[
        (StoreProduct.store_id == s) & (StoreProduct.wx_product_id == p) for s, p in pairs]))).all()
    out = {}
    for sp in sps:
        link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == tenant_id,
                                                         ProductSourceLink.store_product_id == sp.id))
        if link is None:
            libs = library_products_for(db, tenant_id, [sp])
            if libs:
                link = db.scalar(select(ProductSourceLink).where(
                    ProductSourceLink.tenant_id == tenant_id,
                    ProductSourceLink.library_product_id.in_([p.id for p in libs if p])))
        if link is not None:
            out[(sp.store_id, sp.wx_product_id)] = link_out(link)
    return out


def order_out(o: StoreOrder, names: dict[int, str], sources: dict) -> dict:
    items, cost, known = [], 0, True
    for it in o.items or []:
        src = sources.get((o.store_id, it.get("product_id")))
        if src:
            cost += (src["price"] or 0) * (it.get("qty") or 1)
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
        # 货源成本 / 毛利 estimate (goods only, shipping not included); only when every line has a source
        "source_cost": cost if known and items else None,
        "margin": (o.order_price - cost) if known and items else None,
    }


@router.get("/stores")
def order_stores(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(StoreOrder.store_id, func.count()).where(StoreOrder.tenant_id == user.tenant_id)
                             .group_by(StoreOrder.store_id)).all())
    return [{"id": s.id, "name": s.name, "orders": counts.get(s.id, 0), "sync_status": s.order_sync_status,
             "sync_message": s.order_sync_message,
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
        if s.order_sync_status == "running":
            continue
        s.order_sync_status, s.order_sync_message = "running", "排队中…"
        started.append(sid)
    db.commit()
    for sid in started:
        background.add_task(order_sync.sync_orders, sid, stores_router.client_factory)
    return {"started": started}


@router.get("")
def list_orders(page: int = 1, size: int = 20, store_id: int | None = None, status: int | None = None,
                keyword: str = "", date_from: str = "", date_to: str = "",
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(StoreOrder).where(StoreOrder.tenant_id == user.tenant_id)
    if store_id:
        q = q.where(StoreOrder.store_id == store_id)
    if status is not None:
        q = q.where(StoreOrder.status == status)
    if keyword:
        k = keyword.strip()
        q = q.where(or_(StoreOrder.order_id.contains(k), StoreOrder.receiver_name.contains(k),
                        StoreOrder.receiver_phone.contains(k)))
    for v, op in ((date_from, "ge"), (date_to, "le")):
        if v:
            try:
                d = datetime.strptime(v[:10], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
            q = q.where(StoreOrder.create_time >= d) if op == "ge" else q.where(
                StoreOrder.create_time <= d.replace(hour=23, minute=59, second=59))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    counts = dict(db.execute(select(StoreOrder.status, func.count()).where(StoreOrder.tenant_id == user.tenant_id)
                             .group_by(StoreOrder.status)).all())
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(StoreOrder.create_time.desc(), StoreOrder.id.desc()).offset(off).limit(lim)).all()
    names = {s.id: s.name for s in _stores(db, user).values()}
    src = _sources(db, user.tenant_id, rows)
    return {"total": total, "status_counts": {str(k): v for k, v in counts.items()},
            "items": [order_out(o, names, src) for o in rows]}


@router.post("/{oid}/reveal")
def reveal_buyer(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """显示买家信息: decode the masked name / phone / address once (解码订单包含的敏感数据) and keep it
    on the order, so it is not decoded again (the API has a daily quota) and is ready for shipping."""
    from ..services.wechat import explain
    o = db.get(StoreOrder, oid)
    if not o or o.tenant_id != user.tenant_id:
        raise HTTPException(404, "订单不存在")
    if not o.revealed_at:
        store = db.get(Store, o.store_id)
        client = stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))
        try:
            resp = client.decode_order_sensitive(o.order_id)
        except Exception as e:
            raise HTTPException(400, f"获取买家信息失败：{explain(e)}")
        addr = resp.get("address_info") or (resp.get("order") or {}).get("address_info") or {}
        fields = order_sync.address_fields(addr)
        if not fields["receiver_name"] and not fields["receiver_phone"]:
            raise HTTPException(400, f"微信没有返回买家信息：{str(resp)[:300]}")
        for k, v in fields.items():
            setattr(o, k, v)
        o.revealed_at = datetime.now()
        db.commit()
    names = {s.id: s.name for s in _stores(db, user).values()}
    return order_out(o, names, _sources(db, user.tenant_id, [o]))


@router.get("/{oid}")
def order_detail(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    o = db.get(StoreOrder, oid)
    if not o or o.tenant_id != user.tenant_id:
        raise HTTPException(404, "订单不存在")
    names = {s.id: s.name for s in _stores(db, user).values()}
    return {**order_out(o, names, _sources(db, user.tenant_id, [o])), "raw": o.raw}
