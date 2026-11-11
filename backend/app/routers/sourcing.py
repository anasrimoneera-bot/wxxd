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


def job_out(j: SourcingJob, queries=None) -> dict:
    d = {"id": j.id, "status": j.status, "total": j.total, "done": j.done, "quantity": j.quantity,
         "sort_type": j.sort_type, "created_at": fmt_dt(j.created_at)}
    if queries is not None:
        d["queries"] = [{"id": q.id, "image_url": q.image_url, "title": q.title, "status": q.status,
                         "error": q.error, "offers": q.offers or [], "best_offer_id": q.best_offer_id,
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
    return job_out(j, qs)


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
    db.commit()
    return link_out(link)


def sources_for(db, tenant_id: int, column, ids: list[int]) -> dict[int, dict]:
    """{product id: source} for the product lists (商品云库 / 商品管理)."""
    if not ids:
        return {}
    rows = db.scalars(select(ProductSourceLink).where(ProductSourceLink.tenant_id == tenant_id, column.in_(ids)))
    key = "library_product_id" if column is ProductSourceLink.library_product_id else "store_product_id"
    return {getattr(r, key): link_out(r) for r in rows}
