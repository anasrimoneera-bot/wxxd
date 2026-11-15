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
