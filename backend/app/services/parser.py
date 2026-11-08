"""Parse collected WeChat Store product data packages into a normalised dict.

Primary format (A): {"data": {"data": "<JSON string>"}, "errno": ..., ...}
Also accepted:      {"data": "<JSON string>"}, a bare product object, or a bare
                    `info` object (snake_case or camelCase).
"""
import json
import re


class ParseError(Exception):
    pass


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake_keys(obj):
    if isinstance(obj, dict):
        return {_CAMEL.sub("_", k).lower(): _snake_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_snake_keys(v) for v in obj]
    return obj


def _extract_product(doc) -> dict:
    if not isinstance(doc, dict):
        raise ParseError("文件内容不是 JSON 对象")
    if "data" in doc:
        inner = doc["data"]
        if isinstance(inner, dict) and isinstance(inner.get("data"), str):
            inner = inner["data"]
        if isinstance(inner, str):
            try:
                payload = json.loads(inner)
            except json.JSONDecodeError as e:
                raise ParseError(f"data 字段不是合法 JSON：{e}") from None
            if payload.get("errcode", 0) not in (0, "0"):
                raise ParseError(f"数据包本身是错误响应：errcode={payload.get('errcode')}")
            prod = payload.get("product")
            if not isinstance(prod, dict):
                raise ParseError("数据包中没有 product 字段")
            return prod
        raise ParseError("不支持的数据包格式（页面状态数据），请重新采集为 data.data 格式")
    if "product_key" in doc and "info" in doc:
        return doc
    if "title" in doc and ("head_img" in doc or "headImg" in doc):
        info = _snake_keys(doc) if "headImg" in doc else doc
        return {"product_key": {"product_id": str(info.get("product_id", ""))}, "info": info, "product_skus": []}
    raise ParseError("无法识别的数据包格式")


def _param_pairs(params) -> list[dict]:
    out = []
    for p in params or []:
        v2 = p.get("param_v2") or {}
        name = v2.get("attr_name")
        value = v2.get("attr_value_name")
        if not name and p.get("categorys"):
            cs = p["categorys"]
            name = cs[0].get("name") if cs else None
            value = cs[1].get("name") if len(cs) > 1 else None
        if name and value not in (None, ""):
            out.append({"name": str(name), "value": str(value)})
    return out


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_package(content: str | bytes, filename: str = "") -> dict:
    """Return the normalised product. Raises ParseError with a Chinese message."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    try:
        doc = json.loads(content)
    except json.JSONDecodeError as e:
        raise ParseError(f"不是合法 JSON：{e}") from None

    prod = _extract_product(doc)
    info = prod.get("info") or {}
    pid = str((prod.get("product_key") or {}).get("product_id") or "").strip()
    if not pid:
        stem = re.sub(r"\.(json|txt)$", "", filename.rsplit("/", 1)[-1])
        pid = stem if stem.isdigit() else ""
    if not pid:
        raise ParseError("缺少来源商品 ID")
    title = (info.get("title") or "").strip()
    if not title:
        raise ParseError("缺少商品标题")

    head_imgs = [u for u in (info.get("head_img") or []) if isinstance(u, str) and u]
    detail = info.get("detail") or {}
    detail_imgs = [u for u in (detail.get("detail_img") or []) if isinstance(u, str) and u]

    # Price lookup from sku_list (contains price even when product_skus lacks it)
    price_by_sku = {}
    for s in ((info.get("sku_list") or {}).get("sku_info") or []):
        price_by_sku[str(s.get("sku_id"))] = _int(s.get("origin_price") or s.get("price"))

    skus = []
    for s in prod.get("product_skus") or []:
        si = s.get("product_sku_info") or {}
        sku_id = str((s.get("sku_key") or {}).get("sku_id") or "")
        stock = si.get("stock_info") or {}
        skus.append({
            "source_sku_id": sku_id,
            "thumb": si.get("thumb_img") or "",
            "price": _int(si.get("sale_price")) or price_by_sku.get(sku_id, 0),
            "stock": _int(stock.get("stock_num")),
            "sku_code": si.get("sku_code") or "",
            "attrs": _param_pairs(si.get("sale_param")),
        })
    if not skus and price_by_sku:  # bare info objects: fall back to sku_list
        for sid, price in price_by_sku.items():
            skus.append({"source_sku_id": sid, "thumb": "", "price": price, "stock": 0, "sku_code": "", "attrs": []})

    video = None
    vids = info.get("head_video_info") or []
    if vids and isinstance(vids, list) and vids[0].get("video_url"):
        v = vids[0]
        video = {
            "url": v.get("video_url"),
            "h265_url": v.get("video_h265_url") or "",
            "cover": v.get("video_head_img") or "",
            "seconds": _int(v.get("video_len")),
            "width": _int(v.get("video_width")),
            "height": _int(v.get("video_height")),
        }

    cats = [str(c.get("cat_id")) for c in (info.get("category") or []) if c.get("cat_id")]
    extra = info.get("extra_service") or {}

    return {
        "platform": "wechat",
        "source_product_id": pid,
        "title": title,
        "short_title": info.get("short_title") or "",
        "head_imgs": head_imgs,
        "detail_imgs": detail_imgs,
        "desc": detail.get("desc") or "",
        "cats": cats,
        "attrs": _param_pairs(info.get("param")),
        "skus": skus,
        "video": video,
        "sales": _int(info.get("total_sold_num")),
        "stock": _int(info.get("total_stock_num")) or sum(s["stock"] for s in skus),
        "source_extra_service": {k: extra.get(k) for k in ("seven_day_return", "freight_insurance") if k in extra},
    }


def summary(norm: dict) -> dict:
    prices = [s["price"] for s in norm["skus"] if s["price"]] or [0]
    return {
        "title": norm["title"][:255],
        "cover": norm["head_imgs"][0] if norm["head_imgs"] else "",
        "has_video": bool(norm.get("video")),
        "price_min": min(prices),
        "price_max": max(prices),
        "stock": norm["stock"],
        "sales": norm["sales"],
        "sku_count": len(norm["skus"]),
    }
