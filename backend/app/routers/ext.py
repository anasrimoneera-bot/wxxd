"""1688 货源助手 (browser extension): pick a product, read 1688 pages in the user's own browser,
choose the 货源 by hand among the search results, and save it (per-spec 货源价 + 运费) with one click.

The extension logs in with /api/auth/login and calls these with the Bearer token. A product is
("store", StoreProduct id) or ("library", LibraryProduct id)."""
import hashlib
import hmac
import io
from datetime import datetime, timedelta
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, aliased

from .. import access
from ..db import get_db
from ..deps import current_user
from ..models import (ExtClaim, LibraryProduct, ProductSourceLink, PublishedRecord, Store, StoreOrder, StoreProduct,
                      User)
from ..services import library_sync

router = APIRouter(prefix="/api/ext", tags=["1688 货源助手"])
ON_SALE = 5  # 微信小店 商品状态: 上架
CLAIM_HOURS = 2  # a product claimed by someone stays out of the others' list this long after their last activity


# ---- claims: no two accounts find a 货源 for the same product --------------------------------
def _groups(db, kind: str, rows) -> dict[int, str]:
    """{product id: group key}. Store products published from the same source product (any store,
    any account) are one job: a 货源 set on one reaches the others through 商品云库."""
    if kind == "library":
        return {p.id: f"src:{p.platform}:{p.source_product_id}" for p in rows}
    pairs = [(p.store_id, p.wx_product_id) for p in rows if p.wx_product_id]
    src = {}
    if pairs:
        from sqlalchemy import tuple_
        for store_id, wx, key in db.execute(select(PublishedRecord.store_id, PublishedRecord.wx_product_id,
                                                   PublishedRecord.source_key).where(
                tuple_(PublishedRecord.store_id, PublishedRecord.wx_product_id).in_(pairs))):
            src[(store_id, wx)] = key
    return {p.id: (f"src:{src[(p.store_id, p.wx_product_id)]}" if (p.store_id, p.wx_product_id) in src
                   else f"store:{p.id}") for p in rows}


def _active_claims(db, keys) -> dict[str, ExtClaim]:
    if not keys:
        return {}
    since = datetime.now() - timedelta(hours=CLAIM_HOURS)
    return {c.group_key: c for c in db.scalars(select(ExtClaim).where(ExtClaim.group_key.in_(list(keys)),
                                                                       ExtClaim.claimed_at > since))}


def _release(db, kind: str, p) -> None:
    key = _groups(db, kind, [p])[p.id]
    c = db.scalar(select(ExtClaim).where(ExtClaim.group_key == key))
    if c:
        db.delete(c)


def _sig(kind: str, pid: int) -> str:
    from ..config import get_settings
    return hmac.new(get_settings().jwt_secret.encode(), f"img:{kind}:{pid}".encode(), hashlib.sha256).hexdigest()[:20]


def image_path(kind: str, pid: int) -> str:
    """A public link to the product's main image, served by this site: 1688 image search cannot
    download pictures from the WeChat image server, so it gets them from here (signed, no login)."""
    return f"/api/ext/img/{kind}/{pid}/{_sig(kind, pid)}.jpg"


def _spec_label(attrs) -> str:
    if isinstance(attrs, str):  # store product: '颜色:红色；尺码:XL'
        return " / ".join(p.split(":", 1)[-1] for p in attrs.split("；") if p)
    return " / ".join(str(a.get("value")) for a in attrs or [])  # 商品云库: [{name, value}]


def _store_item(db, p: StoreProduct, names: dict) -> dict:
    return {"kind": "store", "id": p.id, "title": p.title, "image": p.cover, "store": names.get(p.store_id, ""),
            "wx_product_id": p.wx_product_id, "on_sale": p.status == ON_SALE, "exposure": p.exposure_30d,
            "hint": p.source_hint,
            "image_proxy": image_path("store", p.id),
            "price_min": p.price_min, "price_max": p.price_max,
            "specs": [{"key": k["sku_id"], "label": _spec_label(k.get("attrs")) or "默认", "price": k.get("price"),
                       "image": k.get("thumb") or "", "stock": k.get("stock")}
                      for k in p.skus or [] if not k.get("removed")]}


def _library_item(p: LibraryProduct) -> dict:
    return {"kind": "library", "id": p.id, "title": p.ai_title or p.title, "image": p.cover, "store": "商品云库",
            "hint": p.source_hint,
            "image_proxy": image_path("library", p.id),
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


def _unclaimed(db, user, kind, rows, missing: bool):
    """Without products someone else is working on, and one product per sourcing job (when listing
    the products still without a 货源)."""
    if not missing:
        return rows
    groups = _groups(db, kind, rows)
    claims = _active_claims(db, set(groups.values()))
    out, seen = [], set()
    for p in rows:
        g = groups[p.id]
        c = claims.get(g)
        if g in seen or (c and c.user_id != user.id):
            continue
        seen.add(g)
        out.append(p)
    return out


def _group_exposure_store():
    """优先找货源: the most 曝光 of the same item in any store (any account), read on the 小店后台 商品列表.
    The item = the 商品云库 source product it was published from (one 货源 serves all its stores)."""
    pr1, pr2, sp2 = aliased(PublishedRecord), aliased(PublishedRecord), aliased(StoreProduct)
    shared = (select(func.max(sp2.exposure_30d)).select_from(pr1)
              .join(pr2, pr2.source_key == pr1.source_key)
              .join(sp2, (sp2.store_id == pr2.store_id) & (sp2.wx_product_id == pr2.wx_product_id))
              .where(pr1.store_id == StoreProduct.store_id, pr1.wx_product_id == StoreProduct.wx_product_id)
              .correlate(StoreProduct).scalar_subquery())
    return func.coalesce(shared, StoreProduct.exposure_30d, 0)


def _group_exposure_library():
    pr, sp = aliased(PublishedRecord), aliased(StoreProduct)
    key = LibraryProduct.platform + ":" + LibraryProduct.source_product_id
    return func.coalesce(select(func.max(sp.exposure_30d)).select_from(pr)
                         .join(sp, (sp.store_id == pr.store_id) & (sp.wx_product_id == pr.wx_product_id))
                         .where(pr.source_key == key).correlate(LibraryProduct).scalar_subquery(), 0)


def _take(db, user, kind: str, sel, limit: int, missing: bool):
    """`limit` rows of the ordered select, without the ones another account is on (read page by page,
    so claimed products do not leave the list short)."""
    out, off, size = [], 0, max(limit * 3, 60)
    for _ in range(10):
        page = db.execute(sel.offset(off).limit(size)).all()
        if not page:
            break
        off += len(page)
        by_id = {r[0].id: r[1] for r in page}
        for p in _unclaimed(db, user, kind, [r[0] for r in page], missing):
            if p.id not in {x[0].id for x in out}:
                out.append((p, by_id.get(p.id) or 0))
        if len(out) >= limit or len(page) < size:
            break
    return out[:limit]


@router.get("/queue")
def queue(kind: str = "store", q: str = "", store_id: int | None = None, missing: bool = True, limit: int = 50,
          stage: int = 1, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Products to find a 货源 for: by default those without one (and not ones another account is on);
    q searches title / id. 优先: items with 曝光 in any store (by any account) come first — in a store:
    上架 ones with 曝光, then the other 上架 ones, then 未上架 / 草稿箱.
    stage 1 (粗找): not yet looked at — 有货源 or 跳过; stage 2 (精选): the ones found 有货源 in stage 1."""
    limit = max(1, min(limit, 200))
    if kind == "library":
        prio = _group_exposure_library()
        sel = select(LibraryProduct, prio).where(LibraryProduct.tenant_id == user.tenant_id,
                                                 LibraryProduct.no_source == False)  # noqa: E712
        if missing:
            sel = sel.where(~LibraryProduct.id.in_(select(ProductSourceLink.library_product_id).where(
                ProductSourceLink.tenant_id == user.tenant_id, ProductSourceLink.library_product_id.is_not(None))))
            sel = sel.where(LibraryProduct.source_hint.is_not(None) if stage == 2 else LibraryProduct.source_hint.is_(None))
        if q:
            sel = sel.where(or_(LibraryProduct.title.contains(q), LibraryProduct.source_product_id == q.strip()))
        rows = _take(db, user, "library", sel.order_by(case((prio > 0, 0), else_=1), prio.desc(), LibraryProduct.id.desc()),
                     limit, missing)
        return {"items": [{**_library_item(p), "exposure": e or None, "priority": bool(e)} for p, e in rows]}
    from .products import _linked_ids
    prio = _group_exposure_store()
    sel = select(StoreProduct, prio).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)))
    if store_id:
        sel = sel.where(StoreProduct.store_id == store_id)
    if missing:  # no 货源 yet, and not skipped as 无货源
        sel = sel.where(~StoreProduct.id.in_(_linked_ids(user.tenant_id)), StoreProduct.no_source == False)  # noqa: E712
        sel = sel.where(StoreProduct.source_hint.is_not(None) if stage == 2 else StoreProduct.source_hint.is_(None))
    if q:
        sel = sel.where(or_(StoreProduct.title.contains(q), StoreProduct.wx_product_id == q.strip()))
    on_sale = StoreProduct.status == ON_SALE
    order = (case((on_sale & (prio > 0), 0), (on_sale, 1), else_=2), prio.desc(), StoreProduct.id.desc())
    rows = _take(db, user, "store", sel.order_by(*order), limit, missing)
    names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
    return {"items": [{**_store_item(db, p, names), "exposure": e or p.exposure_30d, "priority": bool(e)} for p, e in rows],
            "stores": [{"id": sid, "name": n} for sid, n in names.items()]}


def _with_spec_images(db, p: StoreProduct) -> None:
    """Products synced before the spec pictures were kept: read the product once to get them."""
    if not p.skus or all("thumb" in k for k in p.skus):
        return
    from ..security import decrypt_secret
    from ..services import product_sync
    from . import stores as stores_router
    s = db.get(Store, p.store_id)
    try:
        resp = stores_router.client_factory(s.appid, decrypt_secret(s.secret_enc)).get_product(p.wx_product_id)
        product_sync.upsert(db, s, product_sync.parse_product(resp))
        db.commit()
    except Exception:  # no pictures this time: the specs still show
        db.rollback()


def library_exposure(db, libs) -> dict[int, int]:
    """{商品云库 product id: the most 曝光 of it in any store} (read on the 小店后台 商品列表)."""
    keys = {f"{lp.platform}:{lp.source_product_id}": lp.id for lp in libs}
    out = {}
    items = list(keys)
    for i in range(0, len(items), 500):
        for key, e in db.execute(select(PublishedRecord.source_key, func.max(StoreProduct.exposure_30d))
                                 .join(StoreProduct, (StoreProduct.store_id == PublishedRecord.store_id)
                                       & (StoreProduct.wx_product_id == PublishedRecord.wx_product_id))
                                 .where(PublishedRecord.source_key.in_(items[i:i + 500]))
                                 .group_by(PublishedRecord.source_key)):
            if e:
                out[keys[key]] = e
    return out


@router.get("/item")
def item(kind: str, id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _get(db, user, kind, id)
    if kind == "store":
        _with_spec_images(db, p)
        names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
        out = _store_item(db, p, names)
        from ..services.product_sync import library_products_for
        libs = [x for x in library_products_for(db, user.tenant_id, [p]) if x]
        e = max([p.exposure_30d or 0, *library_exposure(db, libs).values()])
    else:
        out = _library_item(p)
        e = library_exposure(db, [p]).get(p.id, 0)
    return {**out, "source": _current_source(db, user, kind, p), "exposure": e or None, "priority": bool(e)}


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
    if lk.offer_id != o.offer_id:  # another link: 已确认最优 no longer holds
        lk.confirmed_at, lk.confirmed_by = None, ""
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
    if body.kind == "store":
        p.no_source = False
    db.flush()
    library_sync.sync_source(db, lk)  # store 货源 -> 商品云库 -> the product in the other stores
    library_sync.propagate_source(db, lk)  # 云库 货源 -> every store
    _release(db, body.kind, p)
    db.commit()
    return {"ok": True, "source": link_out(lk), "auto_price": _auto_price_after_link(db, user, body.kind, p)}


def _auto_price_after_link(db, user, kind: str, p) -> list[dict]:
    """自动改价 of the store products that got this 货源 (the product, and the same 商品云库 product in the
    other stores)."""
    from ..services.product_sync import library_products_for
    from .products import apply_auto_price
    allowed = set(access.stores(db, user.tenant_id))
    targets = {p.id: p} if kind == "store" else {}
    libs = library_products_for(db, user.tenant_id, [p]) if kind == "store" else [p]
    for lib in libs:
        if lib is None:
            continue
        for sp in library_sync.store_products_of(db, lib):
            targets.setdefault(sp.id, sp)
    try:
        return apply_auto_price(db, user, [sp for sp in targets.values() if sp.store_id in allowed])
    except Exception as e:  # the 货源 is saved either way
        db.rollback()
        return [{"ok": False, "title": "", "error": str(e)}]


IMG_CACHE = Path(tempfile.gettempdir()) / "wxxd-ext-img"  # shared by the server's worker processes


def _jpeg(url: str) -> bytes:
    """The image as a JPEG of at most 800 px, cached on disk: 1688 asks for all the links of a
    批量图搜 at once and gives up on slow ones, so the extension fetches them first (warm-up)."""
    from PIL import Image

    from ..services.wechat import download
    f = IMG_CACHE / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".jpg")
    if f.exists():
        return f.read_bytes()
    img = Image.open(io.BytesIO(download(url if not url.startswith("//") else "https:" + url, timeout=20)))
    img = img.convert("RGB")
    img.thumbnail((800, 800))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    data = out.getvalue()
    try:
        IMG_CACHE.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp%d" % os.getpid())
        tmp.write_bytes(data)
        tmp.replace(f)
    except OSError:
        pass
    return data


@router.api_route("/img/{kind}/{pid}/{sig}.jpg", methods=["GET", "HEAD"])
def image(kind: str, pid: int, sig: str, db: Session = Depends(get_db)):
    """The product's main image as JPEG, for 1688 image search (see image_path)."""
    if kind not in ("store", "library") or not hmac.compare_digest(sig, _sig(kind, pid)):
        raise HTTPException(404, "图片不存在")
    p = db.get(StoreProduct if kind == "store" else LibraryProduct, pid)
    if not p or not p.cover:
        raise HTTPException(404, "图片不存在")
    try:
        data = _jpeg(p.cover)
    except Exception:
        raise HTTPException(502, "读取商品主图失败")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


class SkipIn(BaseModel):
    zero_stock: bool = True  # 库存清零 in every store it is listed in
    kind: str
    id: int


def _same_item(db, user, kind: str, p) -> tuple[list, list]:
    """(store products, 商品云库 products) of the same item: the product, the 商品云库 product it was
    published from, and that product in every store this account can use."""
    from ..services.product_sync import library_products_for
    allowed = set(access.stores(db, user.tenant_id))
    libs = [x for x in library_products_for(db, user.tenant_id, [p]) if x] if kind == "store" else [p]
    sps = {p.id: p} if kind == "store" else {}
    for lib in libs:
        for sp in library_sync.store_products_of(db, lib):
            sps.setdefault(sp.id, sp)
    return [sp for sp in sps.values() if sp.store_id in allowed], libs


def zero_stock(db, products: list) -> list[dict]:
    """库存清零 of these store products in the 微信小店 (each spec with stock), mirrored here."""
    from sqlalchemy.orm.attributes import flag_modified

    from ..security import decrypt_secret
    from ..services.wechat import explain
    from . import stores as stores_router
    out, clients = [], {}
    for sp in products:
        s = db.get(Store, sp.store_id)
        if s.id not in clients:
            clients[s.id] = stores_router.client_factory(s.appid, decrypt_secret(s.secret_enc))
        skus = [dict(k) for k in sp.skus or []]
        try:
            for k in skus:
                if (k.get("stock") or 0) > 0 and not k.get("removed"):
                    clients[s.id].update_stock(sp.wx_product_id, k["sku_id"], 3, 0)
                    k["stock"] = 0
            out.append({"store": s.name, "title": sp.title, "ok": True})
        except Exception as e:
            out.append({"store": s.name, "title": sp.title, "ok": False, "error": explain(e)})
        sp.skus = skus
        flag_modified(sp, "skus")
        sp.stock = sum(k.get("stock") or 0 for k in skus)
    return out


@router.post("/skip")
def skip(body: SkipIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """跳过 in the 1688 货源助手: no suitable source on 1688. The product is marked 无货源 (with its
    商品云库 product and the same product in the other stores), no longer offered for finding a 货源
    (setting a 货源 clears it), and its stock becomes 0 in every store it is listed in (zero_stock)."""
    p = _get(db, user, body.kind, body.id)
    note = "1688 货源助手：没有合适的货源，已跳过"
    sps, libs = _same_item(db, user, body.kind, p)
    for sp in sps:
        sp.no_source, sp.source_hint = True, None
    for lib in libs:
        linked = db.scalar(select(ProductSourceLink.id).where(ProductSourceLink.tenant_id == lib.tenant_id,
                                                              ProductSourceLink.library_product_id == lib.id))
        if not linked:
            lib.no_source, lib.no_source_note, lib.source_hint = True, note, None
    stock = zero_stock(db, sps) if body.zero_stock else []
    _release(db, body.kind, p)
    db.commit()
    return {"ok": True, "zeroed": sum(r["ok"] for r in stock), "failed": [r for r in stock if not r["ok"]]}


# ---- 第一阶段 (粗找): 有货源 — a matching offer exists; 第二阶段 picks the best one and its price ----------
class FoundIn(BaseModel):
    kind: str
    id: int
    offer_id: str = Field(default="", max_length=64)
    price: float | None = None
    title: str = Field(default="", max_length=255)
    image_url: str = Field(default="", max_length=1024)


@router.post("/found")
def found(body: FoundIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """有货源 (第一阶段): kept on the product, its 商品云库 product and the same product in the other stores;
    第二阶段 (stage=2 in the queue) lists these to choose the best 货源."""
    p = _get(db, user, body.kind, body.id)
    hint = {"offer_id": body.offer_id, "price": body.price, "title": body.title, "image_url": body.image_url,
            "at": datetime.now().isoformat(timespec="seconds"), "by": user.username}
    sps, libs = _same_item(db, user, body.kind, p)
    for x in [*sps, *libs, p]:
        x.source_hint = hint
        x.no_source = False
    _release(db, body.kind, p)
    db.commit()
    return {"ok": True}


class ClaimItem(BaseModel):
    kind: str
    id: int


class ClaimIn(BaseModel):
    items: list[ClaimItem] = Field(max_length=50)


@router.post("/claim")
def claim(body: ClaimIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Start (or keep) working on these products. A product someone else is on is not given:
    {claimed: ["store:1", ...], taken: [{key, by}]}. Claiming again renews the claim."""
    claimed, taken = [], []
    for it in body.items:
        try:
            p = _get(db, user, it.kind, it.id)
        except HTTPException:
            continue
        by = _claim_one(db, user, it.kind, p)
        if by:
            taken.append({"key": f"{it.kind}:{it.id}", "by": by})
        else:
            claimed.append(f"{it.kind}:{it.id}")
    db.commit()
    return {"claimed": claimed, "taken": taken}


def _claim_one(db, user, kind: str, p) -> str | None:
    """Claim (or renew) one product for this account; the other account's name when it is on it."""
    g = _groups(db, kind, [p])[p.id]
    c = db.scalar(select(ExtClaim).where(ExtClaim.group_key == g))
    fresh = c and c.claimed_at > datetime.now() - timedelta(hours=CLAIM_HOURS)
    if fresh and c.user_id != user.id:
        return c.username
    if c is None:
        c = ExtClaim(group_key=g)
        db.add(c)
    c.user_id, c.tenant_id, c.username, c.claimed_at = user.id, user.tenant_id, user.username, datetime.now()
    db.flush()
    return None


@router.post("/release")
def release(body: ClaimIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Stop working on these products (batch cleared): others get them again."""
    for it in body.items:
        try:
            p = _get(db, user, it.kind, it.id)
        except HTTPException:
            continue
        g = _groups(db, it.kind, [p])[p.id]
        c = db.scalar(select(ExtClaim).where(ExtClaim.group_key == g, ExtClaim.user_id == user.id))
        if c:
            db.delete(c)
    db.commit()
    return {"ok": True}


# ---- 换商品: the product with this 微信小店商品 ID -----------------------------------------------
@router.get("/by-wx")
def by_wx(product_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The 货源助手 works on this product instead (the ID shown in 订单管理 / 商品管理)."""
    pid = product_id.strip()
    if not pid:
        raise HTTPException(400, "请填写微信小店商品 ID")
    p = db.scalar(select(StoreProduct).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)),
                                             StoreProduct.wx_product_id == pid).order_by(StoreProduct.id.desc()))
    if not p:
        raise HTTPException(404, f"没有找到商品 ID {pid}（请先在商品管理里同步这个店铺的商品）")
    by = _claim_one(db, user, "store", p)  # 找货源 isolation: not a product another account is on
    if by:
        db.rollback()
        raise HTTPException(409, f"这个商品正在被账号「{by}」找货源（{CLAIM_HOURS} 小时内有效），请换别的商品")
    db.commit()
    names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
    return {**_store_item(db, p, names), "wx_product_id": p.wx_product_id, "source": _current_source(db, user, "store", p)}


# ---- 采购跟踪: the buyer info pasted into 1688「使用临时地址」 → which order is being bought ----------
_DIGITS = re.compile(r"\d{7,}")


def _norm(s: str) -> str:
    return re.sub(r"[\s,，;；:：#\-－·]", "", s or "")


def _order_brief(db, o) -> dict:
    s = db.get(Store, o.store_id)
    return {"id": o.id, "order_id": o.order_id, "store": s.name if s else "", "status": o.status,
            "receiver_name": o.receiver_name, "items": [{"title": it.get("title", ""), "attrs": it.get("attrs", ""),
                                                         "qty": it.get("qty") or 1} for it in o.items or []],
            "purchase_order_no": o.purchase_order_no or "",
            "purchase_cost": o.purchase_cost, "order_price": o.order_price}


class MatchIn(BaseModel):
    text: str = Field(min_length=4, max_length=1000)


@router.post("/order-match")
def order_match(body: MatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The order whose receiver is in the pasted text (复制收件信息 / 复制面单地址 in 订单管理): by phone,
    else by name + address. Recent orders only, newest first."""
    text = _norm(body.text)
    phones = {d for d in _DIGITS.findall(text)}
    since = datetime.now() - timedelta(days=60)
    rows = db.scalars(select(StoreOrder).where(StoreOrder.store_id.in_(access.store_ids(user.tenant_id)),
                                               or_(StoreOrder.create_time >= since, StoreOrder.create_time.is_(None)))
                      .order_by(StoreOrder.create_time.desc(), StoreOrder.id.desc()).limit(3000)).all()
    best, best_score = None, 0
    for o in rows:
        la = o.label_address or {}
        cand_phones = {_norm(p) for p in (o.receiver_phone, o.virtual_phone, la.get("phone")) if p}
        names = {n for n in (o.receiver_name, la.get("name")) if n}
        addrs = {_norm(a) for a in (o.receiver_address, la.get("address")) if a and len(_norm(a)) >= 6}
        score = 0
        if any(p and not re.search(r"[*＊]", p) and p in phones for p in cand_phones):
            score += 10
        if any(n and "*" not in n and n in text for n in names):
            score += 3
        if any(a in text or (len(a) >= 12 and a[-12:] in text) for a in addrs):
            score += 6
        if score > best_score:
            best, best_score = o, score
    if not best or best_score < 9:  # a phone, or name + address
        return {"order": None}
    return {"order": _order_brief(db, best)}


class BoughtLine(BaseModel):
    """One line of the 1688 order page: the offer, its 单价 and 数量 there (yuan)."""
    offer_id: str = ""
    unit_price: float = Field(gt=0, le=1_000_000)
    qty: int = Field(default=1, ge=1, le=100000)


@router.get("/order")
def order_by_no(order_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """采购跟踪: the order being bought, chosen by its 微信订单号."""
    no = re.sub(r"\D", "", order_id or "")
    o = db.scalar(select(StoreOrder).where(StoreOrder.store_id.in_(access.store_ids(user.tenant_id)),
                                           StoreOrder.order_id == no)) if no else None
    if not o:
        raise HTTPException(404, f"没有找到订单 {no or order_id}（请先在订单管理里同步订单）")
    return {"order": _order_brief(db, o)}


class ExtPurchaseIn(BaseModel):
    id: int
    lines: list[BoughtLine] = Field(default_factory=list, max_length=50)  # → the order lines' 货源单价
    purchase_order_nos: list[str] = Field(default_factory=list, max_length=20)
    purchase_cost: float | None = Field(default=None, ge=0, le=10_000_000)  # yuan, what 立即下单 showed (incl. 运费)


@router.post("/purchase")
def record_purchase(body: ExtPurchaseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The 1688 order placed for our order: its 订单编号 (read on the 收银台) and the price paid. A second
    1688 order for the same order (another supplier) is added to the first one."""
    o = db.get(StoreOrder, body.id)
    if not o or not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)):
        raise HTTPException(404, "订单不存在")
    have = [n for n in re.split(r"[,，\s]+", o.purchase_order_no or "") if n]
    new = [n.strip() for n in body.purchase_order_nos if n.strip() and n.strip() not in have]
    if not new and body.purchase_order_nos:
        return {"ok": True, "duplicate": True, "order": _order_brief(db, o)}
    if body.purchase_cost is not None:
        cost = round(body.purchase_cost * 100)
        o.purchase_cost = cost + (o.purchase_cost or 0) if have and new else cost
    o.purchase_order_no = ",".join(have + new)[:128]
    o.purchased_at = datetime.now()
    changed = _update_source_prices(db, user, o, body.lines, body.purchase_cost if not have else None)
    db.commit()
    return {"ok": True, "order": _order_brief(db, o), "prices_changed": changed}


def _update_source_prices(db, user, o, bought: list[BoughtLine], paid: float | None = None) -> int:
    """The real 采购价 of each spec: the 单价 each 1688 line was bought at becomes the 货源价 of the order
    line's spec, as it is (no calculation). A 1688 line goes to the order line bought from the same offer,
    else to the next order line in order (a different offer was bought: its price still counts)."""
    from .orders import PurchaseLine, _sources, apply_source_prices
    srcs = _sources(db, user.tenant_id, [o])
    ours = [(it, srcs.get((o.store_id, it.get("product_id")))) for it in o.items or []]
    ours = [(it, src) for it, src in ours if src]  # an order line without 货源 has no 货源价 to keep
    if paid and len(ours) == 1 and len(o.items or []) == 1:  # one spec: what was paid is its 采购价 = 货源价
        it, src = ours[0]
        return apply_source_prices(db, user, o, [PurchaseLine(source_id=src["id"], unit_price=paid,
                                                              product_id=it.get("product_id") or "", sku_id=it.get("sku_id") or "")])
    used, lines = set(), []
    pairs = []
    for b in bought:  # same offer first
        i = next((i for i, (_, src) in enumerate(ours) if i not in used and b.offer_id
                  and str(src.get("offer_id") or "") == b.offer_id), None)
        if i is not None:
            used.add(i)
        pairs.append([b, i])
    for p in pairs:  # the rest in order
        if p[1] is None:
            p[1] = next((i for i in range(len(ours)) if i not in used), None)
            if p[1] is not None:
                used.add(p[1])
    for b, i in pairs:
        if i is None:
            continue
        it, src = ours[i]
        lines.append(PurchaseLine(source_id=src["id"], unit_price=b.unit_price, product_id=it.get("product_id") or "",
                                  sku_id=it.get("sku_id") or ""))
    return apply_source_prices(db, user, o, lines) if lines else 0


# ---- 自动发货: the 1688 order's 物流 (read on its 订单详情 page) → 微信小店 发货 ----------------------
class ShipFrom1688In(BaseModel):
    id: int | None = None  # our order (from the 「1688查看」 link); else found by 采购单号
    purchase_order_no: str = Field(default="", max_length=64)
    company: str = Field(min_length=1, max_length=64)  # as 1688 shows it, e.g. 申通快递(STO)
    waybill_id: str = Field(min_length=4, max_length=64)


def match_company(companies: list[dict], text: str) -> str | None:
    """申通快递(STO) / 申通 / STO → the WeChat 快递公司 id."""
    code = (re.search(r"[(（]\s*([A-Za-z0-9]+)\s*[)）]", text) or [None, ""])[1].upper()
    name = re.sub(r"[(（].*?[)）]", "", text).strip()
    by_id = {c["id"].upper(): c["id"] for c in companies}
    if code and code in by_id:
        return by_id[code]
    if text.strip().upper() in by_id:
        return by_id[text.strip().upper()]
    for c in companies:
        if name and c["name"] == name:
            return c["id"]
    short = name.replace("快递", "").replace("速递", "").replace("物流", "")
    for c in companies:
        if short and (short in c["name"] or c["name"].replace("快递", "").replace("速递", "") in name):
            return c["id"]
    return None


@router.post("/ship")
def ship_from_1688(body: ShipFrom1688In, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """发货 our order with the 快递公司 + 运单号 the 1688 supplier shipped with."""
    from .orders import ShipIn, delivery_companies, ship_order
    o = None
    if body.id:
        o = db.get(StoreOrder, body.id)
        if o and not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)):
            o = None
    if o is None and body.purchase_order_no.strip():
        o = db.scalar(select(StoreOrder).where(StoreOrder.store_id.in_(access.store_ids(user.tenant_id)),
                                               StoreOrder.purchase_order_no.contains(body.purchase_order_no.strip()))
                      .order_by(StoreOrder.id.desc()))
    if o is None:
        raise HTTPException(404, "没有找到采购单号对应的订单（先在订单的「采购」里填 1688 采购单号）")
    waybill = body.waybill_id.strip()
    from ..services import order_sync
    if any(d.get("waybill_id") == waybill for d in order_sync.deliveries(o.raw or {})):
        return {"ok": True, "duplicate": True, "order_id": o.order_id, "waybill_id": waybill}
    cid = match_company(delivery_companies(o.store_id, user, db), body.company)
    if not cid:
        raise HTTPException(400, f"微信小店的快递公司里没有找到「{body.company}」，请在订单管理里手动发货")
    ship_order(db, o, ShipIn(delivery_id=cid, waybill_id=waybill))
    return {"ok": True, "order_id": o.order_id, "delivery_id": cid, "waybill_id": waybill, "status": o.status}



# ---- 删除规格: a spec the 货源 does not have -------------------------------------------------------
class SkuRemoveIn(BaseModel):
    kind: str = "store"
    id: int
    sku_id: str = Field(default="", max_length=64)  # one spec, or
    sku_ids: list[str] = Field(default_factory=list, max_length=200)  # several (删除所选规格)


@router.post("/sku-remove")
def sku_remove(body: SkuRemoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The 货源 has no such specs: in the 微信小店 their stock becomes 0 (WeChat has no API to delete one spec
    without sending the whole product to review again), and they are left out of 商品管理 and the
    货源助手 from now on (also after a re-sync). At least one spec stays."""
    from sqlalchemy.orm.attributes import flag_modified

    from ..security import decrypt_secret
    from ..services.wechat import explain
    from . import stores as stores_router
    if body.kind != "store":
        raise HTTPException(400, "只有店铺商品可以删除规格")
    want = list(dict.fromkeys([i for i in [body.sku_id, *body.sku_ids] if i]))
    if not want:
        raise HTTPException(400, "请选择要删除的规格")
    p = _get(db, user, "store", body.id)
    skus = [dict(k) for k in p.skus or []]
    by_id = {k["sku_id"]: k for k in skus}
    missing = [i for i in want if i not in by_id]
    if missing:
        raise HTTPException(404, "规格不存在，请先同步商品")
    left = [k for k in skus if not k.get("removed") and k["sku_id"] not in want]
    if not left:
        raise HTTPException(400, "不能删除全部规格，至少要留一个（不卖了请在商品管理里下架商品）")
    s = db.get(Store, p.store_id)
    c = stores_router.client_factory(s.appid, decrypt_secret(s.secret_enc))
    done, failed = [], []
    for i in want:
        try:
            c.update_stock(p.wx_product_id, i, 3, 0)
            by_id[i].update(stock=0, removed=True)
            done.append(i)
        except Exception as e:
            failed.append({"sku_id": i, "error": explain(e)})
    p.skus = skus
    flag_modified(p, "skus")
    p.stock = sum(x.get("stock") or 0 for x in skus)
    db.commit()
    if not done:
        raise HTTPException(400, f"微信小店改库存失败：{failed[0]['error']}")
    return {"ok": True, "removed": done, "failed": failed, "specs": len([x for x in skus if not x.get("removed")])}



# ---- 曝光: read by the 货源助手 on the 小店后台 商品列表 (the API has no such number) ---------------
class ExposureItem(BaseModel):
    product_id: str = Field(min_length=5, max_length=32)
    exposure: int = Field(ge=0, le=10**10)


class ExposureIn(BaseModel):
    items: list[ExposureItem] = Field(min_length=1, max_length=2000)


@router.post("/exposure")
def exposure(body: ExposureIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """近30天曝光 of the products on the 小店后台 商品列表: kept on the products (all the account's stores);
    找货源 lists the ones with the most 曝光 first. {matched, unknown}"""
    vals = {i.product_id: i.exposure for i in body.items}
    rows = db.scalars(select(StoreProduct).where(StoreProduct.store_id.in_(access.store_ids(user.tenant_id)),
                                                 StoreProduct.wx_product_id.in_(list(vals)))).all()
    now = datetime.now()
    for p in rows:
        p.exposure_30d, p.exposure_at = vals[p.wx_product_id], now
    db.commit()
    found = {p.wx_product_id for p in rows}
    # the exposed ones still without a 货源 (most 曝光 first): the plugin offers to find theirs now
    from .products import _linked_ids
    linked = set(db.scalars(_linked_ids(user.tenant_id)))
    exposed = [p for p in rows if (p.exposure_30d or 0) > 0]
    todo = sorted([p for p in exposed if p.id not in linked and not p.no_source], key=lambda p: -(p.exposure_30d or 0))
    todo = _unclaimed(db, user, "store", todo, True)  # not the ones another account is on
    names = {s.id: s.name for s in access.stores(db, user.tenant_id).values()}
    return {"matched": len(found), "unknown": [i for i in vals if i not in found][:50],
            "with_exposure": len(exposed), "sourced": sum(1 for p in exposed if p.id in linked),
            "no_source": sum(1 for p in exposed if p.no_source),
            "need_source": [_store_item(db, p, names) for p in todo[:50]]}
