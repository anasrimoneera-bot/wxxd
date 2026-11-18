"""1688 货源助手 (browser extension): pick a product, read 1688 pages in the user's own browser,
let Gemini check candidates, and save the 货源 (per-spec 货源价 + 运费) with one click.

The extension logs in with /api/auth/login and calls these with the Bearer token. A product is
("store", StoreProduct id) or ("library", LibraryProduct id)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import access
from ..db import get_db
from ..deps import current_user
from ..models import LibraryProduct, ProductSourceLink, Store, StoreProduct, User
from ..services import library_sync, vision

router = APIRouter(prefix="/api/ext", tags=["1688 货源助手"])


def _spec_label(attrs) -> str:
    if isinstance(attrs, str):  # store product: '颜色:红色；尺码:XL'
        return " / ".join(p.split(":", 1)[-1] for p in attrs.split("；") if p)
    return " / ".join(str(a.get("value")) for a in attrs or [])  # 商品云库: [{name, value}]


def _store_item(db, p: StoreProduct, names: dict) -> dict:
    return {"kind": "store", "id": p.id, "title": p.title, "image": p.cover, "store": names.get(p.store_id, ""),
            "price_min": p.price_min, "price_max": p.price_max,
            "specs": [{"key": k["sku_id"], "label": _spec_label(k.get("attrs")) or "默认", "price": k.get("price")}
                      for k in p.skus or []]}


def _library_item(p: LibraryProduct) -> dict:
    return {"kind": "library", "id": p.id, "title": p.ai_title or p.title, "image": p.cover, "store": "商品云库",
            "price_min": p.price_min, "price_max": p.price_max,
            "specs": [{"key": str(k.get("source_sku_id")), "label": _spec_label(k.get("attrs")) or "默认",
                       "price": k.get("price")} for k in (p.normalized or {}).get("skus") or []]}


def _get(db, user, kind: str, pid: int):
    if kind == "store":
        p = db.get(StoreProduct, pid)
        if not p or not access.can_use(db, user.tenant_id, db.get(Store, p.store_id)):
            raise HTTPException(404, "商品不存在")
        return p
    if kind == "library":
        p = db.get(LibraryProduct, pid)
        if not p or p.tenant_id != user.tenant_id:
            raise HTTPException(404, "商品不存在")
        return p
    raise HTTPException(400, "kind 只能是 store / library")


def _current_source(db, user, kind, p) -> dict | None:
    from .sourcing import link_out
    if kind == "store":
        link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.store_product_id == p.id))
    else:
        link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == user.tenant_id,
                                                         ProductSourceLink.library_product_id == p.id))
    return link_out(link)


@router.get("/queue")
def queue(kind: str = "store", q: str = "", store_id: int | None = None, missing: bool = True, limit: int = 50,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Products to find a 货源 for: by default those without one; q searches title / id."""
    limit = max(1, min(limit, 200))
    if kind == "library":
        sel = select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                           LibraryProduct.no_source == False)  # noqa: E712
        if missing:
            sel = sel.where(~LibraryProduct.id.in_(select(ProductSourceLink.library_product_id).where(
                ProductSourceLink.tenant_id == user.tenant_id, ProductSourceLink.library_product_id.is_not(None))))
        if q:
            sel = sel.where(or_(LibraryProduct.title.contains(q), LibraryProduct.source_product_id == q.strip()))
        rows = db.scalars(sel.order_by(LibraryProduct.id.desc()).limit(limit)).all()
        return {"items": [_library_item(p) for p in rows]}
    from .products import _linked_ids
    sel = select(StoreProduct).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)))
    if store_id:
        sel = sel.where(StoreProduct.store_id == store_id)
    if missing:
        sel = sel.where(~StoreProduct.id.in_(_linked_ids(user.tenant_id)))
    if q:
        sel = sel.where(or_(StoreProduct.title.contains(q), StoreProduct.wx_product_id == q.strip()))
    rows = db.scalars(sel.order_by(StoreProduct.id.desc()).limit(limit)).all()
    names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
    return {"items": [_store_item(db, p, names) for p in rows],
            "stores": [{"id": sid, "name": n} for sid, n in names.items()]}


@router.get("/item")
def item(kind: str, id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, user, kind, id)
    if kind == "store":
        names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
        out = _store_item(db, p, names)
    else:
        out = _library_item(p)
    return {**out, "source": _current_source(db, user, kind, p), "vision": vision.configured()}


class Candidate(BaseModel):
    key: str = Field(max_length=64)
    image_url: str = Field(max_length=2048)
    title: str = Field(default="", max_length=300)


class VerifyIn(BaseModel):
    kind: str
    id: int
    candidates: list[Candidate] = Field(min_length=1, max_length=vision.MAX_CANDIDATES)


@router.post("/verify")
def verify(body: VerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """AI 核对: Gemini compares our main image + title with each candidate (同款 / 相似 / 不同)."""
    p = _get(db, user, body.kind, body.id)
    title = p.title if body.kind == "store" else (p.ai_title or p.title)
    if not p.cover:
        raise HTTPException(400, "这个商品没有主图")
    try:
        results = vision.compare(p.cover, title, [c.model_dump() for c in body.candidates])
    except vision.VisionError as e:
        raise HTTPException(400, str(e))
    return {"results": results}


class Offer(BaseModel):
    offer_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=255)
    image_url: str = Field(default="", max_length=1024)
    detail_url: str = Field(default="", max_length=1024)
    supplier: str = Field(default="", max_length=255)
    price: float = Field(gt=0, le=1_000_000)  # yuan: the lowest / default 货源价
    moq: int | None = None


class LinkIn(BaseModel):
    kind: str
    id: int
    offer: Offer
    sku_prices: dict[str, float] = Field(default_factory=dict)  # our spec key -> yuan (from the 1688 SKU)
    freight: float | None = Field(default=None, ge=0, le=100000)  # yuan: 1688 运费


@router.post("/link")
def link(body: LinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """设为货源 from a 1688 page: the offer, each spec's 货源价 and the 运费. Written back to 商品云库 and
    to the product in every store, like 找货源 → 设为货源."""
    from .sourcing import link_out
    p = _get(db, user, body.kind, body.id)
    if any(v <= 0 for v in body.sku_prices.values()):
        raise HTTPException(400, "货源价必须大于 0")
    col = ProductSourceLink.store_product_id if body.kind == "store" else ProductSourceLink.library_product_id
    sel = select(ProductSourceLink).where(col == p.id)
    if body.kind == "library":
        sel = sel.where(ProductSourceLink.tenant_id == user.tenant_id)
    lk = db.scalar(sel)
    if lk is None:
        lk = ProductSourceLink(tenant_id=user.tenant_id, store_product_id=p.id if body.kind == "store" else None,
                               library_product_id=p.id if body.kind == "library" else None)
        db.add(lk)
    o = body.offer
    lk.offer_id, lk.title, lk.image_url = o.offer_id, o.title[:255], o.image_url
    lk.detail_url = o.detail_url or f"https://detail.1688.com/offer/{o.offer_id}.html"
    lk.supplier, lk.moq, lk.unit = o.supplier[:255], o.moq, lk.unit or ""
    prices = {k: round(v * 100) for k, v in body.sku_prices.items()}
    lk.price = min(prices.values()) if prices else round(o.price * 100)
    lk.sku_prices = prices or None
    lk.original_price = None
    lk.freight = round(body.freight * 100) if body.freight is not None else None
    lk.linked_at = datetime.now()
    if body.kind == "library" and p.no_source:
        p.no_source, p.no_source_note = False, ""
    db.flush()
    library_sync.sync_source(db, lk)  # store 货源 -> 商品云库 -> the product in the other stores
    library_sync.propagate_source(db, lk)  # 云库 货源 -> every store
    db.commit()
    return {"ok": True, "source": link_out(lk)}
