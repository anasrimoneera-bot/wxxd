"""订单: China time like 微信小店后台, 毛利合计 + 负毛利."""
from datetime import datetime

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import ProductSourceLink, Store, StoreOrder, StoreProduct
from app.services import order_sync

from .test_flow import setup_store
from .test_orders import ShipWeChat, detail


class ActWeChat(ShipWeChat):
    def get_order(self, oid):
        return detail(oid)


def _setup(client, monkeypatch):
    from app.routers import stores
    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", ActWeChat)
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
