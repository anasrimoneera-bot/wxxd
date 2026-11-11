"""一键申请类目: WeChat's whole category library (获取所有类目) with each leaf's 保证金
(获取保证金类目规则); apply in one click for every category the store has not opened that
matches the chosen 保证金 amounts."""
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import CategoryApplication, CategoryInfo, Store, User
from ..security import decrypt_secret
from ..services import category_info
from ..services.wechat import explain
from . import stores as stores_router
from .common import fmt_dt

router = APIRouter(prefix="/api/categories", tags=["categories"])
APPLY_PAUSE = 0.3  # seconds between applications


def _store(db, user, store_id: int) -> Store:
    s = db.get(Store, store_id)
    if not s or s.tenant_id != user.tenant_id:
        raise HTTPException(404, "店铺不存在")
    return s


def _client(store: Store):
    return stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))


# ------------------------------------------------------------------ library sync
class SyncIn(BaseModel):
    store_id: int  # whose API credentials are used to read the (shared) library
    full: bool = False  # re-read every 保证金, not only missing / older than 7 days


@router.post("/catalog/sync")
def catalog_sync(body: SyncIn, background: BackgroundTasks, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    client = _client(_store(db, user, body.store_id))
    if not category_info.start(db):
        raise HTTPException(400, "类目库正在同步中")
    background.add_task(category_info.run_sync, client, body.full)
    return category_info.status(db)


@router.get("/catalog/status")
def catalog_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return category_info.status(db)


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Every leaf category with its 保证金 / 资质 and this store's state (opened / applied)."""
    store = _store(db, user, store_id)
    opened: set[str] = set()
    opened_error = ""
    try:
        opened = {str(c) for c in _client(store).account_categories()}
    except Exception as e:
        opened_error = f"读取店铺已开通类目失败：{explain(e)}"
    apps = {}
    for a in db.scalars(select(CategoryApplication).where(CategoryApplication.store_id == store.id)
                        .order_by(CategoryApplication.id)):
        apps[a.cat_id] = a  # latest wins
    rows = []
    for c in db.scalars(select(CategoryInfo).where(CategoryInfo.leaf.is_(True)).order_by(CategoryInfo.path)):
        a = apps.get(c.cat_id)
        rows.append({"cat_id": c.cat_id, "name": c.name, "path_names": c.path_names, "deposit": c.deposit,
                     "deposit_source": c.deposit_source, "needs_qualification": c.needs_qualification,
                     "qua_tips": c.qua_tips, "opened": c.cat_id in opened,
                     "application": {"ok": a.ok, "audit_id": a.audit_id, "message": a.message,
                                     "created_at": fmt_dt(a.created_at)} if a else None})
    return {"items": rows, "opened_error": opened_error, "opened_count": len(opened),
            "sync": category_info.status(db)}


@router.get("/raw")
def raw(store_id: int, cat_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """What WeChat returns for one category (保证金规则 + 类目信息), to check the 保证金 reading."""
    client = _client(_store(db, user, store_id))
    out = {}
    for key, call in (("getcategoryrule", client.category_deposit_rule), ("category_detail", client.category_detail)):
        try:
            out[key] = call(cat_id)
        except Exception as e:
            out[key] = {"error": explain(e)}
    out["deposit_read"] = category_info.deposit_of(client, cat_id)[0]
    return out


# ------------------------------------------------------------------ apply
class ApplyIn(BaseModel):
    store_id: int
    cat_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/apply")
def apply(body: ApplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """一键申请: submit an application for each category (path from the library; no documents)."""
    store = _store(db, user, body.store_id)
    client = _client(store)
    results = []
    for i, cid in enumerate(dict.fromkeys(body.cat_ids)):
        c = db.get(CategoryInfo, cid)
        if not c or not c.path:
            results.append({"cat_id": cid, "ok": False, "audit_id": "", "message": "类目库里没有这个类目，请先同步类目库"})
            continue
        if i:
            time.sleep(APPLY_PAUSE)
        try:
            audit = client.apply_category(c.path.split("/"))
            a = CategoryApplication(tenant_id=user.tenant_id, store_id=store.id, cat_id=cid, ok=True,
                                    audit_id=str(audit or "")[:128], message="已提交，等待微信审核")
        except Exception as e:
            a = CategoryApplication(tenant_id=user.tenant_id, store_id=store.id, cat_id=cid, ok=False,
                                    message=explain(e)[:500])
        db.add(a)
        db.commit()
        results.append({"cat_id": cid, "ok": a.ok, "audit_id": a.audit_id, "message": a.message})
    return {"results": results}
