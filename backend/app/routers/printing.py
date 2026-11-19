"""打单: print 快递单 (电子面单) for orders, like 微信小店后台 → 订单 → 打单发货, and 面单 PDF import.

Print flow (微信小店「电子面单打印组件」): the server takes a 面单 number (预取号 + 取号, with template_id
so WeChat returns print_info); the browser hands print_info to the 打印组件 running on the user's PC
(ws://127.0.0.1:12705); after it printed, the server sends 打印成功通知 and, if asked, 订单发货."""
import random
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import access
from ..db import get_db
from ..deps import current_user
from ..models import Store, StoreOrder, User
from ..services import order_sync
from ..services.wechat import explain
from .orders import ShipIn, _client, ship_order

router = APIRouter(prefix="/api/orders", tags=["打单"])


def _store(db, user, store_id: int) -> Store:
    s = access.stores(db, user.tenant_id).get(store_id)
    if not s:
        raise HTTPException(404, "店铺不存在")
    return s


def _wx(db, s: Store):
    from ..security import decrypt_secret
    from . import stores as stores_router
    return stores_router.client_factory(s.appid, decrypt_secret(s.secret_enc))


@router.get("/print/options")
def print_options(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The store's 面单账号 (bound 快递公司 / 网点), templates, and the saved sender."""
    s = _store(db, user, store_id)
    c = _wx(db, s)
    out = {"accounts": [], "templates": {}, "sender": s.print_sender or {}, "errors": []}
    try:
        for a in c.ewaybill_accounts():
            site = a.get("site_info") or {}
            out["accounts"].append({"acct_id": a.get("acct_id"), "delivery_id": a.get("delivery_id"), "shop_id": a.get("shop_id"),
                                    "site_code": site.get("site_code") or "", "site_name": site.get("site_name") or "",
                                    "monthly_card": a.get("monthly_card") or "", "available": a.get("available"),
                                    "sender_address": a.get("sender_address") or {}})
    except Exception as e:
        out["errors"].append(f"读取面单账号失败：{explain(e)}（请先在微信小店后台开通电子面单）")
    try:
        for t in c.ewaybill_templates():
            out["templates"][t.get("delivery_id")] = {"default": t.get("default_template_id") or "", "list": [
                {"template_id": x.get("template_id"), "name": x.get("template_name")} for x in t.get("template_list") or []]}
    except Exception as e:
        out["errors"].append(f"读取面单模板失败：{explain(e)}")
    return out


class Sender(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    mobile: str = Field(min_length=5, max_length=20)
    province: str = Field(min_length=1, max_length=32)
    city: str = Field(min_length=1, max_length=32)
    county: str = Field(default="", max_length=32)
    street: str = Field(default="", max_length=64)
    address: str = Field(min_length=1, max_length=200)


class WaybillIn(BaseModel):
    order_ids: list[int] = Field(min_length=1, max_length=100)
    acct_id: str
    delivery_id: str
    shop_id: str
    site_code: str = ""
    template_id: str = "single"  # the 快递公司's template id; 'single' = WeChat's default template
    sender: Sender


def _receiver(o: StoreOrder) -> dict:
    """取号 only checks the receiver is not empty: WeChat uses the order's own address."""
    raw = o.raw or {}
    a = (((raw.get("order") or raw).get("order_detail") or {}).get("delivery_info") or {}).get("address_info") or {}
    return {"name": a.get("user_name") or o.receiver_name or "买家", "mobile": a.get("tel_number") or o.receiver_phone or "1",
            "province": a.get("province_name") or "-", "city": a.get("city_name") or "-",
            "county": a.get("county_name") or "-", "street": "", "address": a.get("detail_info") or o.receiver_address or "-"}


@router.post("/print/waybills")
def take_waybills(body: WaybillIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """取号 for each order (or the print data again for one taken before with the same 快递公司).
    Returns, per order, the waybill number and print_info for the 打印组件."""
    results, clients = [], {}
    sender = body.sender.model_dump()
    for oid in body.order_ids:
        o = db.get(StoreOrder, oid)
        if not o or not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)):
            results.append({"id": oid, "ok": False, "error": "订单不存在"})
            continue
        store = db.get(Store, o.store_id)
        store.print_sender = sender  # used next time
        c = clients.setdefault(o.store_id, _wx(db, store))
        ew = o.ewaybill or {}
        try:
            if ew.get("waybill_id") and ew.get("delivery_id") == body.delivery_id:  # 补打: the same 面单
                info = c.ewaybill_print_content(ew["ewaybill_order_id"], body.template_id)
                results.append({"id": o.id, "order_id": o.order_id, "ok": True, "waybill_id": ew["waybill_id"],
                                "print_info": info, "re_print": True})
                continue
            if o.status not in (20, 21):
                raise ValueError(f"订单状态为「{order_sync.status_name(o.status)}」，不能打单")
            goods = [{"good_name": (it.get("title") or "商品")[:60], "good_count": it.get("qty") or 1,
                      "product_id": int(it["product_id"]) if str(it["product_id"]).isdigit() else it["product_id"],
                      "sku_id": int(it["sku_id"]) if str(it["sku_id"]).isdigit() else it["sku_id"]} for it in o.items or []]
            req = {"delivery_id": body.delivery_id, "site_code": body.site_code, "ewaybill_acct_id": body.acct_id,
                   "sender": sender, "receiver": _receiver(o), "shop_id": body.shop_id,
                   "ec_order_list": [{"ec_order_id": int(o.order_id) if o.order_id.isdigit() else o.order_id, "goods_list": goods}]}
            req["ewaybill_order_id"] = c.ewaybill_precreate(req)
            r = c.ewaybill_create({**req, "template_id": body.template_id})
            if not r.get("waybill_id"):
                raise ValueError(r.get("delivery_error_msg") or "快递公司没有返回运单号")
            o.ewaybill = {"ewaybill_order_id": str(r.get("ewaybill_order_id") or req["ewaybill_order_id"]),
                          "delivery_id": body.delivery_id, "waybill_id": r["waybill_id"], "acct_id": body.acct_id,
                          "taken_at": datetime.now().isoformat(timespec="seconds"), "printed_at": None}
            db.commit()
            results.append({"id": o.id, "order_id": o.order_id, "ok": True, "waybill_id": r["waybill_id"],
                            "print_info": r.get("print_info") or c.ewaybill_print_content(o.ewaybill["ewaybill_order_id"], body.template_id),
                            "re_print": False})
        except Exception as e:
            db.rollback()
            results.append({"id": o.id, "order_id": o.order_id, "ok": False, "error": explain(e) if not isinstance(e, ValueError) else str(e)})
    db.commit()
    return {"results": results}


class PrintedItem(BaseModel):
    id: int
    re_print: bool = False


class PrintedIn(BaseModel):
    items: list[PrintedItem] = Field(min_length=1, max_length=100)
    ship: bool = True  # 打单并发货


@router.post("/print/done")
def printed(body: PrintedIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The 打印组件 printed these: 打印成功通知, then (ship) 订单发货 with the waybill number."""
    results = []
    for it in body.items:
        o = db.get(StoreOrder, it.id)
        if not o or not access.can_use(db, user.tenant_id, db.get(Store, o.store_id)) or not (o.ewaybill or {}).get("waybill_id"):
            results.append({"id": it.id, "ok": False, "error": "没有面单"})
            continue
        ew = o.ewaybill
        c = _client(db, o)
        try:
            c.ewaybill_printed(ew["ewaybill_order_id"], ew["delivery_id"], ew["waybill_id"], it.re_print)
            o.ewaybill = {**ew, "printed_at": datetime.now().isoformat(timespec="seconds")}
            flag_modified(o, "ewaybill")
            db.commit()
            if body.ship and o.status in (20, 21):
                ship_order(db, o, ShipIn(delivery_id=ew["delivery_id"], waybill_id=ew["waybill_id"]), c)
            results.append({"id": o.id, "order_id": o.order_id, "ok": True, "shipped": body.ship and o.status not in (20, 21)})
        except HTTPException as e:
            results.append({"id": o.id, "order_id": o.order_id, "ok": False, "error": e.detail})
        except Exception as e:
            results.append({"id": o.id, "order_id": o.order_id, "ok": False, "error": explain(e)})
    return {"results": results}


# ---- 面单 PDF: read the receiver from downloaded shipping labels -------------------------------
def made_phone(last4: str, seed: str) -> str:
    """1 + 6 made-up digits + the last 4 from the label (the middle digits are not the buyer's)."""
    rnd = random.Random(seed)  # the same order always gets the same number
    return "1" + str(rnd.randint(3, 9)) + "".join(str(rnd.randint(0, 9)) for _ in range(5)) + last4


@router.post("/labels/import")
async def import_labels(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """面单 PDF (downloaded from 微信小店后台 打单): each page's receiver goes to its order — matched by the
    order number on the label, else by its waybill number."""
    from ..services import label_ocr
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(400, "文件太大（最多 30MB）")
    try:
        from starlette.concurrency import run_in_threadpool
        pages = await run_in_threadpool(label_ocr.page_lines, data)  # OCR: ~1-2 s a page
    except Exception:
        raise HTTPException(400, "读不了这个 PDF")
    store_ids = access.store_ids(user.tenant_id)
    results = []
    for n, lines in enumerate(pages, 1):
        text = "\n".join(lines)
        lab = label_ocr.parse_label(lines)
        if not lab:
            results.append({"page": n, "ok": False, "error": "这一页没找到收件人"})
            continue
        o = None
        if lab["order_id"]:
            o = db.scalar(select(StoreOrder).where(StoreOrder.store_id.in_(store_ids), StoreOrder.order_id == lab["order_id"]))
        if o is None:  # by the waybill number printed on the label
            for wb in sorted(set(re.findall(r"(?<![A-Za-z0-9])[A-Z]{0,4}\d{10,16}(?!\d)", text))):
                for cand in db.scalars(select(StoreOrder).where(StoreOrder.store_id.in_(store_ids), StoreOrder.status.in_([20, 21, 30]))
                                       .order_by(StoreOrder.id.desc()).limit(2000)):
                    if (cand.ewaybill or {}).get("waybill_id") == wb or any(
                            d.get("waybill_id") == wb for d in order_sync.deliveries(cand.raw or {})):
                        o = cand
                        break
                if o:
                    break
        if o is None:
            results.append({"page": n, "ok": False, "error": "没找到对应的订单（面单上没有订单号）", "address": lab["address"]})
            continue
        o.label_address = {"name": lab["surname"], "phone": made_phone(lab["last4"], o.order_id) if lab["last4"] else "",
                           "last4": lab["last4"], "masked_name": lab["masked_name"], "masked_phone": lab["masked_phone"],
                           "address": lab["address"], "page": n, "file": (file.filename or "")[:100],
                           "imported_at": datetime.now().isoformat(timespec="seconds")}
        results.append({"page": n, "ok": True, "id": o.id, "order_id": o.order_id, **o.label_address})
    db.commit()
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}
