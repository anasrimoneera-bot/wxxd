"""商品管理 API: products synced from each WeChat store, price / listing / delete via the WeChat API."""
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import (LibraryProduct, ProductSourceLink, PublishedRecord, SourcingJob, SourcingQuery, Store,
                      StoreProduct, User)
from ..security import decrypt_secret
from ..services import product_sync
from ..services.wechat import explain
from . import stores as stores_router
from .common import fmt_dt, page_params

router = APIRouter(prefix="/api/products", tags=["products"])


def _client(store: Store):
    return stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))


# 货源 state of a store product:
#   linked     - a 1688 source is set (设为货源)
#   none       - 无货源: searched in 找货源 but no source was set
#   unsearched - never searched yet
def _library_sources(tenant_id: int):
    """(store product id, ProductSourceLink) for store products published from a 商品云库 product
    that has a source: a source found in 商品云库 also counts in 商品管理."""
    return (select(StoreProduct.id, ProductSourceLink)
            .join(PublishedRecord, (PublishedRecord.store_id == StoreProduct.store_id)
                  & (PublishedRecord.wx_product_id == StoreProduct.wx_product_id))
            .join(LibraryProduct, (LibraryProduct.tenant_id == tenant_id)
                  & (LibraryProduct.platform + ":" + LibraryProduct.source_product_id == PublishedRecord.source_key))
            .join(ProductSourceLink, (ProductSourceLink.tenant_id == tenant_id)
                  & (ProductSourceLink.library_product_id == LibraryProduct.id))
            .where(StoreProduct.tenant_id == tenant_id, StoreProduct.wx_product_id != ""))


def _linked_ids(tenant_id: int):
    direct = select(ProductSourceLink.store_product_id).where(
        ProductSourceLink.tenant_id == tenant_id, ProductSourceLink.store_product_id.is_not(None))
    inherited = _library_sources(tenant_id).with_only_columns(StoreProduct.id)
    return direct.union(inherited)


def _searched_ids(tenant_id: int):
    return select(SourcingQuery.store_product_id).where(
        SourcingQuery.tenant_id == tenant_id, SourcingQuery.store_product_id.is_not(None),
        SourcingQuery.status == "done")


def _in_progress_ids(tenant_id: int):
    """Queued in a recent job; an hour-old pending query is treated as abandoned (e.g. a restart)."""
    return select(SourcingQuery.store_product_id).join(SourcingJob, SourcingJob.id == SourcingQuery.job_id).where(
        SourcingQuery.tenant_id == tenant_id, SourcingQuery.store_product_id.is_not(None),
        SourcingQuery.status == "pending", SourcingJob.created_at > datetime.now() - timedelta(hours=1))


def _filtered(user, store_id: int | None, status: int | None, keyword: str, source: str = ""):
    q = select(StoreProduct).where(StoreProduct.tenant_id == user.tenant_id)
    if store_id:
        q = q.where(StoreProduct.store_id == store_id)
    if status is not None:
        q = q.where(StoreProduct.status == status)
    if keyword:
        k = keyword.strip()
        q = q.where(StoreProduct.title.contains(k) | (StoreProduct.wx_product_id == k)
                    | (StoreProduct.spu_code == k))
    linked = StoreProduct.id.in_(_linked_ids(user.tenant_id))
    if source == "linked":
        q = q.where(linked)
    elif source == "none":
        q = q.where(~linked, StoreProduct.id.in_(_searched_ids(user.tenant_id)))
    elif source == "unsearched":
        q = q.where(~linked, ~StoreProduct.id.in_(_searched_ids(user.tenant_id)))
    return q


def product_out(p: StoreProduct, store_names: dict[int, str], sources: dict | None = None,
                searched: set | None = None) -> dict:
    src = (sources or {}).get(p.id)
    return {
        "source": src,
        "source_state": "linked" if src else "none" if p.id in (searched or set()) else "unsearched",
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
                  source: str = "", page: int = 1, size: int = 20, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    q = _filtered(user, store_id, status, keyword, source)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    no_source = db.scalar(select(func.count()).select_from(
        _filtered(user, store_id, status, keyword, "none").subquery()))
    unsearched = db.scalar(select(func.count()).select_from(
        _filtered(user, store_id, status, keyword, "unsearched").subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(StoreProduct.id.desc()).offset(off).limit(lim)).all()
    names = {sid: s.name for sid, s in _stores(db, user).items()}
    from .sourcing import sources_for
    ids = [p.id for p in rows]
    src = sources_for(db, user.tenant_id, ProductSourceLink.store_product_id, ids)
    from .sourcing import link_out
    missing = [i for i in ids if i not in src]
    if missing:  # no source set here: use the source found in 商品云库, if any
        for sp_id, link in db.execute(_library_sources(user.tenant_id).where(StoreProduct.id.in_(missing))):
            src.setdefault(sp_id, {**link_out(link), "from_library": True})
    searched = set(db.scalars(_searched_ids(user.tenant_id).where(SourcingQuery.store_product_id.in_(ids or [0]))))
    return {"total": total, "no_source_count": no_source, "unsearched_count": unsearched,
            "items": [product_out(p, names, src, searched) for p in rows]}


@router.get("/next-batch")
def next_batch(store_id: int | None = None, status: int | None = None, keyword: str = "", limit: int = 10,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    """找货源 without a selection: the next products in list order that have no source and have not
    been searched (or are being searched) yet."""
    limit = max(1, min(limit, 10))
    q = _filtered(user, store_id, status, keyword).where(
        ~StoreProduct.id.in_(_linked_ids(user.tenant_id)),
        ~StoreProduct.id.in_(_searched_ids(user.tenant_id)),
        ~StoreProduct.id.in_(_in_progress_ids(user.tenant_id)),
        StoreProduct.cover != "")
    rows = db.scalars(q.order_by(StoreProduct.id.desc()).limit(limit)).all()
    return [{"id": p.id, "title": p.title, "cover": p.cover} for p in rows]


@router.get("/no-source-ids")
def no_source_ids(store_id: int | None = None, status: int | None = None, keyword: str = "",
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Ids of 无货源 products (searched, no source set), for 删除全部无货源商品."""
    q = _filtered(user, store_id, status, keyword, "none").with_only_columns(StoreProduct.id)
    return list(db.scalars(q.order_by(StoreProduct.id.desc())))


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


class SkuStock(BaseModel):
    sku_id: str
    stock: int = Field(ge=0, le=100_000_000)


class StockIn(BaseModel):
    skus: list[SkuStock] = Field(min_length=1)


def _push_stock(store: Store, p: StoreProduct, changes: list[tuple[str, int, int]]):
    """changes: [(sku_id, diff_type, num)]. Sent to WeChat SKU by SKU, then mirrored locally."""
    c = _client(store)
    skus = {s["sku_id"]: dict(s) for s in p.skus or []}  # copies, so the JSON change is detected
    for sku_id, diff_type, num in changes:
        if sku_id not in skus:
            raise ValueError(f"规格 {sku_id} 不存在，请先同步商品")
        c.update_stock(p.wx_product_id, sku_id, diff_type, num)
        old = skus[sku_id].get("stock") or 0
        skus[sku_id]["stock"] = num if diff_type == 3 else old + num if diff_type == 1 else max(old - num, 0)
    p.skus = list(skus.values())
    p.stock = sum(s.get("stock") or 0 for s in p.skus)


@router.post("/{pid}/stock")
def set_stock(pid: int, body: StockIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """改库存: set each SKU's stock (synced to the WeChat store)."""
    p = _own(db, user, [pid])[0]
    store = db.get(Store, p.store_id)
    try:
        _push_stock(store, p, [(s.sku_id, 3, s.stock) for s in body.skus])
    except Exception as e:
        db.commit()  # keep the SKUs that did go through
        raise HTTPException(400, f"改库存失败：{explain(e)}")
    db.commit()
    return product_out(p, {store.id: store.name})


class BatchStockIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    mode: str = Field(pattern="^(set|add|sub)$")  # set every SKU to value / add value / subtract value
    value: int = Field(ge=0, le=100_000_000)


@router.post("/batch-stock")
def batch_stock(body: BatchStockIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    diff_type = {"set": 3, "add": 1, "sub": 2}[body.mode]
    if diff_type != 3 and body.value == 0:
        raise HTTPException(400, "数量必须大于 0")
    stores = _stores(db, user)
    results = []
    for p in _own(db, user, body.ids):
        try:
            _push_stock(stores[p.store_id], p, [(s["sku_id"], diff_type, body.value) for s in p.skus or []])
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
    rows = _own(db, user, body.ids)
    # 无货源 products (searched, no source set): when deleted, mark their 商品云库 copy so it is not listed again
    no_source = set(db.scalars(_filtered(user, None, None, "", "none").with_only_columns(StoreProduct.id)
                               .where(StoreProduct.id.in_([p.id for p in rows]))))
    deleted_no_source = []
    for p in rows:
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
                if p.id in no_source:
                    deleted_no_source.append(p)
                db.delete(p)
            results.append({"id": p.id, "ok": True})
        except Exception as e:
            results.append({"id": p.id, "ok": False, "error": explain(e)})
    for lib in product_sync.library_products_for(db, user.tenant_id, deleted_no_source):
        lib.no_source, lib.no_source_note = True, "商品管理中作为无货源商品删除"
    db.commit()
    return {"results": results}
