"""买家消息: buyer messages of every store the account may use, in one place (售后).

WeChat pushes them to /api/wx/push/{store_id} (set as the store's 消息推送 URL, with the Token and
EncodingAESKey saved here) as commkf_send_msg_to_kf events; replies go out with commkf/sendmsg."""
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import access
from ..db import get_db
from ..deps import current_user
from ..models import BuyerMessage, Store, User
from ..security import decrypt_secret, encrypt_secret
from ..services import settings_store, wxpush
from ..services.wechat import explain
from .common import fmt_dt
from ..timeutil import cn_time

router = APIRouter(tags=["买家消息"])
REPLY_HOURS, REPLY_MAX = 48, 5  # WeChat: within 48 h of the buyer's last message, at most 5 replies


# ---- WeChat → here ----------------------------------------------------------------------------
def _push_keys(store: Store) -> tuple[str, str]:
    return store.push_token or "", decrypt_secret(store.push_aes_key_enc) if store.push_aes_key_enc else ""


@router.get("/api/wx/push/{store_id}", response_class=PlainTextResponse)
def push_verify(store_id: int, request: Request, db: Session = Depends(get_db)):
    """The URL check when 消息推送 is saved in the WeChat back office: echo echostr back."""
    s = db.get(Store, store_id)
    if not s or not s.push_token:
        raise HTTPException(404, "未配置")
    q = dict(request.query_params)
    token, aes = _push_keys(s)
    echostr = q.get("echostr", "")
    if q.get("msg_signature"):  # 安全模式: echostr is encrypted
        if wxpush.signature(token, q.get("timestamp", ""), q.get("nonce", ""), echostr) != q["msg_signature"]:
            raise HTTPException(403, "签名不对")
        return wxpush.decrypt(aes, echostr)
    if wxpush.signature(token, q.get("timestamp", ""), q.get("nonce", "")) != q.get("signature"):
        raise HTTPException(403, "签名不对")
    return echostr


@router.post("/api/wx/push/{store_id}", response_class=PlainTextResponse)
async def push_receive(store_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """A push from WeChat. Buyer messages are saved; other events are acknowledged."""
    s = db.get(Store, store_id)
    if not s or not s.push_token:
        raise HTTPException(404, "未配置")
    token, aes = _push_keys(s)
    try:
        data = wxpush.open_message(token, aes, dict(request.query_params), (await request.body()).decode("utf-8"))
    except (wxpush.PushError, ValueError) as e:
        raise HTTPException(403, str(e))
    if data.get("Event") == "commkf_send_msg_to_kf":
        wx_id = str(data.get("msg_id") or "")
        if wx_id and db.scalar(select(BuyerMessage.id).where(BuyerMessage.store_id == s.id, BuyerMessage.wx_msg_id == wx_id)):
            return "success"  # WeChat retries a push it thinks failed: saved already
        msg_type, content = wxpush.message_text(data)
        try:
            at = cn_time(int(data.get("CreateTime") or 0)) if data.get("CreateTime") else datetime.now()
        except (TypeError, ValueError):
            at = datetime.now()
        m = BuyerMessage(store_id=s.id, openid=str(data.get("FromUserName") or data.get("openid") or ""),
                         direction="in", msg_type=msg_type, content=content[:4000], raw=data, created_at=at,
                         wx_msg_id=wx_id, read=msg_type == "evaluate")
        db.add(m)
        db.commit()
        if msg_type != "evaluate" and (s.ai_reply_mode or "off") != "off":  # AI 客服 answers in the background
            from ..services import ai_reply
            background.add_task(ai_reply.handle, s.id, m.openid, m.id)
    return "success"


# ---- the 买家消息 page ------------------------------------------------------------------------
def _reply_state(db, store_id: int, openid: str) -> dict:
    """Can we still reply? (48 h since the buyer's last message, at most 5 replies since)."""
    last_in = db.scalar(select(func.max(BuyerMessage.created_at)).where(  # a 评价 is not a message to answer
        BuyerMessage.store_id == store_id, BuyerMessage.openid == openid, BuyerMessage.direction == "in",
        BuyerMessage.msg_type != "evaluate"))
    if not last_in:
        return {"can_reply": False, "left": 0, "reason": "买家还没有发过消息"}
    deadline = last_in + timedelta(hours=REPLY_HOURS)
    sent = db.scalar(select(func.count()).where(BuyerMessage.store_id == store_id, BuyerMessage.openid == openid,
                                                BuyerMessage.direction == "out", BuyerMessage.created_at >= last_in)) or 0
    left = max(REPLY_MAX - sent, 0)
    if datetime.now() > deadline:
        return {"can_reply": False, "left": 0, "reason": "买家最后一条消息已超过 48 小时，等买家再发消息后才能回复"}
    if not left:
        return {"can_reply": False, "left": 0, "reason": "已回复 5 条，等买家再发消息后才能继续回复"}
    return {"can_reply": True, "left": left, "deadline": fmt_dt(deadline), "reason": ""}


def msg_out(m: BuyerMessage) -> dict:
    return {"id": m.id, "direction": m.direction, "msg_type": m.msg_type, "content": m.content,
            "sender": m.sender, "created_at": fmt_dt(m.created_at), "need_human": bool(m.need_human)}


@router.get("/api/messages/conversations")
def conversations(store_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One row per buyer conversation (store + openid), newest first, with unread counts."""
    stores = access.stores(db, user.tenant_id)
    ids = [store_id] if store_id in stores else list(stores)
    rows = db.execute(select(BuyerMessage.store_id, BuyerMessage.openid, func.max(BuyerMessage.id).label("last"),
                             func.sum((BuyerMessage.direction == "in") & (BuyerMessage.read == False)).label("unread"),  # noqa: E712
                             func.max(BuyerMessage.need_human).label("need_human"),
                             func.sum(BuyerMessage.direction == "draft").label("drafts"))
                      .where(BuyerMessage.store_id.in_(ids or [0])).group_by(BuyerMessage.store_id, BuyerMessage.openid)
                      .order_by(func.max(BuyerMessage.id).desc()).limit(300)).all()
    last = {m.id: m for m in db.scalars(select(BuyerMessage).where(BuyerMessage.id.in_([r.last for r in rows] or [0])))}
    out = []
    for r in rows:
        m = last.get(r.last)
        out.append({"store_id": r.store_id, "store_name": stores[r.store_id].name, "openid": r.openid,
                    "unread": int(r.unread or 0), "last": msg_out(m) if m else None,
                    "need_human": bool(r.need_human), "drafts": int(r.drafts or 0)})
    # 商家客服 in API mode: only then chats come here (in 客服工作台 mode only 评价 arrive)
    chat_seen = set(db.scalars(select(BuyerMessage.store_id).where(
        BuyerMessage.store_id.in_(ids or [0]), BuyerMessage.direction == "in", BuyerMessage.msg_type != "evaluate").distinct()))
    any_seen = set(db.scalars(select(BuyerMessage.store_id).where(BuyerMessage.store_id.in_(ids or [0])).distinct()))
    return {"items": out, "unread_total": sum(c["unread"] for c in out),
            "need_human_total": sum(c["need_human"] for c in out),
            "ai_key_set": bool(settings_store.get("deepseek_api_key")),
            "stores": [{"id": s.id, "name": s.name, "push_set": bool(s.push_token), "ai_reply_mode": s.ai_reply_mode or "off",
                        "chat_seen": s.id in chat_seen, "only_events": s.id in any_seen and s.id not in chat_seen}
                       for s in stores.values()]}


def _store(db, user, store_id) -> Store:
    s = access.stores(db, user.tenant_id).get(store_id)
    if not s:
        raise HTTPException(404, "店铺不存在")
    return s


@router.get("/api/messages")
def messages(store_id: int, openid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A conversation; marks the buyer's messages read."""
    _store(db, user, store_id)
    rows = db.scalars(select(BuyerMessage).where(BuyerMessage.store_id == store_id, BuyerMessage.openid == openid)
                      .order_by(BuyerMessage.id.desc()).limit(200)).all()[::-1]
    for m in rows:
        if m.direction == "in" and not m.read:
            m.read = True
    db.commit()
    return {"items": [msg_out(m) for m in rows], **_reply_state(db, store_id, openid)}


class SendIn(BaseModel):
    store_id: int
    openid: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=1000)


@router.post("/api/messages/send")
def send(body: SendIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from . import stores as stores_router
    s = _store(db, user, body.store_id)
    state = _reply_state(db, s.id, body.openid)
    if not state["can_reply"]:
        raise HTTPException(400, state["reason"])
    try:
        r = stores_router.client_factory(s.appid, decrypt_secret(s.secret_enc)).send_kf_text(body.openid, body.text)
    except Exception as e:
        raise HTTPException(400, f"发送失败：{explain(e)}")
    m = BuyerMessage(store_id=s.id, openid=body.openid, direction="out", msg_type="text", content=body.text,
                     sender=user.display_name or user.username, read=True, wx_msg_id=str((r or {}).get("msg_id") or ""))
    db.add(m)
    # answered by a person: no longer 需人工, AI drafts of the conversation are done with
    conv = (BuyerMessage.store_id == s.id) & (BuyerMessage.openid == body.openid)
    db.execute(update(BuyerMessage).where(conv, BuyerMessage.need_human == True).values(need_human=False))  # noqa: E712
    db.execute(delete(BuyerMessage).where(conv, BuyerMessage.direction == "draft"))
    db.commit()
    return {"message": msg_out(m), **_reply_state(db, s.id, body.openid)}


# ---- AI 客服 ------------------------------------------------------------------------------------
class ConvIn(BaseModel):
    store_id: int
    openid: str = Field(min_length=1, max_length=128)


@router.post("/api/messages/suggest")
def suggest(body: ConvIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """AI 建议回复 for the conversation (not sent): the seller edits and sends it."""
    from ..services import ai_reply
    s = _store(db, user, body.store_id)
    try:
        return ai_reply.draft(db, s, body.openid)
    except Exception as e:
        raise HTTPException(400, f"AI 生成失败：{e}")


class ConvDeleteIn(BaseModel):
    items: list[ConvIn] = Field(min_length=1, max_length=500)


@router.post("/api/messages/conversations/delete")
def delete_conversations(body: ConvDeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """删除会话: every message of these conversations, here only (WeChat keeps its own records). A buyer
    who writes again shows up as a new conversation."""
    n = 0
    for c in body.items:
        _store(db, user, c.store_id)
        n += db.execute(delete(BuyerMessage).where(BuyerMessage.store_id == c.store_id, BuyerMessage.openid == c.openid)).rowcount or 0
    db.commit()
    return {"deleted": n}


@router.delete("/api/messages/draft/{mid}")
def drop_draft(mid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(BuyerMessage, mid)
    if not m or m.direction != "draft":
        raise HTTPException(404, "没有这条建议")
    _store(db, user, m.store_id)
    db.delete(m)
    db.commit()
    return {"ok": True}


class AiReplyIn(BaseModel):
    mode: str = Field(pattern="^(off|suggest|auto)$")
    kb: str = Field(default="", max_length=5000)  # 店铺客服资料: 发货时间, 快递, 售后规则, 常见问题


@router.get("/api/stores/{store_id}/ai-reply")
def ai_get(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _store(db, user, store_id)
    return {"mode": s.ai_reply_mode or "off", "kb": s.ai_reply_kb or "", "key_set": bool(settings_store.get("deepseek_api_key"))}


@router.put("/api/stores/{store_id}/ai-reply")
def ai_set(store_id: int, body: AiReplyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _store(db, user, store_id)
    if body.mode != "off" and not settings_store.get("deepseek_api_key"):
        raise HTTPException(400, "还没有设置 DeepSeek API Key：请平台管理员在「系统设置」里填写后再开启 AI 客服")
    s.ai_reply_mode, s.ai_reply_kb = body.mode, body.kb.strip()
    db.commit()
    return ai_get(store_id, user, db)


# ---- 消息推送 settings of a store ----------------------------------------------------------------
class PushIn(BaseModel):
    token: str = Field(min_length=3, max_length=32)
    aes_key: str = Field(default="", max_length=43)  # EncodingAESKey (安全模式); empty = keep / 明文模式


@router.get("/api/stores/{store_id}/push")
def push_get(store_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _store(db, user, store_id)
    return {"path": f"/api/wx/push/{s.id}", "token": s.push_token or "", "aes_key_set": bool(s.push_aes_key_enc)}


@router.put("/api/stores/{store_id}/push")
def push_set(store_id: int, body: PushIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .stores import get_store
    s = get_store(db, user, store_id, owner_only=True)
    if body.aes_key and len(body.aes_key) != 43:
        raise HTTPException(400, "EncodingAESKey 应为 43 位")
    s.push_token = body.token.strip()
    if body.aes_key:
        s.push_aes_key_enc = encrypt_secret(body.aes_key.strip())
    db.commit()
    return push_get(store_id, user, db)
