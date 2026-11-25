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


def store_out(s: Store, templates: dict[int, str], cfg: dict | None = None) -> dict:
    c = cfg or {f: getattr(s, f) for f in ("freight_template_id", "freight_template_name", "address_id",
                                           "address_name", "publish_template_id")}
    configured = bool(c["publish_template_id"] and c["freight_template_id"] and c["address_id"])
    return {
        "id": s.id, "name": s.name, "logo": s.logo, "appid": s.appid, "wx_shop_id": s.wx_shop_id,
        "open_status": s.open_status, **c,
        "publish_template_name": templates.get(c["publish_template_id"] or 0, ""),
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
    from .. import access
    names = _templates(db, user.tenant_id)
    ids = {access.config(db, user.tenant_id, s)["publish_template_id"] for s in stores}
    ids = {i for i in ids if i and i not in names}
    if ids:
        names.update(dict(db.execute(select(PublishTemplate.id, PublishTemplate.name)
                                     .where(PublishTemplate.id.in_(ids))).all()))
    return names


def _shared_out(db, user, s: Store, tpl) -> dict:
    from .. import access
    out = store_out(s, tpl, access.config(db, user.tenant_id, s))
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
    """发布配置: on the store for its owner. An assigned account uses the owner's 运费模板 / 退货地址 and only
    sets its own 上货模板."""
    from .. import access
    if body.publish_template_id is not None:
        t = db.get(PublishTemplate, body.publish_template_id)
        if not t or t.tenant_id != user.tenant_id:
            raise HTTPException(400, "上货模板不存在")
    a = access.access_row(db, user.tenant_id, s)
    if a is not None:
        if body.publish_template_id is not None:
            a.publish_template_id = body.publish_template_id
        return
    for k, v in body.model_dump(exclude={"store_ids"}).items():
        if v is not None:
            setattr(s, k, v)


# ---- 运费模板: create / edit the store's freight templates (WeChat 增加 / 更新运费模版) ---------------------
PROVINCES = ["北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省", "黑龙江省", "上海市", "江苏省",
             "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区",
             "海南省", "重庆市", "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省", "青海省",
             "宁夏回族自治区", "新疆维吾尔自治区"]
SEND_TIMES = {"SendTime_TWENTYFOUR_HOUR": "24小时内发货", "SendTime_FOUTYEIGHT_HOUR": "48小时内发货",
              "SendTime_SHIP_TODAY": "今日发"}


class FreightIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    valuation_type: str = Field(default="PIECE", pattern="^(PIECE|WEIGHT)$")  # 按件数 / 按重量
    send_time: str = Field(default="SendTime_FOUTYEIGHT_HOUR")
    address_id: str = ""  # 发货地址: one of the store's addresses ("" on edit = unchanged)
    shipping_method: str = Field(default="FREE", pattern="^(FREE|CONDITION_FREE|NO_FREE)$")  # 包邮 / 条件包邮 / 不包邮
    # 默认运费 (不包邮 / 条件包邮 when the condition is not met): first N 件/kg for ¥, each further M 件/kg ¥
    first_val_amount: int = Field(default=1, ge=1, le=9999)
    first_price: float = Field(default=0, ge=0, le=999)
    second_val_amount: int = Field(default=1, ge=1, le=9999)
    second_price: float = Field(default=0, ge=0, le=999)
    # 条件包邮: 满 N 件 or 满 ¥ (one of them)
    condition: str = Field(default="piece", pattern="^(piece|amount)$")
    min_piece: int = Field(default=2, ge=1, le=9999)
    min_amount: float = Field(default=0, ge=0, le=99999)
    not_send_provinces: list[str] = Field(default_factory=list, max_length=30)  # 不发货地区
    is_default: bool = False


def _freight_payload(body: FreightIn, old: dict | None, address_info: dict | None) -> dict:
    if body.send_time not in SEND_TIMES:
        raise HTTPException(400, "发货时间不正确")
    bad = [p for p in body.not_send_provinces if p not in PROVINCES]
    if bad:
        raise HTTPException(400, f"不认识的地区：{'、'.join(bad)}")
    if len(set(body.not_send_provinces)) >= len(PROVINCES):
        raise HTTPException(400, "不能设置全国不发货")
    tpl = {k: v for k, v in (old or {}).items() if k not in ("create_time", "update_time")}
    tpl.update(name=body.name.strip(), valuation_type=body.valuation_type, send_time=body.send_time,
               delivery_type="EXPRESS", shipping_method=body.shipping_method, is_default=body.is_default)
    if address_info:
        tpl["address_info"] = address_info
    if not tpl.get("address_info"):
        raise HTTPException(400, "请选择发货地址")
    if body.shipping_method == "FREE":
        tpl.pop("all_freight_calc_method", None)
        tpl.pop("all_condition_free_detail", None)
    else:
        default = {"is_default": True, "first_val_amount": body.first_val_amount, "first_price": round(body.first_price * 100),
                   "second_val_amount": body.second_val_amount, "second_price": round(body.second_price * 100)}
        others = [m for m in ((old or {}).get("all_freight_calc_method") or {}).get("freight_calc_method_list") or []
                  if not m.get("is_default")]  # 指定地区运费 set in the 小店后台 stay
        tpl["all_freight_calc_method"] = {"freight_calc_method_list": [default, *others]}
        if body.shipping_method == "CONDITION_FREE":
            send = [{"province_name": p} for p in PROVINCES if p not in body.not_send_provinces]
            piece = body.condition == "piece"
            if not piece and body.min_amount <= 0:
                raise HTTPException(400, "请填写满多少元包邮")
            tpl["all_condition_free_detail"] = {"condition_free_detail_list": [{
                "address_infos": send, "min_piece": body.min_piece if piece else 0, "min_weight": 0,
                "min_amount": 0 if piece else round(body.min_amount * 100),
                "valuation_flag": 1 if piece else 0, "amount_flag": 0 if piece else 1}]}
        else:
            tpl.pop("all_condition_free_detail", None)
    tpl["not_send_area"] = {"address_infos": [{"province_name": p} for p in dict.fromkeys(body.not_send_provinces)]}
    return tpl


def _freight_form(d: dict) -> dict:
    """A WeChat 运费模板 as the form's fields."""
    calc = ((d.get("all_freight_calc_method") or {}).get("freight_calc_method_list") or [])
    default = next((m for m in calc if m.get("is_default")), calc[0] if calc else {})
    cond = ((d.get("all_condition_free_detail") or {}).get("condition_free_detail_list") or [{}])[0]
    addr = d.get("address_info") or {}
    return {
        "name": d.get("name", ""), "valuation_type": d.get("valuation_type") or "PIECE",
        "send_time": d.get("send_time") or "SendTime_FOUTYEIGHT_HOUR", "shipping_method": d.get("shipping_method") or "FREE",
        "first_val_amount": default.get("first_val_amount") or 1, "first_price": (default.get("first_price") or 0) / 100,
        "second_val_amount": default.get("second_val_amount") or 1, "second_price": (default.get("second_price") or 0) / 100,
        "condition": "amount" if cond.get("amount_flag") else "piece", "min_piece": cond.get("min_piece") or 2,
        "min_amount": (cond.get("min_amount") or 0) / 100,
        "not_send_provinces": list(dict.fromkeys(a.get("province_name") for a in
                                                 (d.get("not_send_area") or {}).get("address_infos") or [] if a.get("province_name"))),
        "is_default": bool(d.get("is_default")), "address_id": "",
        "address_text": "".join(addr.get(k, "") for k in ("province_name", "city_name", "county_name", "detail_info")),
        "region_rules": len([m for m in calc if not m.get("is_default")]),
    }


@router.get("/freight-options")
def freight_options(_: User = Depends(current_user)):
    return {"provinces": PROVINCES, "send_times": [{"id": k, "name": v} for k, v in SEND_TIMES.items()]}


@router.get("/{store_id}/freight-templates/{tid}")
def get_freight_template(store_id: int, tid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_store(db, user, store_id)
    try:
        return _freight_form(client_factory(s.appid, decrypt_secret(s.secret_enc)).freight_template_detail(tid))
    except Exception as e:
        raise HTTPException(400, f"读取运费模板失败：{explain(e)}")


@router.post("/{store_id}/freight-templates")
def add_freight_template(store_id: int, body: FreightIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """新建运费模板 in the store (its owner; accounts it is assigned to use the owner's)."""
    s = get_store(db, user, store_id, owner_only=True)  # an assigned account uses the owner's templates
    c = client_factory(s.appid, decrypt_secret(s.secret_enc))
    if not body.address_id:
        raise HTTPException(400, "请选择发货地址")
    try:
        tid = c.add_freight_template(_freight_payload(body, None, c.address_info(body.address_id)))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"新建运费模板失败：{explain(e)}")
    return {"id": tid, "name": body.name.strip()}


@router.put("/{store_id}/freight-templates/{tid}")
def update_freight_template(store_id: int, tid: str, body: FreightIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    s = get_store(db, user, store_id, owner_only=True)  # an assigned account uses the owner's templates
    c = client_factory(s.appid, decrypt_secret(s.secret_enc))
    try:
        old = c.freight_template_detail(tid)
        tpl = _freight_payload(body, old, c.address_info(body.address_id) if body.address_id else None)
        tpl["template_id"] = tid
        c.update_freight_template(tpl)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"保存运费模板失败：{explain(e)}")
    # the new name on every 发布配置 that uses it
    from ..models import StoreAccess
    if s.freight_template_id == tid:
        s.freight_template_name = body.name.strip()
    for a in db.scalars(select(StoreAccess).where(StoreAccess.store_id == s.id, StoreAccess.freight_template_id == tid)):
        a.freight_template_name = body.name.strip()
    db.commit()
    return {"id": tid, "name": body.name.strip()}


@router.put("/{store_id}/config")
def config_store(store_id: int, body: StoreConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_store(db, user, store_id)
    _apply(s, body, db, user)
    db.commit()
    return _shared_out(db, user, s, _all_templates(db, user, [s]))


def _match(options: list[dict], tid: str | None, name: str | None) -> dict | None:
    """The store's own entry: the same id, else the same name (each store has its own template / address ids)."""
    return (next((o for o in options if tid and o["id"] == str(tid)), None)
            or next((o for o in options if name and o["name"] == name), None))


@router.post("/bulk-config")
def bulk_config(body: BulkConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """批量配置: the 运费模板 / 退货地址 are read from the first store; for every other store its own one with
    the same name is used (ids differ between stores). A store without one keeps its own and is reported."""
    missing = []
    for sid in body.store_ids:
        s = get_store(db, user, sid)
        b = body.model_copy()
        if s.tenant_id == user.tenant_id and (body.freight_template_id or body.address_id):  # assigned: the owner's
            c = client_factory(s.appid, decrypt_secret(s.secret_enc))
            for fid, fname, label, fn in (("freight_template_id", "freight_template_name", "运费模板", c.freight_templates),
                                          ("address_id", "address_name", "退货地址", c.addresses)):
                if not getattr(body, fid):
                    continue
                try:
                    m = _match(fn(), getattr(body, fid), getattr(body, fname))
                except Exception as e:
                    m = None
                    missing.append({"store": s.name, "item": label, "error": explain(e)})
                else:
                    if m is None:
                        missing.append({"store": s.name, "item": label, "error": f"这个店铺没有名为「{getattr(body, fname)}」的{label}"})
                if m:
                    setattr(b, fid, m["id"]); setattr(b, fname, m["name"])
                else:
                    setattr(b, fid, None); setattr(b, fname, None)
        _apply(s, b, db, user)
    db.commit()
    return {"ok": True, "count": len(body.store_ids), "missing": missing}


def check_config(s: Store, cfg: dict) -> list[str]:
    """The 运费模板 / 退货地址 of the 发布配置 still exist in the store ([] = fine, also when it cannot be read)."""
    try:
        c = client_factory(s.appid, decrypt_secret(s.secret_enc))
        bad = []
        if cfg.get("freight_template_id") and str(cfg["freight_template_id"]) not in c.freight_template_ids():
            bad.append(f"运费模板「{cfg.get('freight_template_name') or cfg['freight_template_id']}」")
        if cfg.get("address_id") and str(cfg["address_id"]) not in c.address_ids():
            bad.append(f"退货地址「{cfg.get('address_name') or cfg['address_id']}」")
        return bad
    except Exception:
        return []
