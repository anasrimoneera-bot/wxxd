"""1688 货源助手: 换商品 by 微信小店商品 ID; 采购跟踪 (pasted buyer info → order → 1688 订单编号 + 实付)."""
from app.db import SessionLocal
from app.models import Store, StoreOrder, StoreProduct
from app.services import order_sync

from .test_flow import setup_store
from .test_orders import detail


def test_swap_product_by_wx_id(client):
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        db.add(StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="10000123", title="厨房垃圾桶",
                            skus=[{"sku_id": "k1", "attrs": [{"attr_key": "颜色", "attr_value": "白"}], "price": 2230}]))
        db.commit()
    r = client.get("/api/ext/by-wx", params={"product_id": " 10000123 "}).json()
    assert r["kind"] == "store" and r["title"] == "厨房垃圾桶" and r["specs"][0]["label"] and r["wx_product_id"] == "10000123"
    assert client.get("/api/ext/by-wx", params={"product_id": "999"}).status_code == 404


def test_purchase_tracking(client):
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        order_sync.upsert(db, s, detail("O1"))
        o2 = detail("O2")
        o2["order"]["order_detail"]["delivery_info"]["address_info"].update(user_name="李四", tel_number="13900000001",
                                                                              detail_info="科技园9号")
        order_sync.upsert(db, s, o2)
        db.commit()
        o = db.query(StoreOrder).filter_by(order_id="O2").one()
        o.receiver_phone = "13900000001"  # 显示买家信息 done
        db.commit()
        oid = o.id
    # 复制收件信息 pasted into 1688「使用临时地址」
    m = client.post("/api/ext/order-match", json={"text": "李四 139 0000 0001 广东省 深圳市 南山区 科技园9号"}).json()["order"]
    assert m["id"] == oid and m["order_id"] == "O2"
    assert client.post("/api/ext/order-match", json={"text": "王五 13511112222 北京市朝阳区"}).json()["order"] is None
    # 立即下单 ¥7.60 → 收银台 订单编号
    r = client.post("/api/ext/purchase", json={"id": oid, "purchase_order_nos": ["5127614858244044402"], "purchase_cost": 7.6}).json()
    assert r["ok"] and r["order"]["purchase_order_no"] == "5127614858244044402" and r["order"]["purchase_cost"] == 760
    # the 收银台 page again: nothing added twice
    assert client.post("/api/ext/purchase", json={"id": oid, "purchase_order_nos": ["5127614858244044402"], "purchase_cost": 7.6}).json()["duplicate"]
    # a second 1688 order (another supplier) for the same order: added
    r = client.post("/api/ext/purchase", json={"id": oid, "purchase_order_nos": ["5127614858244049999"], "purchase_cost": 3}).json()
    assert r["order"]["purchase_order_no"] == "5127614858244044402,5127614858244049999" and r["order"]["purchase_cost"] == 1060
    row = next(x for x in client.get("/api/orders").json()["items"] if x["id"] == oid)
    assert row["purchase_order_no"].startswith("5127614858244044402") and row["purchased_at"]


def test_purchase_updates_source_price(client):
    from app.models import ProductSourceLink
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", skus=[])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id="777", price=160))
        order_sync.upsert(db, s, detail("C1", qty=1))
        db.commit()
    o = client.get("/api/orders").json()["items"][0]
    # the 1688 order page: 单价 ¥2.00 × 1 (offer 777) → our 货源单价 ¥2.00 (the 1688 price is kept)
    r = client.post("/api/ext/purchase", json={"id": o["id"], "purchase_order_nos": ["5127614858244044402"], "purchase_cost": 7.6,
                                                "lines": [{"offer_id": "777", "unit_price": 2, "qty": 1}]}).json()
    assert r["prices_changed"] == 1
    src = client.get("/api/orders").json()["items"][0]["items"][0]["source"]
    assert src["price"] == 200 and src["original_price"] == 160
    # another offer: not this order's 货源 → nothing changed
    r = client.post("/api/ext/purchase", json={"id": o["id"], "purchase_order_nos": ["1"], "lines": [{"offer_id": "888", "unit_price": 5}]}).json()
    assert r["prices_changed"] == 0
