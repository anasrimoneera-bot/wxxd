"""商品管理 API: products synced from each WeChat store, price / listing / delete via the WeChat API."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Store, StoreProduct, User
from ..security import decrypt_secret
from ..services import product_sync
from ..services.wechat import explain
from . import stores as stores_router
from .common import fmt_dt, page_params

router = APIRouter(prefix="/api/products", tags=["products"])


def _client(store: Store):
    return stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))


def product_out(p: StoreProduct, store_names: dict[int, str]) -> dict:
    return {
        "id": p.id, "store_id": p.store_id, "store_name": store_names.get(p.store_id, ""),
        "wx_product_id": p.wx_product_id, "title": p.title, "cover": p.cover, "spu_code": p.spu_code,
        "status": p.status, "status_name": product_sync.status_name(p.status, p.edit_status),
        "price_min": p.price_min, "price_max": p.price_max, "stock": p.stock, "skus": p.skus or [],
        "synced_at": fmt_dt(p.synced_at),
    }


def _stores(db, user) -> dict[int, Store]:
    return {s.id: s for s in db.scalars(select(Store).where(Store.tenant_id == user.tenant_id))}


@router.get("/stores")
def store_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Stores with product counts and sync state, for the left-hand store list."""
    counts = dict(db.execute(select(StoreProduct.store_id, func.count()).where(
        StoreProduct.tenant_id == user.tenant_id).group_by(StoreProduct.store_id)).all())
    on_sale = dict(db.execute(select(StoreProduct.store_id, func.count()).where(
        StoreProduct.tenant_id == user.tenant_id, StoreProduct.status == product_sync.ON_SALE)
        .group_by(StoreProduct.store_id)).all())
    return [{"id": s.id, "name": s.name, "logo": s.logo, "count": counts.get(s.id, 0),
             "on_sale": on_sale.get(s.id, 0), "sync_status": s.sync_status, "sync_message": s.sync_message,
             "synced_at": fmt_dt(s.synced_at)} for s in _stores(db, user).values()]


class SyncIn(BaseModel):
    store_ids: list[int] = Field(min_length=1)


@router.post("/sync")
def start_sync(body: SyncIn, background: BackgroundTasks, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    stores = _stores(db, user)
    started = []
    for sid in body.store_ids:
        s = stores.get(sid)
        if not s:
            raise HTTPException(404, "店铺不存在")
        if s.sync_status == "running":
            continue
        s.sync_status, s.sync_message = "running", "排队中…"
        started.append(sid)
    db.commit()
    for sid in started:
        background.add_task(product_sync.sync_store, sid, stores_router.client_factory)
    return {"started": started}


@router.get("")
def list_products(store_id: int | None = None, status: int | None = None, keyword: str = "",
                  page: int = 1, size: int = 20, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    q = select(StoreProduct).where(StoreProduct.tenant_id == user.tenant_id)
    if store_id:
        q = q.where(StoreProduct.store_id == store_id)
    if status is not None:
        q = q.where(StoreProduct.status == status)
    if keyword:
        k = keyword.strip()
        q = q.where(StoreProduct.title.contains(k) | (StoreProduct.wx_product_id == k)
                    | (StoreProduct.spu_code == k))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(StoreProduct.id.desc()).offset(off).limit(lim)).all()
    names = {sid: s.name for sid, s in _stores(db, user).items()}
    return {"total": total, "items": [product_out(p, names) for p in rows]}


def _own(db, user, ids: list[int]) -> list[StoreProduct]:
    rows = db.scalars(select(StoreProduct).where(StoreProduct.tenant_id == user.tenant_id,
                                                 StoreProduct.id.in_(ids))).all()
    if not rows:
        raise HTTPException(404, "商品不存在")
    return rows


class SkuPrice(BaseModel):
    sku_id: str
    price: float = Field(gt=0, le=1_000_000)  # yuan


class PriceIn(BaseModel):
    skus: list[SkuPrice] = Field(min_length=1)


def _push_prices(db, store: Store, p: StoreProduct, new: dict[str, int]):
    skus = [{"sku_id": s["sku_id"], "sale_price": new.get(s["sku_id"], s["price"])} for s in p.skus or []]
    _client(store).update_prices(p.wx_product_id, skus)
    p.skus = [{**s, "price": new.get(s["sku_id"], s["price"])} for s in p.skus or []]
    prices = [s["price"] for s in p.skus if s["price"]] or [0]
    p.price_min, p.price_max = min(prices), max(prices)


@router.post("/{pid}/price")
def set_price(pid: int, body: PriceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _own(db, user, [pid])[0]
    store = db.get(Store, p.store_id)
    try:
        _push_prices(db, store, p, {s.sku_id: round(s.price * 100) for s in body.skus})
    except Exception as e:
        raise HTTPException(400, f"改价失败：{explain(e)}")
    db.commit()
    return product_out(p, {store.id: store.name})


class BatchPriceIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    mode: str = Field(pattern="^(set|multiply|add)$")  # set price / multiply / add yuan
    value: float


def _new_price(fen: int, mode: str, value: float) -> int:
    if mode == "set":
        new = value * 100
    elif mode == "multiply":
        new = fen * value
    else:
        new = fen + value * 100
    return max(int(round(new)), 1)


@router.post("/batch-price")
def batch_price(body: BatchPriceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.mode == "set" and body.value <= 0 or body.mode == "multiply" and body.value <= 0:
        raise HTTPException(400, "数值必须大于 0")
    stores = _stores(db, user)
    results = []
    for p in _own(db, user, body.ids):
        try:
            _push_prices(db, stores[p.store_id], p,
                         {s["sku_id"]: _new_price(s["price"], body.mode, body.value) for s in p.skus or []})
            results.append({"id": p.id, "ok": True})
        except Exception as e:
            results.append({"id": p.id, "ok": False, "error": explain(e)})
    db.commit()
    return {"results": results}


class ActionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(listing|delisting|delete)$")


@router.post("/batch-action")
def batch_action(body: ActionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stores = _stores(db, user)
    results = []
    for p in _own(db, user, body.ids):
        c = _client(stores[p.store_id])
        try:
            if body.action == "listing":
                c.listing(p.wx_product_id)
                p.status = product_sync.ON_SALE
            elif body.action == "delisting":
                c.delisting(p.wx_product_id)
                p.status = product_sync.DELISTED
            else:
                if p.status == product_sync.ON_SALE:  # WeChat only deletes products that are not on sale
                    c.delisting(p.wx_product_id)
                c.delete_product(p.wx_product_id)
                db.delete(p)
            results.append({"id": p.id, "ok": True})
        except Exception as e:
            results.append({"id": p.id, "ok": False, "error": explain(e)})
    db.commit()
    return {"results": results}
