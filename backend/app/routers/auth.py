from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owner_user
from ..models import Tenant, User
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
sub_router = APIRouter(prefix="/api/subaccounts", tags=["subaccounts"])


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    company: str = Field(default="", max_length=64)


class LoginIn(BaseModel):
    username: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "active": u.active, "is_admin": u.is_admin, "tenant_id": u.tenant_id, "created_at": u.created_at.strftime("%Y-%m-%d %H:%M:%S")}


def _has_users(db) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


@router.get("/setup")
def setup_status(db: Session = Depends(get_db)):
    """Self-registration is closed: only the very first account (the platform admin) can register;
    every other account is created by the admin in 平台管理 → 用户管理."""
    return {"registration_open": not _has_users(db)}


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if _has_users(db):
        raise HTTPException(403, "不开放注册，请联系管理员开通账号")
    first_user = True
    t = Tenant(name=body.company or body.username)
    db.add(t)
    db.flush()
    u = User(tenant_id=t.id, username=body.username, password_hash=hash_password(body.password),
             display_name=body.username, role="owner", is_admin=first_user)  # first account = platform admin
    db.add(u)
    db.commit()
    return {"token": create_token(u.id, t.id), "user": user_out(u)}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == body.username))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(400, "用户名或密码错误")
    if not u.active:
        raise HTTPException(403, "账号已停用")
    if not u.is_admin:
        reason = db.get(Tenant, u.tenant_id).block_reason()
        if reason:
            raise HTTPException(403, reason)
    return {"token": create_token(u.id, u.tenant_id), "user": user_out(u)}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    return {**user_out(user), "company": t.name,
            "expires_at": t.expires_at.strftime("%Y-%m-%d") if t.expires_at else None}


class SubIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=64)
    display_name: str = ""


class SubUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=6, max_length=64)
    display_name: str | None = None
    active: bool | None = None


@sub_router.get("")
def list_subs(user: User = Depends(owner_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).where(User.tenant_id == user.tenant_id, User.role == "operator")
                      .order_by(User.id)).all()
    return [user_out(u) for u in rows]


@sub_router.post("")
def create_sub(body: SubIn, user: User = Depends(owner_user), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(400, "用户名已存在")
    u = User(tenant_id=user.tenant_id, username=body.username, password_hash=hash_password(body.password),
             display_name=body.display_name or body.username, role="operator")
    db.add(u)
    db.commit()
    return user_out(u)


def _own_sub(db, user, sub_id) -> User:
    u = db.get(User, sub_id)
    if not u or u.tenant_id != user.tenant_id or u.role != "operator":
        raise HTTPException(404, "子账号不存在")
    return u


@sub_router.put("/{sub_id}")
def update_sub(sub_id: int, body: SubUpdate, user: User = Depends(owner_user), db: Session = Depends(get_db)):
    u = _own_sub(db, user, sub_id)
    if body.password:
        u.password_hash = hash_password(body.password)
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.active is not None:
        u.active = body.active
    db.commit()
    return user_out(u)


@sub_router.delete("/{sub_id}")
def delete_sub(sub_id: int, user: User = Depends(owner_user), db: Session = Depends(get_db)):
    db.delete(_own_sub(db, user, sub_id))
    db.commit()
    return {"ok": True}
