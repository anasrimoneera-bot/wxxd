"""找货源 API: batch 1688 image search (≤10 images) and 设为货源."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import LibraryProduct, ProductSourceLink, SourcingJob, SourcingQuery, StoreProduct, User
from ..services import sourcing
from .common import fmt_dt

router = APIRouter(prefix="/api/sourcing", tags=["sourcing"])
MAX_IMAGES = 10


class JobIn(BaseModel):
    image_urls: list[str] = Field(default_factory=list)
    library_product_ids: list[int] = Field(default_factory=list)
    store_product_ids: list[int] = Field(default_factory=list)
    quantity: int = Field(default=1, ge=1, le=100000)
    sort_type: str = Field(default="", pattern="^(|price_asc|price_desc|sold_desc|yx_desc)$")
    crops: dict[str, list[float]] = Field(default_factory=dict)  # 框选主体 by image URL: [x, y, w, h] fractions


def check_crop(crop: list[float] | None) -> list[float] | None:
    """None/empty = whole image. Otherwise 4 fractions inside the image, at least 5% wide and high."""
    if not crop:
        return None
    if len(crop) != 4:
        raise HTTPException(400, "框选区域格式不正确")
    x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in crop)
    w, h = min(w, 1 - x), min(h, 1 - y)
    if w < 0.05 or h < 0.05:
        raise HTTPException(400, "框选区域太小，请框住整个商品")
    if x == 0 and y == 0 and w == 1 and h == 1:
        return None
    return [round(v, 4) for v in (x, y, w, h)]


def job_out(j: SourcingJob, queries=None) -> dict:
    d = {"id": j.id, "status": j.status, "total": j.total, "done": j.done, "quantity": j.quantity,
         "sort_type": j.sort_type, "created_at": fmt_dt(j.created_at)}
    if queries is not None:
        d["queries"] = [{"id": q.id, "image_url": q.image_url, "title": q.title, "status": q.status,
                         "error": q.error, "offers": q.offers or [], "hidden_count": q.hidden_count or 0,
                         "crop": q.crop, "store_product_id": q.store_product_id,
                         "best_offer_id": q.best_offer_id,
                         "linkable": bool(q.library_product_id or q.store_product_id)} for q in queries]
    return d


@router.post("/jobs")
def create_job(body: JobIn, background: BackgroundTasks, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    items: list[dict] = []
    for url in body.image_urls:
        url = url.strip()
        if url:
            if not url.startswith(("http://", "https://")):
                raise HTTPException(400, f"不是有效的图片链接：{url[:60]}")
            items.append({"image_url": url})
    for p in db.scalars(select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id,
                                                     LibraryProduct.id.in_(body.library_product_ids or [0]))):
        items.append({"image_url": p.cover, "title": p.title, "library_product_id": p.id})
    for p in db.scalars(select(StoreProduct).where(StoreProduct.tenant_id == user.tenant_id,
                                                   StoreProduct.id.in_(body.store_product_ids or [0]))):
        items.append({"image_url": p.cover, "title": p.title, "store_product_id": p.id})
    items = [i for i in items if i["image_url"]]
    for i in items:
        i["crop"] = check_crop(body.crops.get(i["image_url"]))
    if not items:
        raise HTTPException(400, "请提供至少 1 张商品主图")
    if len(items) > MAX_IMAGES:
        raise HTTPException(400, f"一次最多 {MAX_IMAGES} 张图片")
    job = SourcingJob(tenant_id=user.tenant_id, user_id=user.id, quantity=body.quantity,
                      sort_type=body.sort_type, total=len(items))
    db.add(job)
    db.flush()
    db.add_all([SourcingQuery(job_id=job.id, tenant_id=user.tenant_id, **i) for i in items])
    db.commit()
    background.add_task(sourcing.run_job, job.id)
    return job_out(job)


@router.get("/jobs")
def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(SourcingJob).where(SourcingJob.tenant_id == user.tenant_id)
                      .order_by(SourcingJob.id.desc()).limit(30)).all()
    return [job_out(j) for j in rows]


def _own_job(db, user, jid) -> SourcingJob:
    j = db.get(SourcingJob, jid)
    if not j or j.tenant_id != user.tenant_id:
        raise HTTPException(404, "找货任务不存在")
    return j


@router.get("/jobs/{jid}")
def get_job(jid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    j = _own_job(db, user, jid)
    qs = db.scalars(select(SourcingQuery).where(SourcingQuery.job_id == j.id).order_by(SourcingQuery.id)).all()
    out = job_out(j, qs)
    lib = sources_for(db, user.tenant_id, ProductSourceLink.library_product_id,
                      [q.library_product_id for q in qs if q.library_product_id])
    sp = sources_for(db, user.tenant_id, ProductSourceLink.store_product_id,
                     [q.store_product_id for q in qs if q.store_product_id])
    for q, d in zip(qs, out["queries"]):
        src = lib.get(q.library_product_id) if q.library_product_id else sp.get(q.store_product_id)
        d["linked_offer_id"] = src["offer_id"] if src else ""
    return out


class ResearchIn(BaseModel):
    crop: list[float] | None = None  # None = whole image


@router.post("/queries/{qid}/research")
def research(qid: int, body: ResearchIn, background: BackgroundTasks, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    """框选主体后重新搜索: search one image of a job again, with the framed region (or the whole image)."""
    q = db.get(SourcingQuery, qid)
    if not q or q.tenant_id != user.tenant_id:
        raise HTTPException(404, "找货记录不存在")
    job = db.get(SourcingJob, q.job_id)
    if q.status == "pending" or job.done < job.total:
        raise HTTPException(400, "正在搜索中，请稍后再试")
    q.crop, q.status, q.error = check_crop(body.crop), "pending", ""
    q.offers, q.hidden_count, q.best_offer_id = [], 0, ""
    job.status = "running"
    db.commit()
    background.add_task(sourcing.rerun_query, q.id)
    return {"ok": True}


class LinkIn(BaseModel):
    query_id: int
    offer_id: str


def link_out(link: ProductSourceLink | None) -> dict | None:
    if not link:
        return None
    return {"offer_id": link.offer_id, "title": link.title, "image_url": link.image_url,
            "detail_url": link.detail_url, "supplier": link.supplier, "price": link.price,
            "moq": link.moq, "unit": link.unit, "linked_at": fmt_dt(link.linked_at)}


@router.post("/link")
def set_source(body: LinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.get(SourcingQuery, body.query_id)
    if not q or q.tenant_id != user.tenant_id:
        raise HTTPException(404, "找货记录不存在")
    if not (q.library_product_id or q.store_product_id):
        raise HTTPException(400, "这张图片是手动粘贴的链接，没有对应的商品，无法设为货源")
    offer = next((o for o in q.offers or [] if o["offer_id"] == body.offer_id), None)
    if not offer:
        raise HTTPException(404, "货源不存在")
    col = ProductSourceLink.library_product_id if q.library_product_id else ProductSourceLink.store_product_id
    target = q.library_product_id or q.store_product_id
    link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == user.tenant_id, col == target))
    if link is None:
        link = ProductSourceLink(tenant_id=user.tenant_id, library_product_id=q.library_product_id,
                                 store_product_id=q.store_product_id)
        db.add(link)
    link.offer_id, link.title, link.image_url = offer["offer_id"], offer["title"][:255], offer["image_url"]
    link.detail_url, link.supplier = offer["detail_url"], offer["supplier"][:255]
    link.price = round((offer["price"] or 0) * 100)
    link.moq, link.unit = offer["moq"], offer["unit"]
    from datetime import datetime
    link.linked_at = datetime.now()
    # A source was found: the 商品云库 product may be listed again
    if q.library_product_id:
        libs = [db.get(LibraryProduct, q.library_product_id)]
    else:
        from ..services.product_sync import library_products_for
        sp = db.get(StoreProduct, q.store_product_id)
        libs = library_products_for(db, user.tenant_id, [sp]) if sp else []
    for lib in libs:
        if lib and lib.no_source:
            lib.no_source, lib.no_source_note = False, ""
    db.commit()
    return link_out(link)


class UnlinkIn(BaseModel):
    query_id: int | None = None
    library_product_id: int | None = None
    store_product_id: int | None = None


@router.post("/unlink")
def cancel_source(body: UnlinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """取消货源. For a 商品管理 product whose source comes from 商品云库, the 商品云库 source is cancelled."""
    lib_id, sp_id = body.library_product_id, body.store_product_id
    if body.query_id:
        q = db.get(SourcingQuery, body.query_id)
        if not q or q.tenant_id != user.tenant_id:
            raise HTTPException(404, "找货记录不存在")
        lib_id, sp_id = q.library_product_id, q.store_product_id
    if not (lib_id or sp_id):
        raise HTTPException(400, "请指定商品")
    col, target = ((ProductSourceLink.library_product_id, lib_id) if lib_id
                   else (ProductSourceLink.store_product_id, sp_id))
    link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == user.tenant_id, col == target))
    if link is None and sp_id:
        sp = db.get(StoreProduct, sp_id)
        if sp and sp.tenant_id == user.tenant_id:
            from ..services.product_sync import library_products_for
            ids = [p.id for p in library_products_for(db, user.tenant_id, [sp])]
            link = db.scalar(select(ProductSourceLink).where(ProductSourceLink.tenant_id == user.tenant_id,
                                                              ProductSourceLink.library_product_id.in_(ids or [0])))
    if link is None:
        raise HTTPException(404, "该商品没有设置货源")
    from_library = bool(link.library_product_id) and not lib_id
    db.delete(link)
    db.commit()
    return {"ok": True, "from_library": from_library}


def sources_for(db, tenant_id: int, column, ids: list[int]) -> dict[int, dict]:
    """{product id: source} for the product lists (商品云库 / 商品管理)."""
    if not ids:
        return {}
    rows = db.scalars(select(ProductSourceLink).where(ProductSourceLink.tenant_id == tenant_id, column.in_(ids)))
    key = "library_product_id" if column is ProductSourceLink.library_product_id else "store_product_id"
    return {getattr(r, key): link_out(r) for r in rows}
