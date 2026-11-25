"""订单管理 phase 1: orders synced from the WeChat Store API, with each line's 货源 cost."""
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.models import ProductSourceLink, Store, StoreOrder, StoreProduct
from app.services import order_sync

from .conftest import FakeWeChat
from .test_flow import setup_store

NOW = datetime(2026, 11, 15, 12, 0, 0)


def detail(oid, status=20, pid="w1", qty=2):
    return {"errcode": 0, "order": {
        "order_id": oid, "status": status, "create_time": int((NOW - timedelta(days=1)).timestamp()),
        "update_time": int(NOW.timestamp()),
        "order_detail": {
            "product_infos": [{"product_id": pid, "sku_id": "s1", "title": "除雾刷", "thumb_img": "https://x/t.jpg",
                               "sku_cnt": qty, "sale_price": 2500, "real_price": 4800,
                               "sku_attrs": [{"attr_key": "颜色", "attr_value": "红色"}]}],
            "price_info": {"product_price": 5000, "order_price": 4800, "freight": 0},
            "pay_info": {"pay_time": int(NOW.timestamp())},
            "delivery_info": {"address_info": {"user_name": "张三", "tel_number": "13800000000",
                                               "province_name": "广东省", "city_name": "深圳市",
                                               "county_name": "南山区", "detail_info": "科技园1号"}},
            "ext_info": {"customer_notes": "尽快发货"}}}}


class OrderWeChat(FakeWeChat):
    windows = []

    def list_orders(self, start, end, next_key="", page_size=100):
        OrderWeChat.windows.append((start, end, next_key))
        if end - start <= 7 * 86400 and not next_key and len(OrderWeChat.windows) == 1:
            return {"order_id_list": ["A1"], "next_key": "k2", "has_more": True}
        if next_key == "k2":
            return {"order_id_list": ["A2"], "has_more": False}
        return {"order_id_list": [], "has_more": False}

    def get_order(self, oid):
        return detail(oid, status=20 if oid == "A1" else 100)


def test_order_sync_and_list(client):
    store, _ = setup_store(client)
    OrderWeChat.windows = []
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", skus=[])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id="o1", price=1000,
                                 detail_url="https://detail.1688.com/offer/o1.html"))
        db.commit()
    order_sync.sync_orders(store["id"], OrderWeChat, now=NOW)
    # first sync: the last 30 days, in windows of at most 7 days
    assert len({(a, b) for a, b, _ in OrderWeChat.windows}) == 5
    assert all(b - a <= 7 * 86400 for a, b, _ in OrderWeChat.windows)

    st = client.get("/api/orders/stores").json()[0]
    assert st["sync_status"] == "done" and st["orders"] == 2
    data = client.get("/api/orders", params={"status": 20}).json()
    assert data["total"] == 1 and data["status_counts"] == {"20": 1, "100": 1}
    o = data["items"][0]
    assert (o["order_id"], o["status_name"], o["receiver_name"]) == ("A1", "待发货", "张三")
    assert o["receiver_address"] == "广东省深圳市南山区科技园1号" and o["buyer_note"] == "尽快发货"
    assert o["items"][0]["attrs"] == "颜色:红色" and o["items"][0]["source"]["offer_id"] == "o1"
    assert (o["source_cost"], o["margin"]) == (2000, 2800)  # 2 x ¥10 source; paid ¥48
    assert client.get(f"/api/orders/{o['id']}").json()["raw"]["order"]["order_id"] == "A1"

    # next sync continues from where the last one finished (one window)
    OrderWeChat.windows = []
    order_sync.sync_orders(store["id"], OrderWeChat, now=NOW + timedelta(hours=1))
    assert len({(a, b) for a, b, _ in OrderWeChat.windows}) == 1
    with SessionLocal() as db:
        assert db.query(StoreOrder).count() == 2  # updated, not duplicated


def test_orders_are_tenant_isolated(client):
    from fastapi.testclient import TestClient

    from app.main import app

    from .conftest import new_tenant_client
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        db.add(StoreOrder(tenant_id=s.tenant_id, store_id=s.id, order_id="X", items=[], raw={}))
        db.commit()
        oid = db.query(StoreOrder).one().id
    with TestClient(app) as other:
        new_tenant_client(client, other, "owner2")
        assert other.get("/api/orders").json()["total"] == 0
        assert other.get(f"/api/orders/{oid}").status_code == 404


MASKED = {"order": {"status": 200, "order_id": "3740472690866306048", "create_time": 1794381141,
                    "update_time": 1794561594, "order_detail": {
                        "ext_info": {"customer_notes": "", "merchant_notes": ""},
                        "pay_info": {"pay_time": 1794381150},
                        "price_info": {"freight": 0, "order_price": 340, "product_price": 340},
                        "delivery_info": {"address_info": {
                            "city_name": "湛江市", "user_name": "郑**", "tel_number": "1**********",
                            "county_name": "徐闻县", "detail_info": "****", "province_name": "广东省",
                            "house_number": "", "virtual_order_tel_number": ""}},
                        "product_infos": [{"title": "一支中性笔", "sku_id": "35577698350", "sku_cnt": 1, "sku_attrs": [],
                                           "product_id": "10001894283731", "real_price": 340, "sale_price": 340}]}},
          "errmsg": "ok", "errcode": 0}


class DecodeWeChat(FakeWeChat):
    decoded = 0

    def decode_order_sensitive(self, oid):
        DecodeWeChat.decoded += 1
        return {"errcode": 0, "address_info": {"user_name": "郑小明", "tel_number": "13912345678",
                                               "province_name": "广东省", "city_name": "湛江市",
                                               "county_name": "徐闻县", "detail_info": "城北路8号"}}


def test_reveal_masked_buyer_info(client, monkeypatch):
    """A real 获取订单详情 response masks the buyer (郑** / 1********** / ****): 显示买家信息 decodes it
    once and keeps it, also when the order is synced again."""
    from app.routers import stores
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", DecodeWeChat)
    with SessionLocal() as db:
        order_sync.upsert(db, db.get(Store, store["id"]), MASKED)
        db.commit()
    o = client.get("/api/orders").json()["items"][0]
    assert o["masked"] and o["status_name"] == "售后取消" and o["receiver_name"] == "郑**"
    r = client.post(f"/api/orders/{o['id']}/reveal").json()
    assert (r["receiver_name"], r["receiver_phone"]) == ("郑小明", "13912345678")
    assert r["receiver_address"] == "广东省湛江市徐闻县城北路8号" and r["revealed"] and not r["masked"]
    client.post(f"/api/orders/{o['id']}/reveal")
    assert DecodeWeChat.decoded == 1  # decoded once (daily quota), then read from the order
    with SessionLocal() as db:  # a later sync brings the masked version again: the decoded one stays
        order_sync.upsert(db, db.get(Store, store["id"]), MASKED)
        db.commit()
    assert client.get("/api/orders").json()["items"][0]["receiver_name"] == "郑小明"


class ShipWeChat(DecodeWeChat):
    sent, comp, state = [], [], {}

    def decode_order_sensitive(self, oid):
        DecodeWeChat.decoded += 1
        return {"errcode": 0,
                "address_info": {"user_name": "李君干", "tel_number": "159****5570", "province_name": "广东省",
                                 "city_name": "中山市", "county_name": "三乡镇", "detail_info": "雅居乐10栋902"},
                "virtual_number_info": {"virtual_tel_number": "17012345678", "extension": "1234",
                                        "virtual_tel_expire_time": int(NOW.timestamp()) + 86400 * 20}}

    def delivery_companies(self):
        return [{"delivery_id": "ZTO", "delivery_name": "中通快递"}, {"delivery_id": "STO", "delivery_name": "申通快递"}]

    def send_delivery(self, oid, delivery_list):
        ShipWeChat.sent.append((oid, delivery_list))
        ShipWeChat.state[oid] = delivery_list
        return {"errcode": 0}

    def compensate_delivery(self, oid, delivery_list, reason=""):
        ShipWeChat.comp.append((oid, delivery_list, reason))
        return {"errcode": 0}

    def delay_virtual_number(self, oid):
        return {"errcode": 0}

    def get_order(self, oid):
        d = detail(oid, qty=2)
        done = ShipWeChat.state.get(oid)
        if done:
            d["order"]["status"] = 30
            d["order"]["order_detail"]["delivery_info"]["delivery_product_info"] = [
                {**x, "delivery_time": int(NOW.timestamp())} for x in done]
        return d


def test_virtual_number_and_shipping(client, monkeypatch):
    from app.routers import stores
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", ShipWeChat)
    ShipWeChat.sent, ShipWeChat.comp, ShipWeChat.state = [], [], {}
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        for oid in ("A1", "A2", "A3"):
            order_sync.upsert(db, s, detail(oid, qty=2))
        db.commit()
        part = detail("A3", qty=2)  # A3: one of the two already shipped
        part["order"]["status"] = 21
        part["order"]["order_detail"]["delivery_info"]["delivery_product_info"] = [
            {"delivery_id": "STO", "waybill_id": "W0", "product_infos": [{"product_id": "w1", "sku_id": "s1", "product_cnt": 1}]}]
        order_sync.upsert(db, s, part)
        db.commit()
    rows = {o["order_id"]: o for o in client.get("/api/orders").json()["items"]}

    # 显示买家信息: the real phone stays masked (虚拟号), the virtual number + extension is shown
    r = client.post(f"/api/orders/{rows['A1']['id']}/reveal").json()
    assert r["phone_masked"] and r["virtual_phone"] == "17012345678转1234" and r["virtual_phone_expire"]
    n = DecodeWeChat.decoded
    client.post(f"/api/orders/{rows['A1']['id']}/reveal")
    assert DecodeWeChat.decoded == n  # kept
    client.post(f"/api/orders/{rows['A1']['id']}/reveal", params={"again": True})
    assert DecodeWeChat.decoded == n + 1  # 重新获取
    assert client.post(f"/api/orders/{rows['A1']['id']}/virtual-delay").json()["virtual_phone"]

    # 发货: everything not shipped yet
    assert [c["id"] for c in client.get("/api/orders/delivery-companies").json()] == ["ZTO", "STO"]
    assert client.post(f"/api/orders/{rows['A1']['id']}/ship", json={"delivery_id": "ZTO"}).status_code == 400
    o = client.post(f"/api/orders/{rows['A1']['id']}/ship", json={"delivery_id": "ZTO", "waybill_id": "W1"}).json()
    assert ShipWeChat.sent[-1] == ("A1", [{"deliver_type": 1, "delivery_id": "ZTO", "waybill_id": "W1",
                                           "product_infos": [{"product_id": "w1", "sku_id": "s1", "product_cnt": 2}]}])
    assert o["status_name"] == "待收货" and o["deliveries"][0]["waybill_id"] == "W1" and not o["can_ship"]
    assert client.post(f"/api/orders/{rows['A1']['id']}/ship", json={"delivery_id": "ZTO", "waybill_id": "W1"}).status_code == 400

    # 批量发货: 订单号 快递公司(名称或编码) 运单号; A3 ships only what is left
    r = client.post("/api/orders/ship-batch",
                    json={"text": "A2 中通 W2\nA3\tSTO\tW3\nZZ 中通 W9\nA2 中通 W8\nA1 顺丰 W7"}).json()
    assert (r["ok"], r["failed"]) == (2, 3)
    assert ShipWeChat.sent[-1][1][0]["product_infos"][0]["product_cnt"] == 1  # A3: 1 of 2 left
    errs = [x["error"] for x in r["results"] if not x["ok"]]
    assert "订单不存在" in errs[0] and "不能发货" in errs[1] and "不认识的快递公司" in errs[2]

    # 补发货
    o = client.post(f"/api/orders/{rows['A1']['id']}/ship",
                    json={"delivery_id": "ZTO", "waybill_id": "W5", "compensation": True, "reason": "漏发"}).json()
    assert ShipWeChat.comp[-1][0] == "A1" and ShipWeChat.comp[-1][2] == "漏发"


def test_purchase_real_cost_and_source_price(client):
    """采购: purchase order number + real cost give the real margin; 货源单价 updates the product's 货源."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", skus=[])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id="o1", price=478))
        order_sync.upsert(db, s, detail("B1", qty=1))
        db.commit()
    o = client.get("/api/orders").json()["items"][0]
    assert o["source_cost"] == 478 and o["margin"] == 4800 - 478 and not o["margin_real"]
    src_id = o["items"][0]["source"]["id"]
    r = client.put(f"/api/orders/{o['id']}/purchase", json={
        "purchase_order_no": "3366998877", "purchase_cost": 8.28, "source_prices": [{"source_id": src_id, "unit_price": 6.22}]}).json()
    assert r["purchase_order_no"] == "3366998877" and r["purchase_cost"] == 828
    assert r["margin"] == 4800 - 828 and r["margin_real"]
    assert r["items"][0]["source"]["price"] == 622 and r["items"][0]["source"]["original_price"] == 478
    assert client.get("/api/orders", params={"keyword": "3366998877"}).json()["total"] == 1
    assert client.put(f"/api/orders/{o['id']}/purchase", json={"source_prices": [{"source_id": 999, "unit_price": 1}]}).status_code == 400
    r = client.put(f"/api/orders/{o['id']}/purchase", json={}).json()  # clear
    assert r["purchase_cost"] is None and not r["margin_real"] and r["margin"] == 4800 - 622


def test_mark_purchased_by_hand_and_notes(client):
    """未采购 → 已采购 by hand (no 采购单号), and back; the notes are in the list."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        d = detail("M1", qty=1)
        d["order"]["order_detail"].setdefault("ext_info", {}).update(customer_notes="请发顺丰", merchant_notes="送小礼品")
        order_sync.upsert(db, s, d)
        db.commit()
    o = client.get("/api/orders").json()["items"][0]
    assert not o["purchased"] and o["buyer_note"] == "请发顺丰" and o["merchant_note"] == "送小礼品"
    assert client.post("/api/orders/purchase/mark", json={"order_ids": [o["id"]]}).json()["ok"] == 1
    o = client.get("/api/orders").json()["items"][0]
    assert o["purchased"] and o["purchased_at"] and o["purchase_order_no"] == "" and o["purchase_note"].startswith("手动标记已采购")
    # saving the 采购 dialog empty keeps it 已采购; 改回未采购 undoes it
    assert client.put(f"/api/orders/{o['id']}/purchase", json={"purchase_note": o["purchase_note"]}).json()["purchased"]
    client.post("/api/orders/purchase/mark", json={"order_ids": [o["id"]], "purchased": False})
    o = client.get("/api/orders").json()["items"][0]
    assert not o["purchased"] and o["purchase_note"] == ""
    # with a 采购单号 it stays 已采购 (取消采购 is for that)
    client.put(f"/api/orders/{o['id']}/purchase", json={"purchase_order_no": "3366998877"})
    assert client.post("/api/orders/purchase/mark", json={"order_ids": [o["id"]], "purchased": False}).json()["ok"] == 0
