from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import admin_user
from ..models import User
from ..services import settings_store
from ..services.titles import rewrite_with_deepseek

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SettingsIn(BaseModel):
    deepseek_api_key: str | None = Field(default=None, max_length=256)  # None = keep current
    clear_deepseek_api_key: bool = False
    deepseek_base_url: str | None = Field(default=None, max_length=256)  # None = keep current
    deepseek_model: str | None = Field(default=None, max_length=64)
    gemini_api_key: str | None = Field(default=None, max_length=256)  # None = keep current
    clear_gemini_api_key: bool = False
    gemini_model: str | None = Field(default=None, max_length=64)
    points_per_publish: int | None = Field(default=None, ge=0, le=100000)  # None = keep current
    daily_upload_limit: int | None = Field(default=None, ge=0, le=1000000)  # 0 = no limit


class TestIn(BaseModel):
    title: str = Field(default="伸缩款汽车前挡风玻璃除雾刷后视镜刮水清洁多功能清洁刷K", max_length=200)


def settings_out() -> dict:
    key = settings_store.get("deepseek_api_key")
    return {
        "deepseek_api_key_set": bool(key),
        "deepseek_api_key_masked": settings_store.mask(key),
        "deepseek_base_url": settings_store.get("deepseek_base_url"),
        "deepseek_model": settings_store.get("deepseek_model"),
        "gemini_api_key_set": bool(settings_store.get("gemini_api_key")),
        "gemini_api_key_masked": settings_store.mask(settings_store.get("gemini_api_key")),
        "gemini_model": settings_store.get("gemini_model"),
        "points_per_publish": settings_store.get_int("points_per_publish"),
        "daily_upload_limit": settings_store.get_int("daily_upload_limit"),
    }


@router.get("/settings")
def get_settings_(_: User = Depends(admin_user)):
    return settings_out()


@router.put("/settings")
def put_settings(body: SettingsIn, _: User = Depends(admin_user)):
    """Only the fields sent are changed (each section of 系统设置 saves its own fields)."""
    values = {}
    if body.deepseek_base_url is not None:
        values["deepseek_base_url"] = body.deepseek_base_url.strip() or "https://api.deepseek.com"
    if body.deepseek_model is not None:
        values["deepseek_model"] = body.deepseek_model.strip() or "deepseek-flash"
    if body.clear_gemini_api_key:
        values["gemini_api_key"] = ""
    elif body.gemini_api_key:
        values["gemini_api_key"] = body.gemini_api_key.strip()
    if body.gemini_model:
        values["gemini_model"] = body.gemini_model.strip()
    for k in ("points_per_publish", "daily_upload_limit"):
        if getattr(body, k) is not None:
            values[k] = str(getattr(body, k))
    if body.clear_deepseek_api_key:
        values["deepseek_api_key"] = ""
    elif body.deepseek_api_key:
        values["deepseek_api_key"] = body.deepseek_api_key.strip()
    settings_store.set_many(values)
    return settings_out()


@router.post("/settings/test-deepseek")
def test_deepseek(body: TestIn, _: User = Depends(admin_user)):
    try:
        new = rewrite_with_deepseek(body.title, "", 30, raise_errors=True)
    except Exception as e:
        raise HTTPException(400, f"调用失败：{e}")
    return {"source": body.title, "rewritten": new}


@router.post("/settings/test-gemini")
def test_gemini(_: User = Depends(admin_user)):
    """One real check: a 1688 sample image against itself (should be 同款)."""
    from ..services import vision
    try:
        r = vision.compare(TEST_1688_IMAGE, "测试商品", [{"key": "1", "image_url": TEST_1688_IMAGE, "title": "测试商品"}])
    except Exception as e:
        raise HTTPException(400, f"调用失败：{e}")
    return {"result": r[0]}


TEST_1688_IMAGE = "https://cbu01.alicdn.com/img/ibank/O1CN01YZTAXwRx8CF1chua_!!2223051725283-0-cib.jpg"


# ---- customers (tenants) -----------------------------------------------------
from datetime import datetime, timedelta  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from ..db import get_db  # noqa: E402
from ..models import LibraryProduct, PublishTask, Store, Tenant  # noqa: E402
from ..security import hash_password  # noqa: E402


def _counts(db: Session, model) -> dict[int, int]:
    return dict(db.execute(select(model.tenant_id, func.count()).group_by(model.tenant_id)).all())


def _status(t: Tenant) -> str:
    if not t.active:
        return "disabled"
    if t.expires_at and t.expires_at < datetime.now():
        return "expired"
    return "active"


def tenant_out(t: Tenant, owner: User | None, c: dict) -> dict:
    days_left = None
    if t.expires_at:
        days_left = (t.expires_at.date() - datetime.now().date()).days
    return {
        "id": t.id, "company": t.name, "note": t.note, "active": t.active,
        "expires_at": t.expires_at.strftime("%Y-%m-%d") if t.expires_at else None,
        "days_left": days_left, "status": _status(t),
        "owner_id": owner.id if owner else None, "username": owner.username if owner else "",
        "is_admin": bool(owner and owner.is_admin),
        "stores": c["stores"].get(t.id, 0) + c["shared"].get(t.id, 0), "shared_stores": c["shared"].get(t.id, 0),
        "products": c["products"].get(t.id, 0),
        "tasks": c["tasks"].get(t.id, 0), "subaccounts": c["subs"].get(t.id, 0),
        "points": t.points or 0, "daily_upload_limit": t.daily_upload_limit,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def _all_counts(db: Session) -> dict:
    subs = dict(db.execute(select(User.tenant_id, func.count()).where(User.role == "operator")
                           .group_by(User.tenant_id)).all())
    from ..models import StoreAccess
    return {"stores": _counts(db, Store), "products": _counts(db, LibraryProduct),
            "tasks": _counts(db, PublishTask), "subs": subs, "shared": _counts(db, StoreAccess)}


def _owner(db: Session, tenant_id: int) -> User | None:
    return db.scalar(select(User).where(User.tenant_id == tenant_id, User.role == "owner").order_by(User.id).limit(1))


def _get_tenant(db: Session, tid: int) -> Tenant:
    t = db.get(Tenant, tid)
    if not t:
        raise HTTPException(404, "客户不存在")
    return t


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")


class TenantCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    company: str = Field(default="", max_length=64)
    months: int = Field(default=1, ge=0, le=120)  # 0 = no expiry
    note: str = Field(default="", max_length=255)


class TenantUpdate(BaseModel):
    company: str | None = Field(default=None, max_length=64)
    active: bool | None = None
    expires_at: str | None = None  # "YYYY-MM-DD"; "" = no expiry; omitted = unchanged
    note: str | None = Field(default=None, max_length=255)
    daily_upload_limit: int | None = Field(default=None, ge=0, le=1000000)  # 0 = no limit
    use_default_upload_limit: bool = False  # back to the 系统设置 default


class ExtendIn(BaseModel):
    months: int = Field(ge=1, le=120)


class PasswordIn(BaseModel):
    password: str = Field(min_length=6, max_length=64)


@router.get("/tenants")
def list_tenants(q: str = "", status: str = "", _: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Tenant).order_by(Tenant.id.desc())).all()
    c = _all_counts(db)
    out = [tenant_out(t, _owner(db, t.id), c) for t in rows]
    if q:
        out = [r for r in out if q.lower() in (r["company"] + r["username"] + r["note"]).lower()]
    if status:
        out = [r for r in out if r["status"] == status]
    return out


@router.post("/tenants")
def create_tenant(body: TenantCreate, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, "用户名已存在")
    expires = None
    if body.months:
        expires = (datetime.now() + timedelta(days=30 * body.months)).replace(hour=23, minute=59, second=59)
    t = Tenant(name=body.company or body.username, expires_at=expires, note=body.note)
    db.add(t)
    db.flush()
    u = User(tenant_id=t.id, username=body.username, password_hash=hash_password(body.password),
             display_name=body.company or body.username, role="owner")
    db.add(u)
    db.commit()
    return tenant_out(t, u, _all_counts(db))


@router.put("/tenants/{tid}")
def update_tenant(tid: int, body: TenantUpdate, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = _get_tenant(db, tid)
    if body.active is False and t.id == admin.tenant_id:
        raise HTTPException(400, "不能停用管理员自己的账号")
    if body.company is not None:
        t.name = body.company or t.name
    if body.active is not None:
        t.active = body.active
    if body.expires_at is not None:
        t.expires_at = _parse_date(body.expires_at)
    if body.note is not None:
        t.note = body.note
    if body.use_default_upload_limit:
        t.daily_upload_limit = None
    elif body.daily_upload_limit is not None:
        t.daily_upload_limit = body.daily_upload_limit
    db.commit()
    return tenant_out(t, _owner(db, t.id), _all_counts(db))


@router.post("/tenants/{tid}/extend")
def extend_tenant(tid: int, body: ExtendIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = _get_tenant(db, tid)
    start = max(t.expires_at or datetime.now(), datetime.now())
    t.expires_at = (start + timedelta(days=30 * body.months)).replace(hour=23, minute=59, second=59)
    t.active = True
    db.commit()
    return tenant_out(t, _owner(db, t.id), _all_counts(db))


@router.post("/tenants/{tid}/reset-password")
def reset_password(tid: int, body: PasswordIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    owner = _owner(db, _get_tenant(db, tid).id)
    if not owner:
        raise HTTPException(404, "该客户没有主账号")
    owner.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True, "username": owner.username}


# ---- 积分 -----------------------------------------------------------------------
class PointsIn(BaseModel):
    change: int = Field(ge=-100000000, le=100000000)  # + 充值 / - 扣减
    note: str = Field(default="", max_length=255)


@router.post("/tenants/{tid}/points")
def topup_points(tid: int, body: PointsIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    from ..services import points
    t = _get_tenant(db, tid)
    if not body.change:
        raise HTTPException(400, "请输入要增加或扣减的积分")
    points.topup(db, t.id, body.change, body.note.strip(), admin.username)
    return tenant_out(t, _owner(db, t.id), _all_counts(db))


@router.get("/tenants/{tid}/points/logs")
def tenant_point_logs(tid: int, page: int = 1, size: int = 20, _: User = Depends(admin_user),
                      db: Session = Depends(get_db)):
    from ..models import PointLog
    from ..services import points
    from .common import page_params
    _get_tenant(db, tid)
    q = select(PointLog).where(PointLog.tenant_id == tid)
    off, lim = page_params(page, size)
    return {"total": db.scalar(select(func.count()).select_from(q.subquery())),
            "items": [points.log_out(r) for r in db.scalars(q.order_by(PointLog.id.desc()).offset(off).limit(lim))]}


# ---- 分配店铺 (StoreAccess) ------------------------------------------------------
def _store_row(db, st, owners: dict) -> dict:
    return {"id": st.id, "name": st.name or st.appid, "appid": st.appid, "logo": st.logo,
            "owner_id": st.tenant_id, "owner_name": owners.get(st.tenant_id, "")}


@router.get("/tenants/{tid}/stores")
def tenant_stores(tid: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    """A customer's own stores, the stores assigned to it, and every other store that can be assigned."""
    from ..models import StoreAccess
    _get_tenant(db, tid)
    owners = dict(db.execute(select(Tenant.id, Tenant.name)).all())
    all_stores = db.scalars(select(Store).order_by(Store.tenant_id, Store.id)).all()
    shared = {a.store_id: a for a in db.scalars(select(StoreAccess).where(StoreAccess.tenant_id == tid))}
    own = [_store_row(db, st, owners) for st in all_stores if st.tenant_id == tid]
    assigned = [{**_store_row(db, st, owners), "granted_by": shared[st.id].granted_by,
                 "granted_at": shared[st.id].created_at.strftime("%Y-%m-%d %H:%M")}
                for st in all_stores if st.id in shared]
    available = [_store_row(db, st, owners) for st in all_stores if st.tenant_id != tid and st.id not in shared]
    return {"own": own, "assigned": assigned, "available": available}


class AssignIn(BaseModel):
    store_ids: list[int] = Field(min_length=1)


@router.post("/tenants/{tid}/stores")
def assign_stores(tid: int, body: AssignIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """分配店铺: the customer may then use these stores (商品管理, 订单管理, 发货, 上货) like its own."""
    from ..models import StoreAccess
    _get_tenant(db, tid)
    n = 0
    for sid in dict.fromkeys(body.store_ids):
        st = db.get(Store, sid)
        if not st:
            raise HTTPException(404, "店铺不存在")
        if st.tenant_id == tid or db.scalar(select(StoreAccess).where(StoreAccess.store_id == sid,
                                                                       StoreAccess.tenant_id == tid)):
            continue
        db.add(StoreAccess(store_id=sid, tenant_id=tid, granted_by=admin.username))
        n += 1
    db.commit()
    return {"added": n, **tenant_stores(tid, admin, db)}


@router.delete("/tenants/{tid}/stores/{sid}")
def unassign_store(tid: int, sid: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """取消分配: the customer no longer sees the store; the store and its data stay with its owner."""
    from sqlalchemy import delete as sql_delete

    from ..models import StoreAccess
    db.execute(sql_delete(StoreAccess).where(StoreAccess.store_id == sid, StoreAccess.tenant_id == tid))
    db.commit()
    return tenant_stores(tid, admin, db)
