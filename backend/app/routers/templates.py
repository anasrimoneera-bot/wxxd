from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import PublishTemplate, Store, User
from ..services.template_config import TemplateConfig
from .common import fmt_dt

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    config: TemplateConfig


def out(t: PublishTemplate, full=True) -> dict:
    d = {"id": t.id, "name": t.name, "created_at": fmt_dt(t.created_at)}
    if full:
        d["config"] = TemplateConfig.model_validate(t.config).model_dump()
    return d


def get_tpl(db, user, tid) -> PublishTemplate:
    t = db.get(PublishTemplate, tid)
    if not t or t.tenant_id != user.tenant_id:
        raise HTTPException(404, "上货模板不存在")
    return t


@router.get("/defaults")
def defaults():
    return TemplateConfig().model_dump()


@router.get("")
def list_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(PublishTemplate).where(PublishTemplate.tenant_id == user.tenant_id)
                      .order_by(PublishTemplate.id)).all()
    return [out(t, full=False) for t in rows]


@router.get("/{tid}")
def get_template(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return out(get_tpl(db, user, tid))


@router.post("")
def create_template(body: TemplateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = PublishTemplate(tenant_id=user.tenant_id, name=body.name, config=body.config.model_dump())
    db.add(t)
    db.commit()
    return out(t)


@router.put("/{tid}")
def update_template(tid: int, body: TemplateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = get_tpl(db, user, tid)
    t.name, t.config = body.name, body.config.model_dump()
    db.commit()
    return out(t)


@router.delete("/{tid}")
def delete_template(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = get_tpl(db, user, tid)
    db.execute(update(Store).where(Store.publish_template_id == t.id).values(publish_template_id=None))
    db.delete(t)
    db.commit()
    return {"ok": True}
