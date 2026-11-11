"""一键申请类目: find the categories the 商品云库 products need but the store has not opened,
filter by 保证金 range, apply for the chosen ones in one go."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import CategoryApplication, CategoryInfo, LibraryProduct, Store, User
from ..security import decrypt_secret
from ..services import category_info
from ..services.wechat import explain
from . import stores as stores_router
from .common import fmt_dt

router = APIRouter(prefix="/api/categories", tags=["categories"])


def _store(db, user, store_id: int) -> Store:
    s = db.get(Store, store_id)
    if not s or s.tenant_id != user.tenant_id:
        raise HTTPException(404, "店铺不存在")
    return s


def _client(store: Store):
    return stores_router.client_factory(store.appid, decrypt_secret(store.secret_enc))


@router.get("/needed")
def needed(store_id: int, group_id: int | None = None, platform: str = "", user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    """Leaf categories used by 商品云库 products, with the store's state for each."""
    store = _store(db, user, store_id)
    q = select(LibraryProduct).where(LibraryProduct.tenant_id == user.tenant_id)
    if group_id:
        q = q.where(LibraryProduct.group_id == group_id)
    if platform:
        q = q.where(LibraryProduct.platform == platform)
    cats: dict[str, dict] = {}
    for p in db.scalars(q):
        path = [str(c) for c in (p.normalized or {}).get("cats") or []]
        if not path:
            continue
        row = cats.setdefault(path[-1], {"cat_id": path[-1], "path": path, "products": 0, "sample": p.title})
        row["products"] += 1
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
    infos = {c.cat_id: c for c in db.scalars(select(CategoryInfo).where(CategoryInfo.cat_id.in_(list(cats) or ["-"])))}
    rows = []
    for cid, r in cats.items():
        a = apps.get(cid)
        rows.append({**r, "opened": cid in opened, "info": category_info.info_out(infos.get(cid)),
                     "application": {"ok": a.ok, "audit_id": a.audit_id, "message": a.message,
                                     "created_at": fmt_dt(a.created_at)} if a else None})
    rows.sort(key=lambda r: (r["opened"], -r["products"]))
    return {"items": rows, "opened_error": opened_error}


class DetailsIn(BaseModel):
    store_id: int
    cat_ids: list[str] = Field(min_length=1, max_length=20)
    refresh: bool = False


@router.post("/details")
def details(body: DetailsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Name, 保证金 and 资质 for up to 20 categories (cached 7 days). The page asks in small batches."""
    store = _store(db, user, body.store_id)
    return category_info.get_infos(db, _client(store), body.cat_ids, body.refresh)


class ApplyItem(BaseModel):
    cat_id: str
    path: list[str] = Field(min_length=1, max_length=6)


class ApplyIn(BaseModel):
    store_id: int
    items: list[ApplyItem] = Field(min_length=1, max_length=100)


@router.post("/apply")
def apply(body: ApplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """一键申请: submit an application for each chosen category (no qualification documents)."""
    store = _store(db, user, body.store_id)
    client = _client(store)
    results = []
    for it in body.items:
        if it.path[-1] != it.cat_id:
            raise HTTPException(400, "类目路径不正确")
        try:
            audit = client.apply_category(it.path)
            a = CategoryApplication(tenant_id=user.tenant_id, store_id=store.id, cat_id=it.cat_id, ok=True,
                                    audit_id=str(audit or "")[:128], message="已提交，等待微信审核")
        except Exception as e:
            a = CategoryApplication(tenant_id=user.tenant_id, store_id=store.id, cat_id=it.cat_id, ok=False,
                                    message=explain(e)[:500])
        db.add(a)
        db.commit()
        results.append({"cat_id": it.cat_id, "ok": a.ok, "audit_id": a.audit_id, "message": a.message})
    return {"results": results}
