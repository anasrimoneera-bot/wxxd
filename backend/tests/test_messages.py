"""买家消息: WeChat pushes buyer messages (plain or 安全模式) to the store's URL; all stores' conversations
in one list; replies within 48 h, 5 at most until the buyer writes again."""
import json
import time
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import BuyerMessage
from app.services import wxpush

from .conftest import FakeWeChat
from .test_flow import setup_store

AES = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"


def _signed(token, encrypt=""):
    ts, nonce = str(int(time.time())), "n1"
    key = "msg_signature" if encrypt else "signature"
    return {"timestamp": ts, "nonce": nonce, key: wxpush.signature(token, ts, nonce, encrypt)}


def test_buyer_messages_push_list_reply(client):
    store, _ = setup_store(client)
    sid = store["id"]
    assert client.post(f"/api/wx/push/{sid}", content="{}").status_code == 404  # not set up yet
    r = client.put(f"/api/stores/{sid}/push", json={"token": "tok123", "aes_key": AES}).json()
    assert r["path"] == f"/api/wx/push/{sid}" and r["aes_key_set"]

    # URL check (明文): echostr back; a wrong signature is refused
    assert client.get(f"/api/wx/push/{sid}", params={**_signed("tok123"), "echostr": "hello"}).text == "hello"
    assert client.get(f"/api/wx/push/{sid}", params={"timestamp": "1", "nonce": "x", "signature": "bad", "echostr": "e"}).status_code == 403

    # a plain JSON push
    msg = {"ToUserName": "gh_shop", "FromUserName": "buyer-1", "CreateTime": int(time.time()), "MsgType": "event",
           "Event": "commkf_send_msg_to_kf", "msg_id": "9001", "msg_type": {"text": {}}, "text": {"content": "什么时候发货？"}}
    assert client.post(f"/api/wx/push/{sid}", params=_signed("tok123"), content=json.dumps(msg)).text == "success"
    assert client.post(f"/api/wx/push/{sid}", params=_signed("tok123"), content=json.dumps(msg)).text == "success"  # retried: kept once
    # an encrypted (安全模式) push from another buyer
    msg2 = {**msg, "FromUserName": "buyer-2", "msg_id": "9002", "msg_type": "order_share", "order_share": {"order_id": "3700001"}}
    enc = wxpush.encrypt(AES, json.dumps(msg2, ensure_ascii=False), "wxapp")
    assert client.post(f"/api/wx/push/{sid}", params=_signed("tok123", enc), content=json.dumps({"Encrypt": enc})).text == "success"
    assert client.post(f"/api/wx/push/{sid}", params={"timestamp": "1", "nonce": "x", "signature": "bad"},
                       content=json.dumps(msg)).status_code == 403  # forged

    convs = client.get("/api/messages/conversations").json()
    assert convs["unread_total"] == 2 and {c["openid"] for c in convs["items"]} == {"buyer-1", "buyer-2"}
    b2 = next(c for c in convs["items"] if c["openid"] == "buyer-2")
    assert b2["last"]["content"] == "[订单卡片] order_id=3700001"

    chat = client.get("/api/messages", params={"store_id": sid, "openid": "buyer-1"}).json()
    assert chat["items"][0]["content"] == "什么时候发货？" and chat["can_reply"] and chat["left"] == 5
    assert client.get("/api/messages/conversations").json()["unread_total"] == 1  # buyer-1 read now

    FakeWeChat.kf_sent.clear()
    for i in range(5):
        r = client.post("/api/messages/send", json={"store_id": sid, "openid": "buyer-1", "text": f"今天发货{i}"})
        assert r.status_code == 200, r.text
    assert FakeWeChat.kf_sent[0] == ("buyer-1", "今天发货0") and r.json()["left"] == 0
    r = client.post("/api/messages/send", json={"store_id": sid, "openid": "buyer-1", "text": "第6条"})
    assert r.status_code == 400 and "5 条" in r.json()["detail"]

    with SessionLocal() as db:  # 48 h after the buyer's last message: no more replies
        for m in db.query(BuyerMessage).filter(BuyerMessage.openid == "buyer-2"):
            m.created_at = datetime.now() - timedelta(hours=49)
        db.commit()
    r = client.post("/api/messages/send", json={"store_id": sid, "openid": "buyer-2", "text": "在吗"})
    assert r.status_code == 400 and "48 小时" in r.json()["detail"]


def _push(client, sid, **msg):
    body = {"ToUserName": "gh_shop", "CreateTime": int(time.time()), "MsgType": "event", "Event": "commkf_send_msg_to_kf", **msg}
    assert client.post(f"/api/wx/push/{sid}", params=_signed("tok123"), content=json.dumps(body, ensure_ascii=False)).text == "success"


def test_evaluate_result_and_ai_reply(client, monkeypatch):
    """评价 events show as such (not a message to answer); AI 客服 answers (auto) or drafts (suggest),
    with the buyer's orders as facts; 退款-like questions are marked 需人工."""
    from app.models import Store, StoreOrder
    from app.services import ai_reply, order_sync
    store, _ = setup_store(client)
    sid = store["id"]
    client.put(f"/api/stores/{sid}/push", json={"token": "tok123"})
    _push(client, sid, FromUserName="b1", msg_id="1", msg_event={"EVENT_TYPE_EVALUATE_RESULT": {}},
          evaluate_result={"value": 3, "reason_list": ["回复及时"]})
    chat = client.get("/api/messages", params={"store_id": sid, "openid": "b1"}).json()
    assert chat["items"][0]["content"] == "[买家评价客服] 挺好的（回复及时）" and not chat["can_reply"]
    st = client.get("/api/messages/conversations").json()["stores"][0]
    assert st["only_events"] and not st["chat_seen"]  # still in 客服工作台 mode: no chats come here

    # AI needs the DeepSeek key
    assert client.put(f"/api/stores/{sid}/ai-reply", json={"mode": "auto", "kb": "48小时内发货，默认中通"}).status_code == 400
    client.put("/api/admin/settings", json={"deepseek_api_key": "sk-test"})
    assert client.put(f"/api/stores/{sid}/ai-reply", json={"mode": "auto", "kb": "48小时内发货，默认中通"}).json()["mode"] == "auto"

    with SessionLocal() as db:  # the buyer's order (openid kept from the order data)
        o = order_sync.upsert(db, db.get(Store, sid), {"order": {"order_id": "3700009", "openid": "b2", "status": 30,
                              "create_time": int(time.time()), "order_detail": {"product_infos": [], "delivery_info": {
                                  "delivery_product_info": [{"delivery_id": "ZTO", "waybill_id": "ZT123"}]}}}})
        db.commit()
        assert o.buyer_openid == "b2"
    asked = []

    def fake_model(system, user):
        asked.append(user)
        return {"reply": "亲，订单已发货，中通 ZT123" if "发货" in user else "已记录，马上为您处理", "need_human": "退款" in user}
    monkeypatch.setattr(ai_reply, "ask_model", fake_model)
    monkeypatch.setattr(ai_reply, "DEBOUNCE_SECONDS", 0)
    FakeWeChat.kf_sent.clear()
    _push(client, sid, FromUserName="b2", msg_id="2", msg_type={"text": {}}, text={"content": "什么时候发货"})
    assert "订单 3700009" in asked[-1] and "ZT123" in asked[-1] and "48小时内发货" in asked[-1]
    assert FakeWeChat.kf_sent[-1] == ("b2", "亲，订单已发货，中通 ZT123")
    chat = client.get("/api/messages", params={"store_id": sid, "openid": "b2"}).json()
    assert chat["items"][-1]["sender"] == "AI客服" and chat["left"] == 4  # the AI used one of the 5

    _push(client, sid, FromUserName="b2", msg_id="3", msg_type={"text": {}}, text={"content": "我要退款"})
    convs = client.get("/api/messages/conversations").json()
    assert convs["need_human_total"] == 1 and convs["stores"][0]["chat_seen"]
    # a person answers: no longer 需人工
    client.post("/api/messages/send", json={"store_id": sid, "openid": "b2", "text": "好的，已为您申请"})
    assert client.get("/api/messages/conversations").json()["need_human_total"] == 0

    # suggest: a draft, nothing sent; 建议回复 on demand
    client.put(f"/api/stores/{sid}/ai-reply", json={"mode": "suggest", "kb": ""})
    n = len(FakeWeChat.kf_sent)
    _push(client, sid, FromUserName="b3", msg_id="4", msg_type={"text": {}}, text={"content": "在吗"})
    items = client.get("/api/messages", params={"store_id": sid, "openid": "b3"}).json()["items"]
    assert items[-1]["direction"] == "draft" and len(FakeWeChat.kf_sent) == n
    assert client.post("/api/messages/suggest", json={"store_id": sid, "openid": "b3"}).json()["reply"]
    assert client.delete(f"/api/messages/draft/{items[-1]['id']}").json()["ok"]

    # several quick messages: only the newest is answered
    from app.models import BuyerMessage
    with SessionLocal() as db:
        first = BuyerMessage(store_id=sid, openid="b4", direction="in", msg_type="text", content="a")
        second = BuyerMessage(store_id=sid, openid="b4", direction="in", msg_type="text", content="b")
        db.add_all([first, second]); db.commit()
        ids = (first.id, second.id)
    ai_reply.handle(sid, "b4", ids[0], wait=0)
    assert client.get("/api/messages", params={"store_id": sid, "openid": "b4"}).json()["items"][-1]["content"] == "b"
