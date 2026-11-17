"""Which stores an account may use: its own, plus those assigned to it in 用户管理 (StoreAccess).
Store data (store products, orders, 货源 of store products) belongs to the store, so every account
with access sees and changes the same data."""
from fastapi import HTTPException
from sqlalchemy import or_, select

from .models import Store, StoreAccess


def store_ids(tenant_id: int):
    """SELECT of the ids of the stores the account may use (for .in_())."""
    return select(Store.id).where(or_(
        Store.tenant_id == tenant_id,
        Store.id.in_(select(StoreAccess.store_id).where(StoreAccess.tenant_id == tenant_id))))


def stores(db, tenant_id: int) -> dict[int, Store]:
    return {s.id: s for s in db.scalars(select(Store).where(Store.id.in_(store_ids(tenant_id))).order_by(Store.id))}


def can_use(db, tenant_id: int, store: Store | None) -> bool:
    if store is None:
        return False
    if store.tenant_id == tenant_id:
        return True
    return db.scalar(select(StoreAccess.id).where(StoreAccess.store_id == store.id,
                                                  StoreAccess.tenant_id == tenant_id)) is not None


def get_store(db, user, store_id: int, owner_only: bool = False) -> Store:
    """The store if the account may use it (owner_only: settings, AppSecret, delete); else 404/403."""
    s = db.get(Store, store_id)
    if not can_use(db, user.tenant_id, s):
        raise HTTPException(404, "店铺不存在")
    if owner_only and s.tenant_id != user.tenant_id:
        raise HTTPException(403, "这个店铺是分配给你使用的，店铺设置只能由店铺所属账号修改")
    return s
