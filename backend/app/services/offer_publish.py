"""1688 上架: list a 商品云库 product with the 1688 offer found for it instead of its data package.

From the 商品云库 product: title (ai_title / title), WeChat category, category attributes, video.
From the 1688 offer (read by the 货源助手 on the detail page): main + detail images (the 云库 ones when
none were read), the specs picked in the plugin (names, pictures, stock) and their prices.
Sale price of each spec = the 自动改价 formula on its 货源价 (1688 price + 运费): to the buyer after the
store's best coupon = 货源价 × 倍率 + 加价. After listing, the store product and its 货源 (per-spec
prices) are saved, so 采购 / 改价 work at once."""
import logging

from ..models import LibraryProduct, ProductSourceLink, Store

log = logging.getLogger(__name__)
MAX_STOCK = 99999


def cost_of(sku: dict, freight_fen: int) -> int:
    """货源价 of a picked 1688 spec in fen (运费 included)."""
    return round(float(sku.get("price") or 0) * 100) + freight_fen


def build_norm(product: LibraryProduct, offer: dict, coupon_list, margin: float, add_fen: int) -> dict:
    from ..routers.products import formula_price
    lib = product.normalized or {}
    freight = round(float(offer.get("freight") or 0) * 100)
    skus, sale = [], {}
    for k in offer.get("skus") or []:
        cost = cost_of(k, freight)
        key = str(k["key"])
        stock = k.get("stock")
        skus.append({"source_sku_id": key, "price": cost, "sku_code": "", "thumb": k.get("image") or "",
                     "stock": min(int(stock), MAX_STOCK) if stock not in (None, "") else MAX_STOCK,
                     "attrs": [{"name": a["name"], "value": a["value"]} for a in k.get("attrs") or [] if a.get("value")]})
        sale[key] = formula_price(cost, margin, add_fen, coupon_list, "")[0]
    video = lib.get("video")
    if offer.get("video_url"):  # the 1688 main video (1688 serves it to its own pages: send the page as Referer)
        video = {"url": offer["video_url"], "referer": offer.get("detail_url") or "https://detail.1688.com/"}
    return {**lib, "ai_title": product.ai_title or "", "video": video,
            "head_imgs": list(offer.get("images") or []) or lib.get("head_imgs") or [],
            "detail_imgs": list(offer.get("detail_images") or []) or lib.get("detail_imgs") or [],
            "skus": skus, "sale_prices": sale}


def after_publish(db, store: Store, client, wx_product_id: str, product: LibraryProduct) -> str:
    """The new store product and its 货源 (each spec's 货源价). Returns a note for the task item."""
    from datetime import datetime

    from . import library_sync, product_sync
    offer = product.offer_data or {}
    sp = product_sync.upsert(db, store, product_sync.parse_product(client.get_product(wx_product_id)))
    db.flush()
    freight = round(float(offer.get("freight") or 0) * 100)
    picked = offer.get("skus") or []
    mapping = library_sync.match_skus(sp.skus or [], [{"source_sku_id": str(k["key"]), "attrs": k.get("attrs") or []}
                                                      for k in picked])
    cost = {str(k["key"]): cost_of(k, freight) for k in picked}
    sku_prices = {sid: cost[key] for sid, key in mapping.items() if key in cost}
    lk = ProductSourceLink(tenant_id=product.tenant_id, store_product_id=sp.id)
    db.add(lk)
    lk.offer_id, lk.title = str(offer.get("offer_id") or ""), (offer.get("title") or "")[:255]
    lk.image_url, lk.supplier = offer.get("image_url") or "", (offer.get("supplier") or "")[:255]
    lk.detail_url = offer.get("detail_url") or f"https://detail.1688.com/offer/{lk.offer_id}.html"
    lk.price = min(cost.values()) if cost else 0
    lk.sku_prices = sku_prices or None
    lk.freight = freight
    lk.linked_at = datetime.now()
    sp.no_source = False
    return "" if len(sku_prices) == len(sp.skus or []) else f"；{len(sp.skus or []) - len(sku_prices)} 个规格没对应上货源价"
