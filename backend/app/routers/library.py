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


def item_out(p: LibraryProduct, groups: dict[int, str], published: dict[str, int] | None = None) -> dict:
    return {
        "published": (published or {}).get(f"{p.platform}:{p.source_product_id}", 0),
        "id": p.id, "title": p.title, "ai_title": p.ai_title or "", "cover": p.cover, "source_product_id": p.source_product_id,
        "platform": p.platform, "platform_name": PLATFORM_NAMES.get(p.platform, p.platform),
        "group_id": p.group_id, "group_name": groups.get(p.group_id or 0, ""), "has_video": p.has_video,
        "price_min": p.price_min, "price_max": p.price_max, "stock": p.stock, "sales": p.sales,
        "sku_count": p.sku_count, "imported_at": fmt_dt(p.imported_at),
    }


def _published_counts(db, user, rows) -> dict[str, int]:
    """How many of the tenant's stores each product has been published to."""
    from ..models import PublishedRecord, Store
    keys = [f"{p.platform}:{p.source_product_id}" for p in rows]
    if not keys:
        return {}
    store_ids = select(Store.id).where(Store.tenant_id == user.tenant_id)
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
                price_min: float | None = None, price_max: float | None = None):
    q = select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id)
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
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = build_query(user, name, ids, platform, group_id, price_min, price_max)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(LibraryProduct.id.desc()).offset(off).limit(lim)).all()
    g = _groups(db, user.tenant_id)
    return {"total": total, "items": [item_out(p, g, _published_counts(db, user, rows)) for p in rows]}


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
    _own_group(db, user, group_id)
    limit = get_settings().max_files_per_upload
    created = updated = 0
    errors = []
    count = 0
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
        text = data.decode("utf-8-sig", errors="replace")
        existing = db.scalar(select(LibraryProduct).where(
            LibraryProduct.tenant_id == user.tenant_id, LibraryProduct.platform == norm["platform"],
            LibraryProduct.source_product_id == norm["source_product_id"]))
        fields = {**summary(norm), "normalized": norm, "raw": text}
        if existing:  # re-upload of the same source updates the library entry
            for k, v in fields.items():
                setattr(existing, k, v)
            if group_id:
                existing.group_id = group_id
            updated += 1
        else:
            db.add(LibraryProduct(tenant_id=user.tenant_id, group_id=group_id, platform=norm["platform"],
                                  source_product_id=norm["source_product_id"], **fields))
            created += 1
        if (created + updated) % 100 == 0:
            db.flush()
    db.commit()
    return {"created": created, "updated": updated, "failed": len(errors), "errors": errors[:200]}


class IdsIn(BaseModel):
    ids: list[int] = Field(min_length=1)


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
    from ..models import PublishedRecord, Store, StoreProduct
    from ..security import decrypt_secret
    from ..services import product_sync
    from ..services.wechat import explain
    from . import stores as stores_router

    rows = db.scalars(select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                   LibraryProduct.id.in_(body.ids))).all()
    stores = {s.id: s for s in db.scalars(select(Store).where(Store.tenant_id == user.tenant_id))}
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


@router.put("/products/{pid}/title")
def set_title(pid: int, body: TitleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(LibraryProduct, pid)
    if not p or p.tenant_id != user.tenant_id:
        raise HTTPException(404, "商品不存在")
    p.ai_title = body.ai_title.strip()
    db.commit()
    return {"id": p.id, "ai_title": p.ai_title}
