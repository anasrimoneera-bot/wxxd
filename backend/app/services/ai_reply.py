"""AI 客服 for 买家消息: answers buyer messages with DeepSeek (the key in 系统设置), from the conversation,
the buyer's orders in this store, the product / order cards they sent, and the store's 店铺客服资料.

Per store (Store.ai_reply_mode): off | suggest (a draft the seller sends with one click) | auto (sent at
once). WeChat allows 5 replies per buyer message (within 48 h); the AI uses at most one of them, so
people can still answer. Refunds, complaints, quality problems and anything the facts do not cover get a
short holding reply and the conversation is marked 需人工."""
import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy import select

from ..db import SessionLocal
from ..models import BuyerMessage, Store, StoreOrder, StoreProduct
from . import settings_store

log = logging.getLogger(__name__)
AI_SENDER = "AI客服"
DEBOUNCE_SECONDS = 4  # buyers often send several short messages: answer them together
HISTORY = 16  # messages of the conversation given to the model

SYSTEM = (
    "你是微信小店的在线客服，替商家及时回复买家。要求：\n"
    "1. 用简体中文，语气亲切、礼貌、简洁，一般不超过 80 个字，不用表情符号堆砌，不用 markdown。\n"
    "2. 只根据下面给出的【店铺资料】【买家的订单】【商品信息】和聊天记录回答，不编造发货时间、物流、价格、库存、规格、优惠等信息；"
    "资料里没有的，礼貌说明会让同事核实后尽快回复。\n"
    "3. 退款、退货、换货、补发、投诉、质量问题、少件错发、催促多次、情绪激动、要求赔偿：先致歉安抚，说明已记录并会尽快由专人处理，"
    "不要自己承诺退款、赔偿或金额，need_human 设为 true。\n"
    "4. 不索要、不提供站外联系方式，不引导到其他平台，不使用「最」「第一」等夸大用语。\n"
    "5. 买家只是打招呼时，热情问候并询问需要什么帮助。\n"
    "只返回 JSON：{\"reply\": \"回复内容\", \"need_human\": true 或 false}"
)


def _orders_text(db, store: Store, openid: str, extra_ids: list[str]) -> str:
    from .order_sync import deliveries, status_name
    q = select(StoreOrder).where(StoreOrder.store_id == store.id)
    rows = list(db.scalars(q.where(StoreOrder.buyer_openid == openid).order_by(StoreOrder.create_time.desc()).limit(5))) if openid else []
    for oid in extra_ids:  # an 订单卡片 the buyer sent
        if not any(o.order_id == oid for o in rows):
            o = db.scalar(q.where(StoreOrder.order_id == oid))
            if o:
                rows.append(o)
    lines = []
    for o in rows[:6]:
        items = "、".join(f"{(i.get('title') or '')[:24]}{(' ' + i['attrs']) if i.get('attrs') else ''}×{i.get('qty') or 1}"
                         for i in (o.items or [])[:4])
        ship = "；".join(f"{d.get('delivery_id', '')} {d.get('waybill_id', '')}" for d in deliveries(o.raw or {})) or "未发货"
        lines.append(f"- 订单 {o.order_id}，{status_name(o.status)}，下单 {o.create_time:%m-%d %H:%M}" if o.create_time else
                     f"- 订单 {o.order_id}，{status_name(o.status)}")
        lines[-1] += f"，商品：{items}，实付 ¥{(o.order_price or 0) / 100:.2f}，物流：{ship}"
    return "\n".join(lines) or "（没有找到这位买家在本店的订单）"


def _products_text(db, store: Store, product_ids: list[str]) -> str:
    out = []
    for pid in product_ids[:3]:
        p = db.scalar(select(StoreProduct).where(StoreProduct.store_id == store.id, StoreProduct.wx_product_id == pid))
        if not p:
            continue
        specs = "；".join(f"{k.get('attrs') or '默认'} ¥{(k.get('price') or 0) / 100:.2f} 库存{k.get('stock') or 0}"
                         for k in (p.skus or [])[:8] if not k.get("removed"))
        out.append(f"- 商品 {pid}：{p.title}；规格：{specs or '无'}")
    return "\n".join(out)


def _card_ids(msgs: list[BuyerMessage], kind: str) -> list[str]:
    ids = []
    for m in msgs:
        obj = (m.raw or {}).get(kind) if m.raw else None
        key = "order_id" if kind == "order_share" else "product_id"
        if isinstance(obj, dict) and obj.get(key):
            ids.append(str(obj[key]))
    return list(dict.fromkeys(ids))


def ask_model(system: str, user: str) -> dict:
    api_key = settings_store.get("deepseek_api_key")
    if not api_key:
        raise RuntimeError("未设置 DeepSeek API Key（系统设置）")
    from .titles import LEGACY_MODELS
    base_url = settings_store.get("deepseek_base_url") or settings_store.DEFAULTS["deepseek_base_url"]
    model = settings_store.get("deepseek_model") or settings_store.DEFAULTS["deepseek_model"]
    r = httpx.post(f"{base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={
        "model": LEGACY_MODELS.get(model, model),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
        "temperature": 0.4, "max_tokens": 400}, timeout=40)
    r.raise_for_status()
    data = json.loads(r.json()["choices"][0]["message"]["content"])
    return {"reply": str(data.get("reply") or "").strip()[:500], "need_human": bool(data.get("need_human"))}


def draft(db, store: Store, openid: str) -> dict:
    """{reply, need_human} for the conversation as it stands."""
    msgs = list(db.scalars(select(BuyerMessage).where(BuyerMessage.store_id == store.id, BuyerMessage.openid == openid,
                                                      BuyerMessage.direction.in_(("in", "out")))
                           .order_by(BuyerMessage.id.desc()).limit(HISTORY)))[::-1]
    chat = "\n".join(f"{'买家' if m.direction == 'in' else '客服'}（{m.created_at:%m-%d %H:%M}）：{m.content}"
                     for m in msgs if m.msg_type != "evaluate")
    products = _products_text(db, store, _card_ids(msgs, "product_share"))
    user = (f"【店铺】{store.name}\n【店铺资料】\n{(store.ai_reply_kb or '（商家没有填写）').strip()[:3000]}\n"
            f"【买家的订单】\n{_orders_text(db, store, openid, _card_ids(msgs, 'order_share'))}\n"
            + (f"【商品信息】\n{products}\n" if products else "")
            + f"【现在时间】{datetime.now():%Y-%m-%d %H:%M}\n【聊天记录】（最后一条是买家刚发的）\n{chat}\n"
              "请回复买家最新的消息。")
    return ask_model(SYSTEM, user)


def handle(store_id: int, openid: str, message_id: int, client_factory=None, wait: float | None = None) -> None:
    """After a buyer message (run in the background): wait a moment, and if it is still the newest one,
    answer it according to the store's AI 客服 mode."""
    time.sleep(DEBOUNCE_SECONDS if wait is None else wait)
    with SessionLocal() as db:
        store = db.get(Store, store_id)
        if not store or (store.ai_reply_mode or "off") == "off":
            return
        newest = db.scalar(select(BuyerMessage.id).where(BuyerMessage.store_id == store_id, BuyerMessage.openid == openid,
                                                         BuyerMessage.direction == "in", BuyerMessage.msg_type != "evaluate")
                           .order_by(BuyerMessage.id.desc()).limit(1))
        if newest != message_id:
            return  # a newer message came in: that one answers
        last = db.scalar(select(BuyerMessage).where(BuyerMessage.store_id == store_id, BuyerMessage.openid == openid,
                                                    BuyerMessage.id > message_id, BuyerMessage.direction.in_(("out", "draft"))))
        if last:
            return  # already answered (by a person, or a draft is waiting)
        from ..routers.messages import _reply_state
        if not _reply_state(db, store_id, openid)["can_reply"]:
            return
        try:
            ans = draft(db, store, openid)
        except Exception as e:
            log.warning("AI 客服 failed for store %s: %s", store_id, e)
            db.add(BuyerMessage(store_id=store_id, openid=openid, direction="draft", msg_type="error",
                                content=f"AI 客服没有回复：{e}"[:500], sender=AI_SENDER, read=True))
            db.commit()
            return
        if not ans["reply"]:
            return
        buyer_msg = db.get(BuyerMessage, message_id)
        if buyer_msg is not None and ans["need_human"]:
            buyer_msg.need_human = True
        if store.ai_reply_mode == "auto":
            from ..routers import stores as stores_router
            from ..security import decrypt_secret
            from .wechat import explain
            factory = client_factory or stores_router.client_factory
            try:
                r = factory(store.appid, decrypt_secret(store.secret_enc)).send_kf_text(openid, ans["reply"])
            except Exception as e:  # e.g. 会话超过48h, 风控: keep it as a draft to send by hand
                db.add(BuyerMessage(store_id=store_id, openid=openid, direction="draft", msg_type="text",
                                    content=ans["reply"], sender=f"{AI_SENDER}（发送失败：{explain(e)}）"[:64], read=True))
                db.commit()
                return
            db.add(BuyerMessage(store_id=store_id, openid=openid, direction="out", msg_type="text", content=ans["reply"],
                                sender=AI_SENDER, read=True, wx_msg_id=str((r or {}).get("msg_id") or "")))
        else:
            db.add(BuyerMessage(store_id=store_id, openid=openid, direction="draft", msg_type="text",
                                content=ans["reply"], sender=AI_SENDER, read=True))
        db.commit()
