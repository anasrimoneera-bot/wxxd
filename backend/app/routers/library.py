import io
import re
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import LibraryProduct, ProductGroup, PublishTemplate, User
from ..services.template_config import TemplateConfig
from ..services.titles import generate_title
from ..services.parser import ParseError, parse_package, summary
from .common import fmt_dt, page_params, xlsx_response

router = APIRouter(prefix="/api/library", tags=["library"])

PLATFORMS = [("taobao", "淘宝"), ("1688", "1688"), ("pdd", "拼多多"), ("kuaishou", "快手电商"), ("wechat", "微信小店")]
PLATFORM_NAMES = dict(PLATFORMS)


def item_out(p: LibraryProduct, groups: dict[int, str], published: dict[str, int] | None = None,
             sources: dict | None = None) -> dict:
    return {
        "source": (sources or {}).get(p.id),
        "published": (published or {}).get(f"{p.platform}:{p.source_product_id}", 0),
        "id": p.id, "title": p.title, "ai_title": p.ai_title or "", "cover": p.cover, "source_product_id": p.source_product_id,
        "platform": p.platform, "platform_name": PLATFORM_NAMES.get(p.platform, p.platform),
        "group_id": p.group_id, "group_name": groups.get(p.group_id or 0, ""), "has_video": p.has_video,
        "price_min": p.price_min, "price_max": p.price_max, "stock": p.stock, "sales": p.sales,
        "sku_count": p.sku_count, "imported_at": fmt_dt(p.imported_at),
        "no_source": bool(p.no_source), "no_source_note": p.no_source_note or "",
        # 共享云库: own upload (free) or added from the shared library (积分 per publish)
        "from_shared": p.origin_tenant_id is not None and p.origin_tenant_id != p.tenant_id,
        # 店铺改价 written back: used as the sale price for the next publish
        "sale_price_min": min(p.sale_prices.values()) if p.sale_prices else None,
        "sale_price_max": max(p.sale_prices.values()) if p.sale_prices else None,
        "sale_prices_at": fmt_dt(p.sale_prices_at) if p.sale_prices_at else "",
    }


def _published_counts(db, user, rows) -> dict[str, int]:
    """How many of the tenant's stores each product has been published to."""
    from ..models import PublishedRecord
    keys = [f"{p.platform}:{p.source_product_id}" for p in rows]
    if not keys:
        return {}
    from .. import access
    store_ids = access.store_ids(user.tenant_id)  # own stores and stores assigned in 用户管理
    return dict(db.execute(select(PublishedRecord.source_key, func.count()).where(
        PublishedRecord.source_key.in_(keys), PublishedRecord.store_id.in_(store_ids))
        .group_by(PublishedRecord.source_key)).all())


def _groups(db, tenant_id) -> dict[int, str]:
    return dict(db.execute(select(ProductGroup.id, ProductGroup.name).where(ProductGroup.tenant_id == tenant_id)).all())


def _own_group(db, user, gid: int | None):
    if gid is None:
        return None
    g = db.get(ProductGroup, gid)
    if not g or g.tenant_id != user.tenant_id:
        raise HTTPException(400, "分组不存在")
    return g


def build_query(user: User, name="", ids="", platform="", group_id: int | None = None,
                price_min: float | None = None, price_max: float | None = None, no_source: bool | None = None,
                source: str = ""):
    from ..models import ProductSourceLink
    q = select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id)
    if no_source is not None:
        q = q.where(LibraryProduct.no_source == no_source)
    linked = LibraryProduct.id.in_(select(ProductSourceLink.library_product_id).where(
        ProductSourceLink.tenant_id == user.tenant_id, ProductSourceLink.library_product_id.is_not(None)))
    if source == "linked":
        q = q.where(linked)
    elif source == "unlinked":  # 未找货源: no source yet and not marked 无货源
        q = q.where(~linked, LibraryProduct.no_source == False)  # noqa: E712
    elif source == "no_source":
        q = q.where(LibraryProduct.no_source == True)  # noqa: E712
    if name:
        q = q.where(LibraryProduct.title.contains(name))
    id_list = [x for x in re.split(r"[\s,，]+", ids or "") if x]
    if id_list:
        q = q.where(LibraryProduct.source_product_id.in_(id_list))
    if platform:
        q = q.where(LibraryProduct.platform == platform)
    if group_id:
        q = q.where(LibraryProduct.group_id == group_id)
    if price_min is not None:
        q = q.where(LibraryProduct.price_max >= int(price_min * 100))
    if price_max is not None:
        q = q.where(LibraryProduct.price_min <= int(price_max * 100))
    return q


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(LibraryProduct.platform, func.count()).where(
        LibraryProduct.tenant_id == user.tenant_id).group_by(LibraryProduct.platform)).all())
    return [{"platform": k, "name": n, "count": counts.get(k, 0)} for k, n in PLATFORMS]


@router.get("/products")
def list_products(page: int = 1, size: int = 20, name: str = "", ids: str = "", platform: str = "",
                  group_id: int | None = None, price_min: float | None = None, price_max: float | None = None,
                  no_source: bool | None = None, source: str = "",
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = build_query(user, name, ids, platform, group_id, price_min, price_max, no_source, source)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    unlinked = db.scalar(select(func.count()).select_from(build_query(
        user, name, ids, platform, group_id, price_min, price_max, None, "unlinked").subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(LibraryProduct.id.desc()).offset(off).limit(lim)).all()
    g = _groups(db, user.tenant_id)
    from ..models import ProductSourceLink
    from .sourcing import sources_for
    src = sources_for(db, user.tenant_id, ProductSourceLink.library_product_id, [p.id for p in rows])
    pub = _published_counts(db, user, rows)
    return {"total": total, "unlinked_count": unlinked, "items": [item_out(p, g, pub, src) for p in rows]}


@router.get("/products/export")
def export_products(name: str = "", ids: str = "", platform: str = "", group_id: int | None = None,
                    price_min: float | None = None, price_max: float | None = None,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = build_query(user, name, ids, platform, group_id, price_min, price_max)
    g = _groups(db, user.tenant_id)
    rows = [[p.title, p.source_product_id, g.get(p.group_id or 0, ""), PLATFORM_NAMES.get(p.platform, p.platform),
             "有" if p.has_video else "无", p.price_min / 100, p.price_max / 100, p.stock, p.sales, p.sku_count,
             fmt_dt(p.imported_at)] for p in db.scalars(q.order_by(LibraryProduct.id.desc()).limit(50000))]
    return xlsx_response("商品云库.xlsx", ["商品名称", "来源商品ID", "分组", "来源", "视频", "最低价", "最高价", "库存",
                                         "销量", "SKU数", "导入时间"], rows)


@router.get("/products/{pid}")
def product_detail(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(LibraryProduct, pid)
    if not p or p.tenant_id != user.tenant_id:
        raise HTTPException(404, "商品不存在")
    return {**item_out(p, _groups(db, user.tenant_id)), "normalized": p.normalized}


def _iter_files(files: list[UploadFile]):
    for f in files:
        data = f.file.read()
        name = f.filename or ""
        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    n = info.filename
                    if not info.is_dir() and n.lower().endswith((".json", ".txt")) and "__MACOSX" not in n:
                        yield n, z.read(info)
        elif name.lower().endswith((".json", ".txt")):
            yield name, data
        else:
            yield name, None


@router.post("/upload")
def upload(files: list[UploadFile] = File(...), group_id: int | None = Form(None),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Import data packages into the 共享云库. A source product that is already in the library (in this
    account, or uploaded by any other account) is skipped; at most 每日上传上限 new products per day."""
    from ..models import Tenant
    from ..services import points
    _own_group(db, user, group_id)
    limit = get_settings().max_files_per_upload
    tenant = db.get(Tenant, user.tenant_id)
    daily = points.daily_limit(tenant)
    left = (daily - points.uploads_today(db, user.tenant_id)) if daily else None
    created = skipped = 0
    errors, skipped_items = [], []
    count = 0
    seen = set()
    for name, data in _iter_files(files):
        count += 1
        if count > limit:
            errors.append({"file": name, "error": f"单次最多上传 {limit} 个，超出部分未处理"})
            break
        if data is None:
            errors.append({"file": name, "error": "仅支持 .json / .txt 数据包（或包含它们的 .zip）"})
            continue
        try:
            norm = parse_package(data, name)
        except ParseError as e:
            errors.append({"file": name, "error": str(e)})
            continue
        key = (norm["platform"], norm["source_product_id"])
        same = select(LibraryProduct).where(LibraryProduct.platform == key[0],
                                            LibraryProduct.source_product_id == key[1])
        reason = ""
        if key in seen or db.scalar(same.where(LibraryProduct.tenant_id == user.tenant_id).limit(1)):
            reason = "你的云库里已有相同商品"
        elif db.scalar(same.where(LibraryProduct.origin_tenant_id == LibraryProduct.tenant_id).limit(1)):
            reason = "共享云库里已有其他用户上传的相同商品，可在「共享云库」里加入"
        if reason:
            skipped += 1
            skipped_items.append({"file": name, "source_product_id": key[1], "reason": reason})
            continue
        if left is not None and left <= 0:
            errors.append({"file": name, "error": f"今日上传已达上限（每天 {daily} 个），明天再上传"})
            continue
        text = data.decode("utf-8-sig", errors="replace")
        db.add(LibraryProduct(tenant_id=user.tenant_id, origin_tenant_id=user.tenant_id, group_id=group_id,
                              platform=key[0], source_product_id=key[1],
                              **summary(norm), normalized=norm, raw=text))
        seen.add(key)
        created += 1
        if left is not None:
            left -= 1
        if created % 100 == 0:
            db.flush()
    db.commit()
    return {"created": created, "skipped": skipped, "failed": len(errors), "errors": errors[:200],
            "skipped_items": skipped_items[:200], "daily_limit": daily,
            "left_today": left}


# ---- 共享云库 -------------------------------------------------------------------
def _shared_query(user: User, name="", ids="", platform=""):
    """Originals uploaded by other accounts that this account has not added yet."""
    from sqlalchemy.orm import aliased
    mine = aliased(LibraryProduct)
    have = select(mine.id).where(mine.tenant_id == user.tenant_id, mine.platform == LibraryProduct.platform,
                                 mine.source_product_id == LibraryProduct.source_product_id).exists()
    q = select(LibraryProduct).where(LibraryProduct.origin_tenant_id == LibraryProduct.tenant_id,
                                     LibraryProduct.tenant_id != user.tenant_id,
                                     LibraryProduct.no_source == False,  # noqa: E712
                                     ~have)
    if name:
        q = q.where(LibraryProduct.title.contains(name))
    id_list = [x for x in re.split(r"[\s,，]+", ids or "") if x]
    if id_list:
        q = q.where(LibraryProduct.source_product_id.in_(id_list))
    if platform:
        q = q.where(LibraryProduct.platform == platform)
    return q


def shared_out(p: LibraryProduct, cost: int) -> dict:
    return {"id": p.id, "title": p.title, "cover": p.cover, "source_product_id": p.source_product_id,
            "platform": p.platform, "platform_name": PLATFORM_NAMES.get(p.platform, p.platform),
            "has_video": p.has_video, "price_min": p.price_min, "price_max": p.price_max, "stock": p.stock,
            "sales": p.sales, "sku_count": p.sku_count, "imported_at": fmt_dt(p.imported_at), "cost": cost}


@router.get("/shared")
def list_shared(page: int = 1, size: int = 20, name: str = "", ids: str = "", platform: str = "",
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    """共享云库: browse what other accounts uploaded (free); publishing it costs 积分 each time."""
    from ..services import settings_store
    q = _shared_query(user, name, ids, platform)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(LibraryProduct.id.desc()).offset(off).limit(lim)).all()
    cost = settings_store.get_int("points_per_publish")
    return {"total": total, "cost": cost, "items": [shared_out(p, cost) for p in rows]}


def _copy_source(db, original: LibraryProduct, tenant_id: int) -> None:
    """The uploader's 货源 comes with a product added from the 共享云库 (the account can change it)."""
    from ..models import ProductSourceLink
    from ..services.library_sync import LINK_FIELDS
    src = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == original.tenant_id,
                                                    ProductSourceLink.library_product_id == original.id))
    copy = db.scalar(select(LibraryProduct).where(LibraryProduct.tenant_id == tenant_id,
                                                  LibraryProduct.platform == original.platform,
                                                  LibraryProduct.source_product_id == original.source_product_id))
    if src and copy:
        db.add(ProductSourceLink(tenant_id=tenant_id, library_product_id=copy.id,
                                 **{f: getattr(src, f) for f in LINK_FIELDS}))


class AddSharedIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    group_id: int | None = None


@router.post("/shared/add")
def add_shared(body: AddSharedIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Add shared products to this account's 商品云库 (free). Each publish of them costs 积分."""
    _own_group(db, user, body.group_id)
    added = 0
    for p in db.scalars(select(LibraryProduct).where(LibraryProduct.id.in_(body.ids))).all():
        if p.origin_tenant_id != p.tenant_id or p.tenant_id == user.tenant_id:
            continue  # not a shared original, or already this account's own
        if db.scalar(select(LibraryProduct.id).where(LibraryProduct.tenant_id == user.tenant_id,
                                                     LibraryProduct.platform == p.platform,
                                                     LibraryProduct.source_product_id == p.source_product_id)):
            continue
        db.add(LibraryProduct(
            tenant_id=user.tenant_id, origin_tenant_id=p.tenant_id, group_id=body.group_id, platform=p.platform,
            source_product_id=p.source_product_id, title=p.title, cover=p.cover, has_video=p.has_video,
            price_min=p.price_min, price_max=p.price_max, stock=p.stock, sales=p.sales, sku_count=p.sku_count,
            normalized=p.normalized, raw=p.raw))
        db.flush()
        _copy_source(db, p, user.tenant_id)
        added += 1
    db.commit()
    return {"added": added}


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1)


class NoSourceIn(IdsIn):
    no_source: bool


@router.post("/products/no-source")
def mark_no_source(body: NoSourceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """标记 / 取消 无货源. Marked products stay in the library but are skipped when publishing."""
    n = db.execute(update(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                LibraryProduct.id.in_(body.ids))
                   .values(no_source=body.no_source, no_source_note="手动标记" if body.no_source else "")).rowcount
    db.commit()
    return {"updated": n}


class MoveIn(IdsIn):
    group_id: int | None = None


class DeleteIn(IdsIn):
    # What to do with copies already published to WeChat stores:
    # delete = delete them in the store (delisted first), delisting = only take them off sale, none = leave them
    store_action: str = Field(default="delete", pattern="^(delete|delisting|none)$")


@router.post("/products/delete")
def delete_products(body: DeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Bulk-delete library products (e.g. no longer available at the source) and, by default,
    also delete or delist the products already published from them in the tenant's WeChat stores."""
    from ..models import PublishedRecord, StoreProduct
    from ..security import decrypt_secret
    from ..services import product_sync
    from ..services.wechat import explain
    from . import stores as stores_router

    rows = db.scalars(select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                   LibraryProduct.id.in_(body.ids))).all()
    from .. import access
    stores = access.stores(db, user.tenant_id)
    clients: dict = {}
    store_results = []
    if body.store_action != "none" and stores:
        keys = {f"{p.platform}:{p.source_product_id}": p for p in rows}
        records = db.scalars(select(PublishedRecord).where(PublishedRecord.source_key.in_(list(keys) or [""]),
                                                           PublishedRecord.store_id.in_(list(stores)))).all()
        for rec in records:
            store = stores[rec.store_id]
            item = {"store": store.name, "wx_product_id": rec.wx_product_id,
                    "title": keys[rec.source_key].title, "ok": True}
            try:
                if rec.wx_product_id:
                    c = clients.get(store.id) or stores_router.client_factory(store.appid,
                                                                             decrypt_secret(store.secret_enc))
                    clients[store.id] = c
                    sp = db.scalar(select(StoreProduct).where(StoreProduct.store_id == store.id,
                                                              StoreProduct.wx_product_id == rec.wx_product_id))
                    try:
                        c.delisting(rec.wx_product_id)  # always safe; delete requires the product off sale
                    except Exception:
                        if body.store_action == "delisting":
                            raise
                    if body.store_action == "delete":
                        c.delete_product(rec.wx_product_id)
                        if sp:
                            db.delete(sp)
                        db.delete(rec)
                    elif sp:
                        sp.status = product_sync.DELISTED
            except Exception as e:
                item.update(ok=False, error=explain(e))
            store_results.append(item)
    for p in rows:
        db.delete(p)
    db.commit()
    return {"deleted": len(rows), "store_results": store_results}


@router.post("/products/move")
def move_products(body: MoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _own_group(db, user, body.group_id)
    r = db.execute(update(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                LibraryProduct.id.in_(body.ids)).values(group_id=body.group_id))
    db.commit()
    return {"moved": r.rowcount}


# ---- groups ---------------------------------------------------------------
class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.get("/groups")
def list_groups(platform: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    cq = select(LibraryProduct.group_id, func.count()).where(LibraryProduct.tenant_id == user.tenant_id)
    if platform:
        cq = cq.where(LibraryProduct.platform == platform)
    counts = dict(db.execute(cq.group_by(LibraryProduct.group_id)).all())
    rows = db.scalars(select(ProductGroup).where(ProductGroup.tenant_id == user.tenant_id).order_by(ProductGroup.id))
    return [{"id": g.id, "name": g.name, "count": counts.get(g.id, 0)} for g in rows]


@router.post("/groups")
def create_group(body: GroupIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if db.scalar(select(ProductGroup).where(ProductGroup.tenant_id == user.tenant_id, ProductGroup.name == body.name)):
        raise HTTPException(400, "分组已存在")
    g = ProductGroup(tenant_id=user.tenant_id, name=body.name)
    db.add(g)
    db.commit()
    return {"id": g.id, "name": g.name, "count": 0}


@router.put("/groups/{gid}")
def rename_group(gid: int, body: GroupIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _own_group(db, user, gid)
    g.name = body.name
    db.commit()
    return {"id": g.id, "name": g.name}


@router.delete("/groups/{gid}")
def delete_group(gid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _own_group(db, user, gid)
    db.execute(update(LibraryProduct).where(LibraryProduct.group_id == g.id).values(group_id=None))
    db.delete(g)
    db.commit()
    return {"ok": True}


# ---- titles -------------------------------------------------------------------
class RegenerateIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=20)  # the UI sends batches, showing progress
    template_id: int | None = None  # use this template's length / banned words; default rules otherwise


class TitleIn(BaseModel):
    ai_title: str = Field(default="", max_length=60)  # empty = clear (publish generates one again)


@router.post("/products/regenerate-titles")
def regenerate_titles(body: RegenerateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cfg = TemplateConfig()
    if body.template_id:
        t = db.get(PublishTemplate, body.template_id)
        if not t or t.tenant_id != user.tenant_id:
            raise HTTPException(400, "上货模板不存在")
        cfg = TemplateConfig.model_validate(t.config)
    rows = db.scalars(select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                   LibraryProduct.id.in_(body.ids))).all()
    results = []
    for p in rows:
        try:
            p.ai_title = generate_title(p.title, cfg, strict=True)[:255]
            results.append({"id": p.id, "ai_title": p.ai_title})
        except Exception as e:
            msg = str(e)
            if "未设置 DeepSeek" in msg:
                raise HTTPException(400, "AI 改写未启用：请管理员在「系统设置」中填写 DeepSeek API Key")
            results.append({"id": p.id, "error": msg[:200]})
    db.commit()
    return {"results": results}


@router.delete("/products/{pid}/sale-prices")
def clear_sale_prices(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """清除店铺改价: publish with the template's price formula again."""
    p = db.get(LibraryProduct, pid)
    if not p or p.tenant_id != user.tenant_id:
        raise HTTPException(404, "商品不存在")
    p.sale_prices, p.sale_prices_at = None, None
    db.commit()
    return {"ok": True}


@router.put("/products/{pid}/title")
def set_title(pid: int, body: TitleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(LibraryProduct, pid)
    if not p or p.tenant_id != user.tenant_id:
        raise HTTPException(404, "商品不存在")
    p.ai_title = body.ai_title.strip()
    db.commit()
    return {"id": p.id, "ai_title": p.ai_title}
