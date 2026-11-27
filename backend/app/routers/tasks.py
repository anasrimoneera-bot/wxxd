import random
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import (LibraryProduct, ProductGroup, PublishedRecord, PublishTask, PublishTaskItem, PublishTemplate,
                      Store, User)
from .common import fmt_dt, page_params, xlsx_response
from .library import PLATFORM_NAMES

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUS_NAMES = {"pending": "等待中", "running": "执行中", "success": "已完成", "partial": "部分失败", "failed": "执行失败",
                "cancelled": "已撤销"}
ITEM_STATUS_NAMES = {"pending": "待执行", "running": "执行中", "success": "成功", "failed": "失败", "skipped": "已跳过",
                     "cancelled": "已撤销"}


class CreateIn(BaseModel):
    store_ids: list[int] = Field(min_length=1)
    platform: str = "wechat"
    group_id: int | None = None  # None = all products of the platform
    product_ids: list[int] | None = None  # optional explicit selection
    # 随机选取: each store gets this many products picked at random from the range (none it already has)
    random_count: int | None = Field(default=None, ge=1, le=10000)
    # 商品来源: mine = 我的云库, shared = 共享云库 (others' uploads, added to 我的云库 when picked), all = both
    library: str = Field(default="mine", pattern="^(mine|shared|all)$")


def task_out(t: PublishTask) -> dict:
    cancelled = t.cancelled or 0
    done = t.success + t.failed + cancelled
    return {
        "id": t.id, "name": t.name, "store_id": t.store_id, "store_name": t.store_name,
        "template_name": t.template_name, "worker": t.worker, "scope": t.scope, "status": t.status,
        "status_name": STATUS_NAMES.get(t.status, t.status), "total": t.total, "success": t.success,
        "failed": t.failed, "cancelled": cancelled, "remaining": max(t.total - done, 0),
        "progress": round(done * 100 / t.total, 2) if t.total else 100,
        "created_at": fmt_dt(t.created_at), "started_at": fmt_dt(t.started_at), "finished_at": fmt_dt(t.finished_at),
    }


def _chunks(xs, n=1000):
    xs = list(xs)
    return [xs[i:i + n] for i in range(0, len(xs), n)]


class Listings:
    """What each store has or is about to get (published + items still waiting in tasks + planned now), as
    source keys, for the 店铺商品相同率 limit."""
    def __init__(self, db):
        self.db = db
        self.by_store: dict[int, set[str]] = {}
        self.holders: dict[str, set[int]] = {}
        self.loaded_stores: set[int] = set()
        self.loaded_keys: set[str] = set()

    def add(self, sid, key):
        self.by_store.setdefault(sid, set()).add(key)
        self.holders.setdefault(key, set()).add(sid)

    def _pending(self):
        return (select(PublishTask.store_id, PublishTaskItem.platform, PublishTaskItem.source_product_id)
                .join(PublishTask, PublishTask.id == PublishTaskItem.task_id)
                .where(PublishTaskItem.status.in_(("pending", "running"))))

    def load_store(self, sid: int):
        if sid in self.loaded_stores:
            return
        self.loaded_stores.add(sid)
        self.by_store.setdefault(sid, set())
        for k in self.db.scalars(select(PublishedRecord.source_key).where(PublishedRecord.store_id == sid)):
            self.add(sid, k)
        for _, pf, spid in self.db.execute(self._pending().where(PublishTask.store_id == sid)):
            self.add(sid, f"{pf}:{spid}")

    def load_keys(self, keys):
        """Every store that has one of these keys, with all its keys (its size counts too)."""
        keys = [k for k in set(keys) if k not in self.loaded_keys]
        self.loaded_keys.update(keys)
        stores = set()
        for part in _chunks(keys):
            stores.update(self.db.scalars(select(PublishedRecord.store_id).where(PublishedRecord.source_key.in_(part))))
            pend = self._pending().where((PublishTaskItem.platform + ":" + PublishTaskItem.source_product_id).in_(part))
            stores.update(r[0] for r in self.db.execute(pend))
        for sid in stores:
            self.load_store(sid)


def limit_overlap(listings: Listings, sid: int, cands: list, want: int | None, pct: int) -> tuple[list, int]:
    """The products for store sid, keeping the 店铺商品相同率 <= pct: products no other store has first, then
    shared ones while, for every other store T, the products both have stay within pct of sid's products and
    of T's. Returns (picked, left out because of the limit)."""
    key = lambda p: f"{p.platform}:{p.source_product_id}"  # noqa: E731
    listings.load_store(sid)
    mine = listings.by_store[sid]
    have = [p for p in cands if key(p) in mine]  # already in the store: still listed, the Worker skips them
    cands = [p for p in cands if key(p) not in mine]
    listings.load_keys([key(p) for p in cands] + list(mine))
    others = lambda k: listings.holders.get(k, set()) - {sid}  # noqa: E731
    overlap: dict[int, int] = {}
    for k in mine:
        for t in others(k):
            overlap[t] = overlap.get(t, 0) + 1
    unique = [p for p in cands if not others(key(p))]
    shared = [p for p in cands if others(key(p))]
    picked = unique[:want] if want else list(unique)
    target = len(mine) + (min(want, len(cands)) if want else len(cands))  # the store's size if all go in
    cap = lambda t: pct * len(listings.by_store.get(t, ())) / 100  # noqa: E731  (T's own side)
    extra = []
    for p in shared:
        if want and len(picked) + len(extra) >= want:
            break
        ts = others(key(p))
        if any(overlap.get(t, 0) + 1 > min(pct * target / 100, cap(t)) for t in ts):
            continue
        extra.append(p)
        for t in ts:
            overlap[t] = overlap.get(t, 0) + 1
    # fewer went in than counted on: drop shared ones until every pair is within pct of the real size
    while extra:
        size = len(mine) + len(picked) + len(extra)
        over = {t for t, n in overlap.items() if n > pct * size / 100}
        if not over:
            break
        involved = [k for k, p in enumerate(extra) if others(key(p)) & over]
        if not involved:  # over already by what the stores have: none of the new ones adds to it
            break
        i = involved[-1]
        for t in others(key(extra.pop(i))):
            overlap[t] -= 1
    left_out = len(shared) - len(extra) if not want else max(min(want - len(picked), len(shared)) - len(extra), 0)
    picked += extra
    for p in picked:  # planned now: the next stores of this request count them
        listings.add(sid, key(p))
    return ([] if want else have) + picked, left_out


@router.post("")
def create_tasks(body: CreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(LibraryProduct.id, LibraryProduct.platform, LibraryProduct.source_product_id, LibraryProduct.title,
               LibraryProduct.cover).where(LibraryProduct.tenant_id == user.tenant_id,
                                           LibraryProduct.platform == body.platform)
    scope = f"{PLATFORM_NAMES.get(body.platform, body.platform)} / "
    if body.library != "mine" and (body.product_ids or body.group_id):
        raise HTTPException(400, "共享云库没有分组，请选「全部商品」")
    scope += {"mine": "", "shared": "共享云库 · ", "all": "我的云库+共享云库 · "}[body.library]
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
    # 无货源 (marked in 商品云库, or skipped in the 1688 货源助手) are never listed: left out of the task
    no_source = 0
    products = []
    if body.library != "shared":
        no_source = db.scalar(select(func.count()).select_from(q.where(LibraryProduct.no_source == True).subquery()))  # noqa: E712
        products = db.execute(q.where(LibraryProduct.no_source == False).order_by(LibraryProduct.id)).all()  # noqa: E712
    shared_ids: set[int] = set()
    if body.library != "mine":  # 共享云库: others' uploads this account has not added (无货源 ones are not shared)
        from .library import _shared_query
        sq = _shared_query(user, platform=body.platform).with_only_columns(
            LibraryProduct.id, LibraryProduct.platform, LibraryProduct.source_product_id, LibraryProduct.title,
            LibraryProduct.cover).order_by(LibraryProduct.id)
        got = db.execute(sq).all()
        shared_ids = {r.id for r in got}
        products = [*products, *got]
    from collections import namedtuple
    Row = namedtuple("Row", "id platform source_product_id title cover")
    products = [Row(*tuple(r)) for r in products]
    if not products:
        raise HTTPException(400, f"所选的 {no_source} 个商品都已标记「无货源」，不会上架" if no_source else "所选范围内没有商品")
    if no_source:
        scope += f"（已过滤 {no_source} 个无货源）"

    stores = []
    for sid in body.store_ids:
        from .. import access
        s = db.get(Store, sid)
        if not access.can_use(db, user.tenant_id, s):  # own stores and stores assigned in 用户管理
            raise HTTPException(404, "店铺不存在")
        cfg = access.config(db, user.tenant_id, s)  # an assigned store: the account's own 发布配置
        missing = [n for n, v in (("上货模板", cfg["publish_template_id"]), ("运费模板", cfg["freight_template_id"]),
                                  ("退货地址", cfg["address_id"])) if not v]
        if missing:
            raise HTTPException(400, f"店铺「{s.name}」未配置：{'、'.join(missing)}")
        from .stores import check_config
        bad = check_config(s, cfg)  # e.g. 批量配置 gave it another store's template: every product would fail
        if bad:
            raise HTTPException(400, f"店铺「{s.name}」的{'、'.join(bad)}在这个店铺里不存在，请在发布配置里给这个店铺重新选择")
        stores.append((s, cfg))

    created = []
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    from ..services import settings_store
    pct = settings_store.get_int("max_overlap_pct")
    listings = Listings(db)
    for s, cfg in stores:
        picked, store_scope, left = products, scope, 0
        if body.random_count:  # its own random pick, leaving out what this store already has
            have = set(db.scalars(select(PublishedRecord.source_key).where(PublishedRecord.store_id == s.id)))
            fresh = [p for p in products if f"{p.platform}:{p.source_product_id}" not in have]
            if not fresh:
                raise HTTPException(400, f"店铺「{s.name}」已经上过这个范围里的全部商品，没有可以随机选取的了")
            random.shuffle(fresh)
            picked, left = limit_overlap(listings, s.id, fresh, body.random_count, pct) if pct else (fresh[:body.random_count], 0)
            store_scope = f"{scope} · 随机 {len(picked)} 个（可选 {len(fresh)} 个）"
        elif pct:
            picked, left = limit_overlap(listings, s.id, products, None, pct)
        if left:
            store_scope += f" · 相同率≤{pct}% 跳过 {left} 个"
        if not picked:
            raise HTTPException(400, f"店铺「{s.name}」没有可以上的商品：范围里的商品这个店铺都已经有了，或者其他店铺已有、"
                                     f"再上会超过店铺商品相同率 {pct}%（系统设置里可以修改）")
        if shared_ids:  # the picked 共享云库 products join 我的云库 (free; each publish costs 积分 unless 上货免积分)
            from .library import add_to_mine
            want = [p.id for p in picked if p.id in shared_ids]
            if want:
                copies = add_to_mine(db, user.tenant_id, want)[1]
                picked = [p if p.id not in shared_ids else p._replace(id=copies[p.id])
                          for p in picked if p.id not in shared_ids or p.id in copies]
        tpl = db.get(PublishTemplate, cfg["publish_template_id"])
        t = PublishTask(tenant_id=user.tenant_id, name=f"云发布-{s.name}-{stamp}", store_id=s.id, store_name=s.name,
                        template_id=tpl.id, template_name=tpl.name, template_snapshot=tpl.config, scope=store_scope[:128],
                        total=len(picked), freight_template_id=cfg["freight_template_id"] or "", address_id=cfg["address_id"] or "")
        db.add(t)
        db.flush()
        db.add_all([PublishTaskItem(task_id=t.id, library_product_id=p.id, platform=p.platform,
                                    source_product_id=p.source_product_id, title=p.title, cover=p.cover)
                    for p in picked])
        created.append(t)
    db.commit()
    return [task_out(t) for t in created]


def _visible(user):
    """The account's own tasks, and those that accounts it assigned its stores to (用户管理 → 分配店铺) created
    on those stores."""
    from sqlalchemy import or_
    own_stores = select(Store.id).where(Store.tenant_id == user.tenant_id)
    return or_(PublishTask.tenant_id == user.tenant_id, PublishTask.store_id.in_(own_stores))


def _creators(db, tasks, user) -> dict[int, str]:
    """Who created the tasks of other accounts: 公司（账号）."""
    from ..models import Tenant
    ids = {t.tenant_id for t in tasks if t.tenant_id != user.tenant_id}
    if not ids:
        return {}
    names = {t.id: t.name for t in db.scalars(select(Tenant).where(Tenant.id.in_(ids)))}
    owners = dict(db.execute(select(User.tenant_id, User.username).where(User.tenant_id.in_(ids), User.role == "owner")
                             .order_by(User.id.desc())).all())
    return {i: f"{names.get(i, '')}（{owners.get(i, '')}）" if owners.get(i) and owners.get(i) != names.get(i)
            else names.get(i, "") or owners.get(i, "") for i in ids}


@router.get("")
def list_tasks(page: int = 1, size: int = 20, store_name: str = "", status: str = "", who: str = "",
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    """who: "" everything visible, mine = created by this account, assigned = by accounts given its stores."""
    q = select(PublishTask).where(_visible(user))
    if who == "mine":
        q = q.where(PublishTask.tenant_id == user.tenant_id)
    elif who == "assigned":
        q = q.where(PublishTask.tenant_id != user.tenant_id)
    if store_name:
        q = q.where(PublishTask.store_name.contains(store_name))
    if status:
        q = q.where(PublishTask.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    off, lim = page_params(page, size)
    rows = db.scalars(q.order_by(PublishTask.id.desc()).offset(off).limit(lim)).all()
    who_names = _creators(db, rows, user)
    assigned = db.scalar(select(func.count()).select_from(
        select(PublishTask.id).where(_visible(user), PublishTask.tenant_id != user.tenant_id).subquery()))
    return {"total": total, "assigned_count": assigned,
            "items": [{**task_out(t), "creator": who_names.get(t.tenant_id, ""), "assigned": t.tenant_id != user.tenant_id}
                      for t in rows]}


def _own_task(db, user, tid) -> PublishTask:
    """A task of this account, or one created on its store by an account the store is assigned to."""
    t = db.get(PublishTask, tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.tenant_id != user.tenant_id:
        s = db.get(Store, t.store_id)
        if not s or s.tenant_id != user.tenant_id:
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


@router.post("/{tid}/cancel")
def cancel_task(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """撤销任务: items not started yet are not published. An item being published right now finishes
    (a WeChat product may already be half created); finished items are not touched."""
    from ..worker import Worker
    t = _own_task(db, user, tid)
    if t.status not in ("pending", "running"):
        raise HTTPException(400, "任务已结束，无需撤销")
    n = db.execute(update(PublishTaskItem).where(PublishTaskItem.task_id == t.id,
                                                 PublishTaskItem.status == "pending")
                   .values(status="cancelled", message="已撤销（未执行）", finished_at=datetime.now())).rowcount
    db.execute(update(PublishTask).where(PublishTask.id == t.id)
               .values(cancelled=func.coalesce(PublishTask.cancelled, 0) + n))
    db.commit()
    Worker.finish_if_done(db, t.id)  # nothing running: the task ends now; otherwise when the last item ends
    db.refresh(t)
    return {"cancelled": n, "task": task_out(t)}


@router.post("/{tid}/resume")
def resume_task(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """恢复执行: put the cancelled items back in the queue."""
    t = _own_task(db, user, tid)
    n = db.execute(update(PublishTaskItem).where(PublishTaskItem.task_id == t.id,
                                                 PublishTaskItem.status == "cancelled")
                   .values(status="pending", message="", worker="", finished_at=None)).rowcount
    if n:
        t.cancelled = max((t.cancelled or 0) - n, 0)
        t.status, t.finished_at = "pending", None
    db.commit()
    return {"resumed": n, "task": task_out(t)}


class DeleteIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/delete")
def delete_tasks(body: DeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """删除历史任务: finished tasks only (waiting / running ones are 撤销ed first). Only the task records go;
    the products already listed in the stores stay."""
    done, busy = [], 0
    for tid in dict.fromkeys(body.ids):
        try:
            t = _own_task(db, user, tid)
        except HTTPException:
            continue
        if t.status in ("pending", "running"):
            busy += 1
            continue
        done.append(t.id)
    if done:
        db.execute(delete(PublishTaskItem).where(PublishTaskItem.task_id.in_(done)))
        db.execute(delete(PublishTask).where(PublishTask.id.in_(done)))
    db.commit()
    return {"deleted": len(done), "busy": busy}


class RetryAllIn(BaseModel):
    store_name: str = ""
    who: str = ""


@router.post("/retry-all-failed")
def retry_all_failed(body: RetryAllIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """一键重试失败: the failed items of every finished task visible here (the list's 店铺 / 创建账号 filters)."""
    q = select(PublishTask).where(_visible(user), PublishTask.status.in_(("success", "partial", "failed")),
                                  PublishTask.failed > 0)
    if body.who == "mine":
        q = q.where(PublishTask.tenant_id == user.tenant_id)
    elif body.who == "assigned":
        q = q.where(PublishTask.tenant_id != user.tenant_id)
    if body.store_name:
        q = q.where(PublishTask.store_name.contains(body.store_name))
    tasks = n = 0
    for t in db.scalars(q).all():
        k = db.execute(update(PublishTaskItem).where(PublishTaskItem.task_id == t.id, PublishTaskItem.status == "failed")
                       .values(status="pending", message="", worker="")).rowcount
        if k:
            t.failed = max((t.failed or 0) - k, 0)
            t.status, t.finished_at = "pending", None
            tasks += 1
            n += k
    db.commit()
    return {"tasks": tasks, "requeued": n}


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
