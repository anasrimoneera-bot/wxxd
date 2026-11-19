"""订单: China time like 微信小店后台, 毛利合计 + 负毛利, 发货协商, 取消订单 (仅退款), 发消息."""
from datetime import datetime

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import ProductSourceLink, Store, StoreOrder, StoreProduct
from app.services import order_sync
from app.services.wechat import WeChatError

from .test_flow import setup_store
from .test_orders import ShipWeChat, detail


class ActWeChat(ShipWeChat):
    calls = []
    negs = []

    def get_order(self, oid):
        return detail(oid)

    def delivery_negotiate(self, order_id, ts, reason, remark=""):
        ActWeChat.calls.append(("negotiate", order_id, ts, reason, remark))
        ActWeChat.negs.append({"order_id": order_id, "predict_delivery_time": ts, "original_delivery_time": ts - 86400,
                               "state": 1, "reason": reason, "remark": remark, "create_time": ts - 3600})

    def delivery_negotiations(self, ids):
        return list(ActWeChat.negs)

    def aftersale_for_buyer(self, body):
        ActWeChat.calls.append(("aftersale", body))
        return "AS1"

    def send_kf_order_card(self, openid, order_id):
        ActWeChat.calls.append(("card", openid, order_id))
        return {"msg_id": "m1"}

    def send_kf_text(self, openid, text):
        if text == "late":
            raise WeChatError(10190009, "session over 48h")
        if text == "noapi":
            raise WeChatError(48001, "api unauthorized")
        ActWeChat.calls.append(("text", openid, text))
        return {"msg_id": "m2"}


def _setup(client, monkeypatch):
    from app.routers import stores
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", ActWeChat)
    ActWeChat.calls, ActWeChat.negs = [], []
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        d = detail("3740657452096339200", qty=1)
        d["order"]["create_time"] = 1795085969  # 2026-11-19 18:59:29 China time
        d["order"]["openid"] = "buyer-9"
        d["order"]["order_detail"]["product_infos"][0]["delivery_deadline"] = 1795276802  # 2026-11-22 00:00:02
        order_sync.upsert(db, s, d)
        db.commit()
    return store


def test_order_time_is_china_time(client, monkeypatch):
    _setup(client, monkeypatch)
    o = client.get("/api/orders").json()["items"][0]
    assert o["create_time"] == "2026-11-19 18:59:29"  # as 微信小店后台 shows it
    # an order synced before the fix (UTC clock): put right once from the saved response
    with SessionLocal() as db:
        db.query(StoreOrder).update({"create_time": datetime(2026, 11, 19, 10, 59, 29)})
        db.commit()
    from app import migrate
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_flag"))
    assert migrate.fix_order_times(engine) == 1 and migrate.fix_order_times(engine) == 0
    assert client.get("/api/orders").json()["items"][0]["create_time"] == "2026-11-19 18:59:29"


def test_margin_summary_and_negative_filter(client, monkeypatch):
    store = _setup(client, monkeypatch)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", skus=[])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id="o1", price=1000))
        order_sync.upsert(db, s, detail("B2", qty=1))
        db.commit()
        db.query(StoreOrder).filter_by(order_id="B2").update({"purchase_cost": 6000})  # paid ¥60 for a ¥48 order
        db.commit()
    r = client.get("/api/orders").json()
    # order 1: ¥48 - ¥10 货源 = ¥38; B2: ¥48 - ¥60 采购实付 = -¥12
    assert r["summary"]["margin_total"] == 3800 - 1200 and r["summary"]["negative_count"] == 1
    neg = client.get("/api/orders", params={"margin": "neg"}).json()
    assert neg["total"] == 1 and neg["items"][0]["order_id"] == "B2" and neg["items"][0]["margin"] == -1200


def test_negotiate_cancel_and_message(client, monkeypatch):
    _setup(client, monkeypatch)
    oid = client.get("/api/orders").json()["items"][0]["id"]
    # 修改发货时间
    assert client.post(f"/api/orders/{oid}/negotiate", json={"predict_delivery_time": "2020-01-01 10:00", "reason": 2}).status_code == 400
    r = client.post(f"/api/orders/{oid}/negotiate", json={"predict_delivery_time": "2099-11-23 18:00", "reason": 2, "remark": "补货中"}).json()
    call = ActWeChat.calls[0]
    assert call[0] == "negotiate" and call[3] == 2 and call[4] == "补货中"
    assert datetime.fromtimestamp(call[2]).strftime("%Y-%m-%d %H:%M") == "2099-11-23 18:00"
    assert r["delivery_deadline"] == "2026-11-22 00:00:02"
    assert r["left"] == 2 and r["items"][0]["state_name"] == "等待买家确认" and r["items"][0]["predict_delivery_time"] == "2099-11-23 18:00:00"
    # 发消息: order card + text; WeChat's 48 h rule explained
    m = client.post(f"/api/orders/{oid}/message", json={"text": "您好，已为您加急", "order_card": True}).json()
    assert ("card", "buyer-9", "3740657452096339200") in ActWeChat.calls and ("text", "buyer-9", "您好，已为您加急") in ActWeChat.calls
    assert [x["content"] for x in m["items"]][-1] == "您好，已为您加急"
    bad = client.post(f"/api/orders/{oid}/message", json={"text": "late"})
    assert bad.status_code == 400 and "48 小时" in bad.json()["detail"]
    noapi = client.post(f"/api/orders/{oid}/message", json={"text": "noapi"})
    assert noapi.status_code == 400 and "接入自研客服" in noapi.json()["detail"]
    # 取消订单: 仅退款 of every line, whole line amount
    c = client.post(f"/api/orders/{oid}/cancel", json={"reason": "10000001"}).json()
    body = next(x[1] for x in ActWeChat.calls if x[0] == "aftersale")
    assert c["results"][0]["ok"] and body["type"] == "REFUND" and body["amount"] == 4800 and body["count"] == 1
    assert body["reason"] == "10000001" and body["request_id"].startswith("cancel-")
