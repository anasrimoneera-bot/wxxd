"""货源 kept on products: 修改货源价 (whole / per spec) and 取消货源. 货源 are set from the
1688 货源助手 browser extension (routers/ext.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import ProductSourceLink, Store, StoreProduct, User
from ..services import library_sync
from .common import fmt_dt

router = APIRouter(prefix="/api/sourcing", tags=["sourcing"])


def link_out(link: ProductSourceLink | None, sku_prices: dict | None = None) -> dict | None:
    """sku_prices: the spec prices keyed for the product shown (defaults to the link's own keys)."""
    if not link:
        return None
    sp_prices = dict(link.sku_prices or {}) if sku_prices is None else sku_prices
    vals = list(sp_prices.values()) + [link.price]
    return {"sku_prices": sp_prices, "price_min": min(vals), "price_max": max(vals), "id": link.id,
            "freight": link.freight, "offer_id": link.offer_id, "title": link.title, "image_url": link.image_url,
            "detail_url": link.detail_url, "supplier": link.supplier, "price": link.price,
            "moq": link.moq, "unit": link.unit, "linked_at": fmt_dt(link.linked_at),
            "original_price": link.original_price,
            "price_edited": link.original_price is not None and link.original_price != link.price}


class UnlinkIn(BaseModel):
    library_product_id: int | None = None
    store_product_id: int | None = None


def _find_link(db, user, lib_id, sp_id) -> tuple[ProductSourceLink, bool]:
    """The source of a product; for a 商品管理 product without its own source, the 商品云库 one.
    Returns (link, from_library)."""
    if not (lib_id or sp_id):
        raise HTTPException(400, "请指定商品")
    col, target = ((ProductSourceLink.library_product_id, lib_id) if lib_id
                   else (ProductSourceLink.store_product_id, sp_id))
    from .. import access
    lq = select(ProductSourceLink).where(col == target)
    if lib_id:
        lq = lq.where(ProductSourceLink.tenant_id == user.tenant_id)
    else:  # a store product's own 货源 is shared by every account that uses the store
        sp = db.get(StoreProduct, sp_id)
        if not sp or not access.can_use(db, user.tenant_id, db.get(Store, sp.store_id)):
            raise HTTPException(404, "商品不存在")
    link = db.scalar(lq)
    if link is None and sp_id:
        from ..services.product_sync import library_products_for
        libs = library_products_for(db, user.tenant_id, [sp])
        link = db.scalar(select(ProductSourceLink).where(
            ProductSourceLink.library_product_id.in_([p.id for p in libs] or [0]),
            ProductSourceLink.tenant_id.in_([p.tenant_id for p in libs] or [0])))
    if link is None:
        raise HTTPException(404, "该商品没有设置货源")
    return link, bool(link.library_product_id) and not lib_id


@router.post("/unlink")
def cancel_source(body: UnlinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """取消货源. For a 商品管理 product whose source comes from 商品云库, the 商品云库 source is cancelled."""
    link, from_library = _find_link(db, user, body.library_product_id, body.store_product_id)
    db.delete(link)
    db.commit()
    return {"ok": True, "from_library": from_library}


class SourcePriceIn(BaseModel):
    library_product_id: int | None = None
    store_product_id: int | None = None
    price: float = Field(gt=0, le=1_000_000)  # yuan


@router.post("/price")
def set_source_price(body: SourcePriceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """修改货源价: change only the price; the 1688 offer (link) stays the same.
    A 商品管理 product whose source comes from 商品云库 changes the 商品云库 source."""
    link, from_library = _find_link(db, user, body.library_product_id, body.store_product_id)
    if link.original_price is None:
        link.original_price = link.price  # remember the 1688 price the first time it is changed
    link.price = round(body.price * 100)
    library_sync.sync_source(db, link)  # store 货源价 -> 商品云库 -> the product's other stores
    library_sync.propagate_source(db, link)  # 云库 货源价 -> every store
    db.commit()
    return {**link_out(link), "from_library": from_library}


class SkuPricesIn(BaseModel):
    library_product_id: int | None = None
    store_product_id: int | None = None
    prices: dict[str, float]  # sku id of that product -> yuan (each spec's 货源价)


@router.post("/sku-prices")
def set_sku_prices(body: SkuPricesIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """货源价 per spec. The base 货源价 becomes the lowest one; the 1688 price is kept the first time.
    Written back to 商品云库 and on to the product in every store, like 修改货源价."""
    if any(v <= 0 or v > 1_000_000 for v in body.prices.values()):
        raise HTTPException(400, "货源价必须大于 0")
    link, from_library = _find_link(db, user, body.library_product_id, body.store_product_id)
    sp = db.get(StoreProduct, body.store_product_id) if body.store_product_id else None
    prices = {k: round(v * 100) for k, v in body.prices.items()}
    if link.original_price is None:
        link.original_price = link.price
    if sp is not None and link.library_product_id:  # a 云库 货源 shown on a store product: translate the keys
        library_sync.set_store_sku_prices(db, link, sp, prices)
    else:
        link.sku_prices = {**(link.sku_prices or {}), **prices}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(link, "sku_prices")
    link.price = min(link.sku_prices.values()) if link.sku_prices else link.price
    db.flush()
    library_sync.sync_source(db, link)
    library_sync.propagate_source(db, link)
    db.commit()
    keyed = library_sync.store_sku_prices(db, link, sp) if sp is not None else None
    return {**link_out(link, keyed), "from_library": from_library}


def sources_for(db, tenant_id: int, column, ids: list[int]) -> dict[int, dict]:
    """{product id: source} for the product lists (商品云库 / 商品管理)."""
    if not ids:
        return {}
    q = select(ProductSourceLink).where(column.in_(ids))
    if column is not ProductSourceLink.store_product_id:  # store products' 货源 belong to the store (shared)
        q = q.where(ProductSourceLink.tenant_id == tenant_id)
    rows = db.scalars(q)
    key = "library_product_id" if column is ProductSourceLink.library_product_id else "store_product_id"
    return {getattr(r, key): link_out(r) for r in rows}
