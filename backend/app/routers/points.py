"""积分 and 共享云库 quota of the current account."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import PointLog, Tenant, User
from ..services import points, settings_store
from .common import page_params

router = APIRouter(prefix="/api/points", tags=["points"])


@router.get("/me")
def my_points(user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Tenant, user.tenant_id)
    return {"points": t.points or 0, "free_publish": bool(t.free_publish),
            "cost_per_publish": 0 if t.free_publish else settings_store.get_int("points_per_publish"),
            "daily_upload_limit": 0 if user.is_admin else points.daily_limit(t), "uploaded_today": points.uploads_today(db, t.id)}


@router.get("/logs")
def my_logs(page: int = 1, size: int = 20, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(PointLog).where(PointLog.tenant_id == user.tenant_id)
    off, lim = page_params(page, size)
    return {"total": db.scalar(select(func.count()).select_from(q.subquery())),
            "items": [points.log_out(r) for r in db.scalars(q.order_by(PointLog.id.desc()).offset(off).limit(lim))]}
