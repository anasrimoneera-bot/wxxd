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

LINK_FIELDS = ("offer_id", "title", "image_url", "detail_url", "supplier", "price", "original_price", "moq", "unit")


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
        own = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == link.tenant_id,
                                                        ProductSourceLink.library_product_id == lib.id))
        if own is None:
            own = ProductSourceLink(tenant_id=link.tenant_id, library_product_id=lib.id)
            db.add(own)
        for f in LINK_FIELDS:
            setattr(own, f, getattr(link, f))
        own.linked_at = datetime.now()
        if lib.no_source:
            lib.no_source, lib.no_source_note = False, ""
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
