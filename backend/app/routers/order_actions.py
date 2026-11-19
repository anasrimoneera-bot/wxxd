"""订单 操作 like 微信小店后台 → 订单管理: 发货协商（修改发货时间）, 取消订单（代买家发起仅退款）and 发消息
to the buyer (商家客服 API: the order card and/or text)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import BuyerMessage, StoreOrder, User
from ..services.wechat import WeChatError, explain
from ..timeutil import cn_time, cn_ts
from .common import fmt_dt
from .orders import _client, _out, _own_order, _refresh

router = APIRouter(prefix="/api/orders", tags=["订单操作"])

NEGOTIATE_REASONS = {1: "买家要求", 2: "商品库存不足", 3: "缺货保障", 4: "收货信息有误"}
NEGOTIATE_STATES = {1: "等待买家确认", 2: "买家已同意", 3: "买家已拒绝", 4: "买家超时未处理（视为拒绝）",
                    10: "已通知买家", 11: "商家已取消", 99: "无效"}
NEGOTIATE_MAX = 3
CANCEL_REASONS = {"10000014": "双方协商一致退款", "10000001": "缺货", "10000002": "拍错/多拍", "10000000": "不想要了",
                  "10000008": "其他"}


def _wx_error(e: Exception, what: str) -> HTTPException:
    return HTTPException(400, f"{what}失败：{explain(e)}")


# ---- 发货协商: 修改发货时间 ---------------------------------------------------------------------
def _negotiation_out(n: dict) -> dict:
    t = lambda k: fmt_dt(cn_time(int(n[k]))) if n.get(k) else ""  # noqa: E731
    return {"state": n.get("state"), "state_name": NEGOTIATE_STATES.get(n.get("state"), str(n.get("state"))),
            "reason": NEGOTIATE_REASONS.get(n.get("reason"), str(n.get("reason") or "")), "remark": n.get("remark") or "",
            "original_delivery_time": t("original_delivery_time"), "predict_delivery_time": t("predict_delivery_time"),
            "deadline": t("deadline"), "create_time": t("create_time")}


@router.get("/{oid}/negotiation")
def negotiation(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """This order's 发货协商 so far (newest first) and how many are left (3 per order)."""
    o = _own_order(db, user, oid)
    try:
        rows = _client(db, o).delivery_negotiations([o.order_id])
    except Exception as e:
        raise _wx_error(e, "查询协商")
    rows = sorted([r for r in rows if str(r.get("order_id")) == o.order_id], key=lambda r: -(r.get("create_time") or 0))
    return {"items": [_negotiation_out(r) for r in rows], "left": max(NEGOTIATE_MAX - len(rows), 0),
            "reasons": [{"value": k, "label": v} for k, v in NEGOTIATE_REASONS.items()],
            "can_negotiate": o.status == 20}


class NegotiateIn(BaseModel):
    predict_delivery_time: str = Field(min_length=10, max_length=19)  # YYYY-MM-DD HH:MM[:SS], China time
    reason: int
    remark: str = Field(default="", max_length=200)


@router.post("/{oid}/negotiate")
def negotiate(oid: int, body: NegotiateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """修改发货时间: the buyer gets a notice (valid 4 hours) and agrees or refuses."""
    o = _own_order(db, user, oid)
    if o.status != 20:
        raise HTTPException(400, "只有待发货的订单可以协商发货时间")
    if body.reason not in NEGOTIATE_REASONS:
        raise HTTPException(400, "请选择协商原因")
    try:
        when = datetime.strptime(body.predict_delivery_time.strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, "发货时间格式应为 YYYY-MM-DD HH:MM")
    if when <= datetime.now():
        raise HTTPException(400, "新的发货时间要晚于现在")
    try:
        _client(db, o).delivery_negotiate(o.order_id, cn_ts(when), body.reason, body.remark.strip())
    except Exception as e:
        raise _wx_error(e, "提交协商")
    return negotiation(oid, user, db)


# ---- 取消订单: 代买家发起「仅退款」 -------------------------------------------------------------
class CancelIn(BaseModel):
    reason: str = "10000014"
    desc: str = Field(default="", max_length=200)


@router.post("/{oid}/cancel")
def cancel_order(oid: int, body: CancelIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """取消订单: for each line of an unshipped order a 仅退款 售后 is started for the buyer with the whole
    line amount; WeChat refunds and the order becomes 售后取消 once every line is refunded."""
    o = _own_order(db, user, oid)
    if o.status != 20:
        raise HTTPException(400, "只有待发货（还没发货）的订单可以取消；已发货的请走售后")
    if body.reason not in CANCEL_REASONS:
        raise HTTPException(400, "请选择取消原因")
    c = _client(db, o)
    results = []
    for i, it in enumerate(o.items or []):
        amount = it.get("real_price") or 0  # the line's 实付 (fen)
        req = {"request_id": f"cancel-{o.order_id}-{i}", "order_id": o.order_id, "product_id": it.get("product_id"),
               "sku_id": it.get("sku_id"), "count": it.get("qty") or 1, "amount": amount, "reason": body.reason,
               "type": "REFUND"}
        if body.desc.strip():
            req["desc"] = body.desc.strip()
        try:
            results.append({"title": it.get("title", ""), "ok": True, "aftersale_id": c.aftersale_for_buyer(req),
                            "amount": amount})
        except Exception as e:
            results.append({"title": it.get("title", ""), "ok": False, "error": explain(e)})
    _refresh(db, o, c)
    return {"results": results, "order": _out(db, user, o)}


# ---- 发消息 to the buyer (商家客服) -----------------------------------------------------------
def _openid(o: StoreOrder) -> str:
    raw = o.raw or {}
    return str((raw.get("order") or raw).get("openid") or "")


KF_ERRORS = {10190009: "买家最后一次发消息已超过 48 小时，暂时不能主动发消息（等买家再发消息后可以回复）",
             10190008: "已达到回复条数上限（买家每发一次消息，最多回复 5 条）",
             10190013: "买家已结束会话，不能继续发消息",
             10190010: "消息内容被风控拦截，请修改后再发"}


@router.get("/{oid}/message")
def message_state(oid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The buyer's conversation for this order (last messages) and whether a reply is possible now."""
    from .messages import _reply_state, msg_out
    o = _own_order(db, user, oid)
    openid = _openid(o)
    if not openid:
        return {"openid": "", "items": [], "can_reply": False, "reason": "订单里没有买家 openid，请先同步订单"}
    rows = db.scalars(select(BuyerMessage).where(BuyerMessage.store_id == o.store_id, BuyerMessage.openid == openid)
                      .order_by(BuyerMessage.id.desc()).limit(20)).all()[::-1]
    return {"openid": openid, "items": [msg_out(m) for m in rows], **_reply_state(db, o.store_id, openid)}


class OrderMessageIn(BaseModel):
    text: str = Field(default="", max_length=1000)
    order_card: bool = False  # also send the order's card


@router.post("/{oid}/message")
def send_message(oid: int, body: OrderMessageIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """发消息: the order card and/or text through 商家客服. WeChat allows it within 48 h of the buyer's last
    message, 5 messages at most; it says so if not (our own record may not know every buyer message)."""
    o = _own_order(db, user, oid)
    openid = _openid(o)
    if not openid:
        raise HTTPException(400, "订单里没有买家 openid，请先同步订单")
    text = body.text.strip()
    if not text and not body.order_card:
        raise HTTPException(400, "请填写消息内容")
    c = _client(db, o)
    sender = user.display_name or user.username
    sent = []
    try:
        if body.order_card:
            r = c.send_kf_order_card(openid, o.order_id)
            sent.append(BuyerMessage(store_id=o.store_id, openid=openid, direction="out", msg_type="order_share",
                                     content=f"[订单卡片] {o.order_id}", sender=sender, read=True,
                                     wx_msg_id=str((r or {}).get("msg_id") or "")))
        if text:
            r = c.send_kf_text(openid, text)
            sent.append(BuyerMessage(store_id=o.store_id, openid=openid, direction="out", msg_type="text",
                                     content=text, sender=sender, read=True, wx_msg_id=str((r or {}).get("msg_id") or "")))
    except WeChatError as e:
        db.add_all(sent)
        db.commit()
        raise HTTPException(400, f"发送失败：{KF_ERRORS.get(e.errcode) or explain(e)}")
    except Exception as e:
        db.add_all(sent)
        db.commit()
        raise _wx_error(e, "发送")
    db.add_all(sent)
    db.commit()
    return message_state(oid, user, db)
