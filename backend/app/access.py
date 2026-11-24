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


CONFIG_FIELDS = ("freight_template_id", "freight_template_name", "address_id", "address_name", "publish_template_id")


def access_row(db, tenant_id: int, store: Store) -> StoreAccess | None:
    if store.tenant_id == tenant_id:
        return None
    return db.scalar(select(StoreAccess).where(StoreAccess.store_id == store.id, StoreAccess.tenant_id == tenant_id))


def config(db, tenant_id: int, store: Store) -> dict:
    """发布配置 the account publishes with: the store's own, or, for an assigned store, what the account set
    for itself (each field it left empty falls back to the owner's)."""
    out = {f: getattr(store, f) for f in CONFIG_FIELDS}
    a = access_row(db, tenant_id, store)
    if a is not None:
        if a.freight_template_id:
            out.update(freight_template_id=a.freight_template_id, freight_template_name=a.freight_template_name)
        if a.address_id:
            out.update(address_id=a.address_id, address_name=a.address_name)
        if a.publish_template_id:
            out["publish_template_id"] = a.publish_template_id
    return out


def get_store(db, user, store_id: int, owner_only: bool = False) -> Store:
    """The store if the account may use it (owner_only: settings, AppSecret, delete); else 404/403."""
    s = db.get(Store, store_id)
    if not can_use(db, user.tenant_id, s):
        raise HTTPException(404, "店铺不存在")
    if owner_only and s.tenant_id != user.tenant_id:
        raise HTTPException(403, "这个店铺是分配给你使用的，店铺设置只能由店铺所属账号修改")
    return s
