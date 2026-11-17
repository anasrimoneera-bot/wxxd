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
from .. import access
from ..services import library_sync
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
            # the 商品云库 product of this account, or of the store's owner (assigned stores)
            .join(LibraryProduct, ((LibraryProduct.tenant_id == tenant_id) | (LibraryProduct.tenant_id == StoreProduct.tenant_id))
                  & (LibraryProduct.platform + ":" + LibraryProduct.source_product_id == PublishedRecord.source_key))
            .join(ProductSourceLink, (ProductSourceLink.tenant_id == LibraryProduct.tenant_id)
                  & (ProductSourceLink.library_product_id == LibraryProduct.id))
            .where(StoreProduct.store_id.in_(access.store_ids(tenant_id)), StoreProduct.wx_product_id != ""))


def _linked_ids(tenant_id: int):
    # a store product's own 货源 belongs to the store: whoever set it, every account using the store sees it
    direct = select(ProductSourceLink.store_product_id).where(ProductSourceLink.store_product_id.is_not(None))
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
    q = select(StoreProduct).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)))
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


def deal(p: StoreProduct, coupon_list) -> dict:
    """到手价: the listed price minus the store's best coupon for this product."""
    from ..services import coupons as cp
    if not coupon_list:
        return {"deal_min": None, "deal_max": None, "coupon": None}
    lo, used_lo = cp.best(p.price_min, coupon_list, p.wx_product_id)
    hi, _ = cp.best(p.price_max, coupon_list, p.wx_product_id)
    if used_lo is None:
        return {"deal_min": None, "deal_max": None, "coupon": None}
    return {"deal_min": lo, "deal_max": hi,
            "coupon": {"name": used_lo.name, "type_name": cp.TYPE_NAMES.get(used_lo.type, str(used_lo.type)),
                       "off": p.price_min - lo}}


def _library_prices(tenant_id: int, ids: list[int]):
    """(store product id, 云库 price min, max): the 商品云库 product each store product was published from."""
    return (select(StoreProduct.id, LibraryProduct.price_min, LibraryProduct.price_max)
            .join(PublishedRecord, (PublishedRecord.store_id == StoreProduct.store_id)
                  & (PublishedRecord.wx_product_id == StoreProduct.wx_product_id))
            .join(LibraryProduct, ((LibraryProduct.tenant_id == tenant_id) | (LibraryProduct.tenant_id == StoreProduct.tenant_id))
                  & (LibraryProduct.platform + ":" + LibraryProduct.source_product_id == PublishedRecord.source_key))
            .where(StoreProduct.id.in_(ids or [0])))


def product_out(p: StoreProduct, store_names: dict[int, str], sources: dict | None = None,
                searched: set | None = None, coupon_map: dict | None = None, lib_prices: dict | None = None) -> dict:
    src = (sources or {}).get(p.id)
    lp = (lib_prices or {}).get(p.id)
    return {
        # the price of the 商品云库 product it was published from (the source product's price)
        "library_price_min": lp[0] if lp else None, "library_price_max": lp[1] if lp else None,
        **deal(p, (coupon_map or {}).get(p.store_id)),
        "source": src,
        "source_state": "linked" if src else "none" if p.id in (searched or set()) else "unsearched",
        "id": p.id, "store_id": p.store_id, "store_name": store_names.get(p.store_id, ""),
        "wx_product_id": p.wx_product_id, "title": p.title, "cover": p.cover, "spu_code": p.spu_code,
        "status": p.status, "status_name": product_sync.status_name(p.status, p.edit_status),
        "price_min": p.price_min, "price_max": p.price_max, "stock": p.stock, "skus": p.skus or [],
        "synced_at": fmt_dt(p.synced_at),
    }


def _stores(db, user) -> dict[int, Store]:
    return access.stores(db, user.tenant_id)  # own stores and stores assigned in 用户管理


@router.get("/stores")
def store_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Stores with product counts and sync state, for the left-hand store list."""
    counts = dict(db.execute(select(StoreProduct.store_id, func.count()).where(
        StoreProduct.store_id.in_(access.store_ids(user.tenant_id))).group_by(StoreProduct.store_id)).all())
    on_sale = dict(db.execute(select(StoreProduct.store_id, func.count()).where(
        StoreProduct.store_id.in_(access.store_ids(user.tenant_id)), StoreProduct.status == product_sync.ON_SALE)
        .group_by(StoreProduct.store_id)).all())
    return [{"id": s.id, "name": s.name, "logo": s.logo, "count": counts.get(s.id, 0),
             "on_sale": on_sale.get(s.id, 0),
             **dict(zip(("sync_status", "sync_message"),
                        product_sync.live_status(s.sync_status, s.sync_message, s.sync_heartbeat))),
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
        if product_sync.live_status(s.sync_status, s.sync_message, s.sync_heartbeat)[0] == "running":
            continue
        s.sync_status, s.sync_message = "running", "排队中…"
        s.sync_heartbeat = datetime.now()
        started.append(sid)
    db.commit()
    for sid in started:
        background.add_task(product_sync.sync_store, sid, stores_router.client_factory)
    return {"started": started}


def _sources_of(db, user, ids: list[int]) -> dict:
    """{store product id: 货源}: its own, else the one of its 商品云库 product."""
    from .sourcing import link_out, sources_for
    src = sources_for(db, user.tenant_id, ProductSourceLink.store_product_id, ids)
    missing = [i for i in ids if i not in src]
    if missing:  # no source set here: use the source found in 商品云库, if any
        for sp_id, link in db.execute(_library_sources(user.tenant_id).where(StoreProduct.id.in_(missing))):
            if sp_id not in src:  # spec prices of the 云库 货源, keyed by this store product's specs
                keyed = library_sync.store_sku_prices(db, link, db.get(StoreProduct, sp_id))
                src[sp_id] = {**link_out(link, keyed), "from_library": True}
    return src


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
    from ..services import coupons as cp
    ids = [p.id for p in rows]
    src = _sources_of(db, user, ids)
    searched = set(db.scalars(_searched_ids(user.tenant_id).where(SourcingQuery.store_product_id.in_(ids or [0]))))
    cmap = cp.by_store(db, {p.store_id for p in rows})
    lib_prices = {sp_id: (lo, hi) for sp_id, lo, hi in db.execute(_library_prices(user.tenant_id, ids))}
    return {"total": total, "no_source_count": no_source, "unsearched_count": unsearched,
            "items": [product_out(p, names, src, searched, cmap, lib_prices) for p in rows]}


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
    rows = db.scalars(select(StoreProduct).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)),
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
    library_sync.sync_prices(db, p)  # the new price also goes back to 商品云库
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
            library_sync.sync_prices(db, p)
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


# ---- 优惠券 / 公式改价 ------------------------------------------------------------
@router.post("/coupons/sync")
def sync_coupons_now(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Read the store's active coupons now (also done at the end of every product sync)."""
    from .. import access
    from ..services import coupons as cp
    store = access.get_store(db, user, store_id)
    try:
        n = cp.sync_store_coupons(db, store, _client(store))
    except Exception as e:
        raise HTTPException(400, f"读取优惠券失败：{explain(e)}")
    db.commit()
    return {"count": n, **list_coupons(store_id, user, db)}


@router.get("/coupons")
def list_coupons(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .. import access
    from ..models import StoreCoupon
    from ..services import coupons as cp
    access.get_store(db, user, store_id)
    rows = db.scalars(select(StoreCoupon).where(StoreCoupon.store_id == store_id).order_by(StoreCoupon.id)).all()
    return {"items": [{"coupon_id": c.coupon_id, "name": c.name, "type": c.type,
                       "type_name": cp.TYPE_NAMES.get(c.type, str(c.type)), "discount_fee": c.discount_fee,
                       "discount_num": c.discount_num, "threshold": c.threshold,
                       "products": len(c.product_ids or []), "end_time": fmt_dt(c.end_time) if c.end_time else "",
                       "raw": c.raw} for c in rows]}


class FormulaIn(BaseModel):
    # the selected products; empty = every product of the current list (store / status / keyword / 货源 filter)
    ids: list[int] = Field(default_factory=list, max_length=2000)
    store_id: int | None = None
    status: int | None = None
    keyword: str = ""
    source: str = ""
    margin: float = Field(gt=0, le=100)  # 利润倍率: 货源价 × margin = the price the buyer pays after coupons
    add: float = Field(default=0, ge=-100000, le=100000)  # yuan added on top (optional)


def formula_price(source: int, margin: float, add: int, coupon_list, product_id: str) -> tuple[int, int]:
    """(new price, coupon value): the lowest price whose 到手价 (after the best applicable coupon) is
    still 货源价 × 利润倍率 (+ add). Coupon value = new price − that target."""
    from ..services import coupons as cp
    target = max(round(source * margin) + add, 1)
    price = target
    for _ in range(30):  # raise the price by what the best coupon still takes off, until it is covered
        got, _c = cp.best(price, coupon_list or [], product_id)
        if got >= target:
            break
        price += target - got
    return price, price - target


@router.post("/formula-preview")
def formula_preview(body: FormulaIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """新售价 = 货源价 × 利润倍率 + 优惠券金额, for the cheapest SKU; the other SKUs keep their price
    difference to it. Each product is compared with its 商品云库 selling price (the current price when it
    was not published from 商品云库): 超出 when the new one is higher."""
    from ..services import coupons as cp
    if body.ids:
        rows = _own(db, user, body.ids)
    else:  # 批量: the whole current list
        rows = db.scalars(_filtered(user, body.store_id, body.status, body.keyword, body.source)
                          .order_by(StoreProduct.id.desc()).limit(2000)).all()
    src = _sources_of(db, user, [p.id for p in rows])
    cmap = cp.by_store(db, {p.store_id for p in rows})
    lib = {sp_id: (lo, hi) for sp_id, lo, hi in db.execute(_library_prices(user.tenant_id, [p.id for p in rows]))}
    out, skipped = [], 0
    for p in rows:
        s = src.get(p.id)
        if not body.ids and (not s or not s.get("price")):
            skipped += 1  # 批量: products without a 货源 are skipped
            continue
        lp = lib.get(p.id)
        # compared with the 商品云库 selling price (the source product's price); the current price if none
        base = lp[0] if lp and lp[0] else p.price_min
        item = {"id": p.id, "title": p.title, "cover": p.cover, "store_id": p.store_id,
                "current_min": p.price_min, "current_max": p.price_max,
                "library_min": lp[0] if lp else None, "library_max": lp[1] if lp else None,
                "compare_min": base, "compare_with": "云库售价" if lp and lp[0] else "现价"}
        if not s or not s.get("price"):
            out.append({**item, "error": "没有货源价"})
            continue
        sku_src = s.get("sku_prices") or {}
        new_min, coupon_value = formula_price(s["price"], body.margin, round(body.add * 100),
                                              cmap.get(p.store_id), p.wx_product_id)
        delta = new_min - p.price_min
        skus = []
        for k in p.skus or []:
            if sku_src:  # 货源价 per spec: every spec by the formula from its own 货源价 (base when none)
                new, cv = formula_price(sku_src.get(k["sku_id"]) or s["price"], body.margin, round(body.add * 100),
                                        cmap.get(p.store_id), p.wx_product_id)
            else:  # one 货源价: the cheapest spec by the formula, the others keep their difference
                new, cv = max(k["price"] + delta, 1), coupon_value
            skus.append({"sku_id": k["sku_id"], "attrs": k.get("attrs", ""), "current": k["price"],
                         "source": sku_src.get(k["sku_id"]) or s["price"], "new": new, "coupon": cv})
        if sku_src and skus:
            cheapest = min(skus, key=lambda k: k["new"])
            new_min, coupon_value = cheapest["new"], cheapest["coupon"]
        # the 云库 price is compared with what the buyer pays (到手价 = new price − coupon);
        # without a 云库 price, the new price with the current price
        compare = (new_min - coupon_value) if item["compare_with"] == "云库售价" else new_min
        out.append({**item, "source_price": s["price"], "coupon_value": coupon_value, "new_min": new_min,
                    "new_max": max([k["new"] for k in skus] or [new_min]), "deal_min": new_min - coupon_value,
                    "over": compare - base, "skus": skus})
    return {"items": out, "skipped_no_source": skipped}


class ApplyItem(BaseModel):
    id: int
    prices: dict[str, int]  # sku_id -> fen


class ApplyIn(BaseModel):
    items: list[ApplyItem] = Field(min_length=1, max_length=2000)


@router.post("/apply-prices")
def apply_prices(body: ApplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Set the given SKU prices (from 公式改价) in the WeChat stores; written back to 商品云库."""
    stores = _stores(db, user)
    rows = {p.id: p for p in _own(db, user, [i.id for i in body.items])}
    results = []
    for it in body.items:
        p = rows.get(it.id)
        if not p:
            results.append({"id": it.id, "ok": False, "error": "商品不存在"})
            continue
        try:
            _push_prices(db, stores[p.store_id], p, {k: max(int(v), 1) for k, v in it.prices.items()})
            library_sync.sync_prices(db, p)
            results.append({"id": p.id, "ok": True})
        except Exception as e:
            results.append({"id": p.id, "ok": False, "error": explain(e)})
    db.commit()
    return {"results": results}
