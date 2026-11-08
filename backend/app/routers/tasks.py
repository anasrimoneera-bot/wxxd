from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import LibraryProduct, ProductGroup, PublishTask, PublishTaskItem, PublishTemplate, Store, User
from .common import fmt_dt, page_params, xlsx_response
from .library import PLATFORM_NAMES

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUS_NAMES = {"pending": "等待中", "running": "执行中", "success": "已完成", "partial": "部分失败", "failed": "执行失败"}
ITEM_STATUS_NAMES = {"pending": "待执行", "running": "执行中", "success": "成功", "failed": "失败", "skipped": "已跳过"}


class CreateIn(BaseModel):
    store_ids: list[int] = Field(min_length=1)
    platform: str = "wechat"
    group_id: int | None = None  # None = all products of the platform
    product_ids: list[int] | None = None  # optional explicit selection


def task_out(t: PublishTask) -> dict:
    done = t.success + t.failed
    return {
        "id": t.id, "name": t.name, "store_id": t.store_id, "store_name": t.store_name,
        "template_name": t.template_name, "worker": t.worker, "scope": t.scope, "status": t.status,
        "status_name": STATUS_NAMES.get(t.status, t.status), "total": t.total, "success": t.success,
        "failed": t.failed, "remaining": max(t.total - done, 0),
        "progress": round(done * 100 / t.total, 2) if t.total else 100,
        "created_at": fmt_dt(t.created_at), "started_at": fmt_dt(t.started_at), "finished_at": fmt_dt(t.finished_at),
    }


@router.post("")
def create_tasks(body: CreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(LibraryProduct.id, LibraryProduct.platform, LibraryProduct.source_product_id, LibraryProduct.title,
               LibraryProduct.cover).where(LibraryProduct.tenant_id == user.tenant_id,
                                           LibraryProduct.platform == body.platform)
    scope = f"{PLATFORM_NAMES.get(body.platform, body.platform)} / "
    if body.product_ids:
        q = q.where(LibraryProduct.id.in_(body.product_ids))
        scope += f"已选 {len(body.product_ids)} 个商品"
    elif body.group_id:
        g = db.get(ProductGroup, body.group_id)
        if not g or g.tenant_id != user.tenant_id:
            raise HTTPException(400, "分组不存在")
        q = q.where(LibraryProduct.group_id == g.id)
        scope += g.name
    else:
        scope += "全部商品"
    products = db.execute(q.order_by(LibraryProduct.id)).all()
    if not products:
        raise HTTPException(400, "所选范围内没有商品")

    stores = []
    for sid in body.store_ids:
        s = db.get(Store, sid)
        if not s or s.tenant_id != user.tenant_id:
            raise HTTPException(404, "店铺不存在")
        missing = [n for n, v in (("上货模板", s.publish_template_id), ("运费模板", s.freight_template_id),
                                  ("退货地址", s.address_id)) if not v]
        if missing:
            raise HTTPException(400, f"店铺「{s.name}」未配置：{'、'.join(missing)}")
        stores.append(s)

    created = []
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    for s in stores:
        tpl = db.get(PublishTemplate, s.publish_template_id)
        t = PublishTask(tenant_id=user.tenant_id, name=f"云发布-{s.name}-{stamp}", store_id=s.id, store_name=s.name,
                        template_id=tpl.id, template_name=tpl.name, template_snapshot=tpl.config, scope=scope,
                        total=len(products))
        db.add(t)
        db.flush()
        db.add_all([PublishTaskItem(task_id=t.id, library_product_id=p.id, platform=p.platform,
                                    source_product_id=p.source_product_id, title=p.title, cover=p.cover)
                    for p in products])
        created.append(t)
    db.commit()
    return [task_out(t) for t in created]


@router.get("")
def list_tasks(page: int = 1, size: int = 20, store_name: str = "", status: str = "",
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(PublishTask).where(PublishTask.tenant_id == user.tenant_id)
    if store_name:
        q = q.where(PublishTask.store_name.contains(store_name))
    if status:
        q = q.where(PublishTask.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(PublishTask.id.desc()).offset(off).limit(lim)).all()
    return {"total": total, "items": [task_out(t) for t in rows]}


def _own_task(db, user, tid) -> PublishTask:
    t = db.get(PublishTask, tid)
    if not t or t.tenant_id != user.tenant_id:
        raise HTTPException(404, "任务不存在")
    return t


def item_out(i: PublishTaskItem) -> dict:
    return {
        "id": i.id, "title": i.title, "cover": i.cover, "library_product_id": i.library_product_id,
        "platform_name": PLATFORM_NAMES.get(i.platform, i.platform), "source_product_id": i.source_product_id,
        "wx_category": i.wx_category, "status": i.status, "status_name": ITEM_STATUS_NAMES.get(i.status, i.status),
        "wx_product_id": i.wx_product_id, "message": i.message, "worker": i.worker,
    }


@router.get("/{tid}/items")
def task_items(tid: int, page: int = 1, size: int = 50, status: str = "",
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _own_task(db, user, tid)
    q = select(PublishTaskItem).where(PublishTaskItem.task_id == t.id)
    if status:
        q = q.where(PublishTaskItem.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(PublishTaskItem.id).offset(off).limit(lim)).all()
    return {"task": task_out(t), "total": total, "items": [item_out(i) for i in rows]}


@router.get("/{tid}/export")
def export_items(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _own_task(db, user, tid)
    rows = [[i.title, i.library_product_id, PLATFORM_NAMES.get(i.platform, i.platform), i.source_product_id,
             i.wx_category, ITEM_STATUS_NAMES.get(i.status, i.status), i.wx_product_id, i.message]
            for i in db.scalars(select(PublishTaskItem).where(PublishTaskItem.task_id == t.id)
                                .order_by(PublishTaskItem.id))]
    return xlsx_response(f"{t.name}.xlsx", ["商品", "云库商品ID", "来源平台", "来源商品ID", "上货微信小店类目", "状态",
                                            "微信商品ID", "执行结果/失败原因"], rows)


@router.post("/{tid}/retry-failed")
def retry_failed(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _own_task(db, user, tid)
    if t.status in ("pending", "running"):
        raise HTTPException(400, "任务执行中，结束后再重试")
    n = db.execute(update(PublishTaskItem).where(PublishTaskItem.task_id == t.id,
                                                 PublishTaskItem.status == "failed")
                   .values(status="pending", message="", worker="")).rowcount
    if n:
        t.failed -= n
        t.status, t.finished_at = "pending", None
    db.commit()
    return {"requeued": n}
