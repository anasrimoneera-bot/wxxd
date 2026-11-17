from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import PublishTemplate, Store, User
from ..security import decrypt_secret, encrypt_secret
from ..services.wechat import WeChatClient, explain

router = APIRouter(prefix="/api/stores", tags=["stores"])

# Tests replace this to avoid real network calls.
client_factory = WeChatClient


def store_out(s: Store, templates: dict[int, str]) -> dict:
    configured = bool(s.publish_template_id and s.freight_template_id and s.address_id)
    return {
        "id": s.id, "name": s.name, "logo": s.logo, "appid": s.appid, "wx_shop_id": s.wx_shop_id,
        "open_status": s.open_status, "freight_template_id": s.freight_template_id,
        "freight_template_name": s.freight_template_name, "address_id": s.address_id,
        "address_name": s.address_name, "publish_template_id": s.publish_template_id,
        "publish_template_name": templates.get(s.publish_template_id or 0, ""),
        "config_status": "configured" if configured else "pending", "last_error": s.last_error,
    }


def _templates(db, tenant_id) -> dict[int, str]:
    return dict(db.execute(select(PublishTemplate.id, PublishTemplate.name)
                           .where(PublishTemplate.tenant_id == tenant_id)).all())


def get_store(db: Session, user: User, store_id: int, owner_only: bool = False) -> Store:
    """A store the account may use (its own or assigned in 用户管理); owner_only for settings / delete."""
    from .. import access
    return access.get_store(db, user, store_id, owner_only)


def _all_templates(db, user, stores) -> dict[int, str]:
    """Template names for these stores: the account's own, plus those of assigned stores' owners."""
    names = _templates(db, user.tenant_id)
    ids = {s.publish_template_id for s in stores if s.publish_template_id and s.publish_template_id not in names}
    if ids:
        names.update(dict(db.execute(select(PublishTemplate.id, PublishTemplate.name)
                                     .where(PublishTemplate.id.in_(ids))).all()))
    return names


def _shared_out(db, user, s: Store, tpl) -> dict:
    out = store_out(s, tpl)
    if s.tenant_id != user.tenant_id:
        from ..models import Tenant
        owner = db.get(Tenant, s.tenant_id)
        out.update(shared=True, owner_name=owner.name if owner else "")
    else:
        out.update(shared=False, owner_name="")
    return out


def _sync_info(s: Store, client) -> None:
    info = client.shop_info()
    s.name = info.get("nickname") or s.name or s.appid
    s.logo = info.get("headimg_url") or s.logo
    s.wx_shop_id = info.get("username") or s.wx_shop_id
    s.open_status = str(info.get("status") or s.open_status)
    s.last_error = ""


class StoreIn(BaseModel):
    appid: str = Field(min_length=6, max_length=64)
    secret: str = Field(min_length=6, max_length=128)
    name: str = ""


class StoreConfig(BaseModel):
    freight_template_id: str | None = None
    freight_template_name: str | None = None
    address_id: str | None = None
    address_name: str | None = None
    publish_template_id: int | None = None


class BulkConfig(StoreConfig):
    store_ids: list[int]


@router.get("")
def list_stores(name: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .. import access
    q = select(Store).where(Store.id.in_(access.store_ids(user.tenant_id)))
    if name:
        q = q.where(Store.name.contains(name))
    rows = db.scalars(q.order_by(Store.id)).all()
    tpl = _all_templates(db, user, rows)
    return [_shared_out(db, user, s, tpl) for s in rows]


@router.post("")
def add_store(body: StoreIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    appid = body.appid.strip()
    if db.scalar(select(Store).where(Store.tenant_id == user.tenant_id, Store.appid == appid)):
        raise HTTPException(400, "该 AppID 已添加")
    s = Store(tenant_id=user.tenant_id, appid=appid, secret_enc=encrypt_secret(body.secret.strip()),
              name=body.name.strip())
    try:
        _sync_info(s, client_factory(appid, body.secret.strip()))
    except Exception as e:
        raise HTTPException(400, f"连接失败：{explain(e)}")
    db.add(s)
    db.commit()
    return _shared_out(db, user, s, _all_templates(db, user, [s]))


@router.post("/{store_id}/refresh")
def refresh_store(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_store(db, user, store_id)
    try:
        _sync_info(s, client_factory(s.appid, decrypt_secret(s.secret_enc)))
    except Exception as e:
        s.last_error = explain(e)[:500]
    db.commit()
    return _shared_out(db, user, s, _all_templates(db, user, [s]))


@router.delete("/{store_id}")
def delete_store(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from sqlalchemy import delete as sql_delete

    from ..models import StoreProduct
    from ..models import StoreAccess
    s = get_store(db, user, store_id, owner_only=True)
    db.execute(sql_delete(StoreProduct).where(StoreProduct.store_id == store_id))
    db.execute(sql_delete(StoreAccess).where(StoreAccess.store_id == store_id))
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/{store_id}/options")
def store_options(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Freight templates and return addresses read live from the store."""
    s = get_store(db, user, store_id)
    c = client_factory(s.appid, decrypt_secret(s.secret_enc))
    out = {"freight_templates": [], "addresses": [], "errors": []}
    for key, fn in (("freight_templates", c.freight_templates), ("addresses", c.addresses)):
        try:
            out[key] = fn()
        except Exception as e:
            out["errors"].append(explain(e))
    return out


def _apply(s: Store, body: StoreConfig, db, user):
    if body.publish_template_id is not None:
        t = db.get(PublishTemplate, body.publish_template_id)
        if not t or t.tenant_id != user.tenant_id:
            raise HTTPException(400, "上货模板不存在")
    for k, v in body.model_dump(exclude={"store_ids"}).items():
        if v is not None:
            setattr(s, k, v)


@router.put("/{store_id}/config")
def config_store(store_id: int, body: StoreConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_store(db, user, store_id, owner_only=True)
    _apply(s, body, db, user)
    db.commit()
    return _shared_out(db, user, s, _all_templates(db, user, [s]))


@router.post("/bulk-config")
def bulk_config(body: BulkConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for sid in body.store_ids:
        _apply(get_store(db, user, sid, owner_only=True), body, db, user)
    db.commit()
    return {"ok": True, "count": len(body.store_ids)}
