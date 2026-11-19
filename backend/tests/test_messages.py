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
           "Event": "commkf_send_msg_to_kf", "msg_type": "text", "text": {"content": "什么时候发货？"}}
    assert client.post(f"/api/wx/push/{sid}", params=_signed("tok123"), content=json.dumps(msg)).text == "success"
    # an encrypted (安全模式) push from another buyer
    msg2 = {**msg, "FromUserName": "buyer-2", "msg_type": "order_share", "order_share": {"order_id": "3700001"}}
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
