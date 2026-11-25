"""积分 for the 共享云库.

Publishing a library product that another account uploaded costs `points_per_publish` 积分 each
time (an account's own uploads are free). The worker reserves the points before publishing and
gives them back when the item fails or is skipped, so only successful publishes are charged.
Admins add points (充值) in 用户管理; every change is written to point_log.
"""
from datetime import datetime

from sqlalchemy import func, select, update

from ..models import LibraryProduct, PointLog, Tenant
from . import settings_store


class NotEnoughPoints(Exception):
    pass


def publish_cost(product: LibraryProduct, tenant_id: int, free: bool = False) -> int:
    """积分 one publish of this product costs the account (0 for its own uploads, and for an account set to
    上货免积分 in 用户管理)."""
    origin = product.origin_tenant_id
    if free or origin is None or origin == tenant_id:
        return 0
    return settings_store.get_int("points_per_publish")


def _log(db, tenant_id: int, change: int, kind: str, note: str, operator: str = "", task_item_id=None) -> PointLog:
    balance = db.scalar(select(Tenant.points).where(Tenant.id == tenant_id)) or 0
    row = PointLog(tenant_id=tenant_id, change=change, balance=balance, kind=kind, note=note[:255],
                   operator=operator[:64], task_item_id=task_item_id)
    db.add(row)
    return row


def reserve(db, tenant_id: int, cost: int, note: str, task_item_id=None) -> PointLog | None:
    """Take `cost` points now (atomically, never below 0). Raises NotEnoughPoints."""
    if cost <= 0:
        return None
    ok = db.execute(update(Tenant).where(Tenant.id == tenant_id, Tenant.points >= cost)
                    .values(points=Tenant.points - cost)).rowcount
    if not ok:
        have = db.scalar(select(Tenant.points).where(Tenant.id == tenant_id)) or 0
        raise NotEnoughPoints(f"积分不足：该商品来自共享云库（其他用户上传），每次上货需 {cost} 积分，"
                              f"当前剩余 {have} 积分，请联系管理员充值")
    row = _log(db, tenant_id, -cost, "publish", note, task_item_id=task_item_id)
    db.commit()
    return row


def refund(db, row: PointLog | None) -> None:
    """The publish did not succeed: give the reserved points back and drop the charge."""
    if row is None:
        return
    db.execute(update(Tenant).where(Tenant.id == row.tenant_id).values(points=Tenant.points - row.change))
    db.delete(row)
    db.commit()


def topup(db, tenant_id: int, change: int, note: str, operator: str) -> PointLog:
    """Admin adds (or with a negative change, removes) points; never below 0."""
    t = db.get(Tenant, tenant_id)
    if change < 0 and (t.points or 0) + change < 0:
        change = -(t.points or 0)
    t.points = (t.points or 0) + change
    db.flush()
    row = _log(db, tenant_id, change, "topup", note or ("管理员充值" if change >= 0 else "管理员扣减"), operator)
    db.commit()
    return row


def uploads_today(db, tenant_id: int) -> int:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(LibraryProduct).where(
        LibraryProduct.tenant_id == tenant_id, LibraryProduct.origin_tenant_id == tenant_id,
        LibraryProduct.imported_at >= start)) or 0


def daily_limit(tenant: Tenant) -> int:
    """New products the account may upload per day (0 = no limit)."""
    if tenant.daily_upload_limit is not None:
        return max(tenant.daily_upload_limit, 0)
    return settings_store.get_int("daily_upload_limit")


def log_out(r: PointLog) -> dict:
    return {"id": r.id, "change": r.change, "balance": r.balance, "kind": r.kind, "note": r.note,
            "operator": r.operator, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
