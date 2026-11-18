"""Changes made to a product in a store (商品管理) are written back to its 商品云库 product:

- 货源: a 1688 source set or edited (修改货源价) on a store product is copied to the library product,
  so publishing it to other stores and 找货源 in 商品云库 see the same source.
- 改价: a store's new SKU prices are kept on the library product (sale_prices, by source SKU id)
  and used as the sale price the next time it is published, instead of the template's formula.

A store product is matched to its library product through PublishedRecord (store + WeChat product id).
"""
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from ..models import LibraryProduct, ProductSourceLink, StoreProduct
from .product_sync import library_products_for

LINK_FIELDS = ("offer_id", "title", "image_url", "detail_url", "supplier", "price", "original_price", "moq", "unit",
               "freight")


def _libs(db, sp: StoreProduct) -> list[LibraryProduct]:
    return [p for p in library_products_for(db, sp.tenant_id, [sp]) if p]


def sync_source(db, link: ProductSourceLink) -> int:
    """Copy a store product's 货源 to its 商品云库 product(s). Returns how many were updated."""
    if not link.store_product_id:
        return 0
    sp = db.get(StoreProduct, link.store_product_id)
    if not sp:
        return 0
    n = 0
    for lib in _libs(db, sp):
        own = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == lib.tenant_id,
                                                        ProductSourceLink.library_product_id == lib.id))
        if own is None:
            own = ProductSourceLink(tenant_id=lib.tenant_id, library_product_id=lib.id)
            db.add(own)
        for f in LINK_FIELDS:
            setattr(own, f, getattr(link, f))
        m = sku_map(sp, lib)  # spec prices: store sku ids -> 云库 source sku ids
        own.sku_prices = {m[k]: v for k, v in (link.sku_prices or {}).items() if k in m} or None
        own.linked_at = datetime.now()
        if lib.no_source:
            lib.no_source, lib.no_source_note = False, ""
        db.flush()
        propagate_source(db, own, skip_store_product=sp.id)  # ... and on to the product's other stores
        n += 1
    return n


def store_products_of(db, lib: LibraryProduct) -> list[StoreProduct]:
    """The store products published from a 商品云库 product, in every store the account may use."""
    from .. import access
    from ..models import PublishedRecord
    recs = db.execute(select(PublishedRecord.store_id, PublishedRecord.wx_product_id).where(
        PublishedRecord.source_key == f"{lib.platform}:{lib.source_product_id}",
        PublishedRecord.store_id.in_(access.store_ids(lib.tenant_id)), PublishedRecord.wx_product_id != "")).all()
    out = []
    for store_id, wx in recs:
        sp = db.scalar(select(StoreProduct).where(StoreProduct.store_id == store_id, StoreProduct.wx_product_id == wx))
        if sp:
            out.append(sp)
    return out


def propagate_source(db, lib_link: ProductSourceLink, skip_store_product: int | None = None) -> int:
    """A 商品云库 product's 货源 (and 货源价) goes to the same product in every store it is listed in:
    store products with their own 货源 get the same one (those without one show the 云库 货源 already)."""
    lib = db.get(LibraryProduct, lib_link.library_product_id) if lib_link.library_product_id else None
    if lib is None:
        return 0
    n = 0
    for sp in store_products_of(db, lib):
        if sp.id == skip_store_product:
            continue
        own = db.scalar(select(ProductSourceLink).where(ProductSourceLink.store_product_id == sp.id))
        if own is None:
            continue
        for f in LINK_FIELDS:
            setattr(own, f, getattr(lib_link, f))
        own.sku_prices = store_sku_prices(db, lib_link, sp) or None  # 云库 spec prices -> this store's specs
        own.linked_at = datetime.now()
        n += 1
    return n


def _norm(v: str) -> str:
    return re.sub(r"[\s\W_]+", "", v or "").lower()


def _store_values(sku: dict) -> list[str]:
    """'颜色:红色；尺码:XL' -> ['红色', 'XL']"""
    return [part.split(":", 1)[-1] for part in (sku.get("attrs") or "").split("；") if part]


def match_skus(store_skus: list[dict], lib_skus: list[dict]) -> dict[str, str]:
    """{store sku_id: library source_sku_id}. Store spec values may have had words removed by the
    template (SKU 违禁词), so a store value matches a library value it is contained in. SKUs named
    款式N by the publisher map to the Nth library SKU; with nothing else to go on, the same position."""
    out, used = {}, set()
    for i, s in enumerate(store_skus):
        vals = [_norm(v) for v in _store_values(s)]
        m = re.fullmatch(r"款式(\d+)", (_store_values(s) or [""])[0])
        cand = None
        if m and 0 < int(m.group(1)) <= len(lib_skus):
            cand = lib_skus[int(m.group(1)) - 1]
        elif vals:
            hits = [ls for ls in lib_skus if ls["source_sku_id"] not in used and all(
                any(v and (v == _norm(a["value"]) or v in _norm(a["value"])) for a in ls["attrs"]) for v in vals)]
            exact = [ls for ls in hits if sorted(vals) == sorted(_norm(a["value"]) for a in ls["attrs"])]
            cand = (exact or hits or [None])[0]
        if cand is None and len(store_skus) == len(lib_skus):
            cand = lib_skus[i]
        if cand is not None and cand["source_sku_id"] not in used:
            out[s["sku_id"]] = cand["source_sku_id"]
            used.add(cand["source_sku_id"])
    return out


def sync_prices(db, sp: StoreProduct) -> int:
    """Keep a store product's current SKU prices on its 商品云库 product(s). Returns how many."""
    n = 0
    for lib in _libs(db, sp):
        lib_skus = (lib.normalized or {}).get("skus") or []
        if not lib_skus:
            continue
        mapping = match_skus(sp.skus or [], lib_skus)
        prices = {mapping[s["sku_id"]]: int(s["price"]) for s in sp.skus or []
                  if s.get("sku_id") in mapping and s.get("price")}
        if not prices:
            continue
        lib.sale_prices = {**(lib.sale_prices or {}), **prices}
        flag_modified(lib, "sale_prices")
        lib.sale_prices_at = datetime.now()
        n += 1
    return n


def backfill_sources(db) -> int:
    """货源 set on store products before they were written back: give each 商品云库 product that has
    none the 货源 of a store product published from it (the most recent one). Through 商品云库 the
    product's copies in the other stores show it too. Existing 云库 货源 are kept. Returns how many."""
    n = 0
    links = db.scalars(select(ProductSourceLink).where(ProductSourceLink.store_product_id.is_not(None))
                       .order_by(ProductSourceLink.linked_at.desc())).all()
    for link in links:
        sp = db.get(StoreProduct, link.store_product_id)
        if not sp:
            continue
        for lib in _libs(db, sp):
            if db.scalar(select(ProductSourceLink.id).where(ProductSourceLink.tenant_id == lib.tenant_id,
                                                            ProductSourceLink.library_product_id == lib.id)):
                continue
            m = sku_map(sp, lib)
            db.add(ProductSourceLink(tenant_id=lib.tenant_id, library_product_id=lib.id,
                                     sku_prices={m[k]: v for k, v in (link.sku_prices or {}).items() if k in m} or None,
                                     **{f: getattr(link, f) for f in LINK_FIELDS}))
            if lib.no_source:
                lib.no_source, lib.no_source_note = False, ""
            db.flush()
            n += 1
    db.commit()
    return n


# ---- 货源价 per spec --------------------------------------------------------------
def sku_map(sp: StoreProduct, lib: LibraryProduct) -> dict[str, str]:
    """{store sku_id: 商品云库 source sku id} for a store product published from lib."""
    return match_skus(sp.skus or [], (lib.normalized or {}).get("skus") or [])


def _lib_of(db, link: ProductSourceLink):
    return db.get(LibraryProduct, link.library_product_id) if link.library_product_id else None


def store_sku_prices(db, link: ProductSourceLink, sp: StoreProduct | None) -> dict[str, int]:
    """The link's spec prices keyed by the store product's sku ids (translated for a 云库 货源)."""
    prices = link.sku_prices or {}
    if not prices or link.store_product_id or sp is None:
        return dict(prices)
    lib = _lib_of(db, link)
    if lib is None:
        return {}
    return {sk: prices[src] for sk, src in sku_map(sp, lib).items() if src in prices}


def set_store_sku_prices(db, link: ProductSourceLink, sp: StoreProduct | None, prices: dict[str, int]) -> None:
    """Write spec prices given by store sku id into the link (translated for a 云库 货源)."""
    if link.store_product_id or sp is None:
        new = {**(link.sku_prices or {}), **prices}
    else:
        lib = _lib_of(db, link)
        m = sku_map(sp, lib) if lib else {}
        new = {**(link.sku_prices or {}), **{m[k]: v for k, v in prices.items() if k in m}}
    link.sku_prices = new or None
    flag_modified(link, "sku_prices")


def price_range(link: ProductSourceLink) -> tuple[int, int]:
    vals = list((link.sku_prices or {}).values()) or [link.price]
    return min(vals), max(vals)
