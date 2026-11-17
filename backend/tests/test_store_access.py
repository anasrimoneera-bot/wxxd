"""用户管理 → 分配店铺: an assigned store is used by another account like its own (shared data);
only the owner changes its settings or deletes it; unassigning removes the access."""
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import ProductSourceLink, Store, StoreOrder, StoreProduct

from .conftest import new_tenant_client
from .test_flow import setup_store


def test_assign_store_to_another_account(client):
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", title="除雾刷", skus=[])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id="o1", price=500))
        db.add(StoreOrder(tenant_id=s.tenant_id, store_id=s.id, order_id="A1", status=20,
                          items=[{"product_id": "w1", "sku_id": "s1", "qty": 1}], raw={}))
        db.commit()
    with TestClient(app) as other:
        new_tenant_client(client, other, "worker2")
        tid = next(t["id"] for t in client.get("/api/admin/tenants").json() if t["username"] == "worker2")
        assert other.get("/api/stores").json() == []
        assert other.get("/api/orders").json()["total"] == 0

        r = client.get(f"/api/admin/tenants/{tid}/stores").json()
        assert [x["id"] for x in r["available"]] == [store["id"]] and r["assigned"] == []
        r = client.post(f"/api/admin/tenants/{tid}/stores", json={"store_ids": [store["id"]]}).json()
        assert r["added"] == 1 and [x["id"] for x in r["assigned"]] == [store["id"]]
        assert next(t for t in client.get("/api/admin/tenants").json() if t["id"] == tid)["stores"] == 1

        # the assigned account sees the same store data
        st = other.get("/api/stores").json()
        assert st[0]["id"] == store["id"] and st[0]["shared"] and st[0]["publish_template_name"]
        prods = other.get("/api/products", params={"store_id": store["id"]}).json()["items"]
        assert prods[0]["title"] == "除雾刷" and prods[0]["source"]["offer_id"] == "o1"
        orders = other.get("/api/orders").json()["items"]
        assert orders[0]["order_id"] == "A1" and orders[0]["items"][0]["source"]["offer_id"] == "o1"
        assert other.get("/api/categories/overview", params={"store_id": store["id"]}).status_code == 200
        # ... but the owner keeps the settings
        assert other.put(f"/api/stores/{store['id']}/config", json={"address_name": "x"}).status_code == 403
        assert other.delete(f"/api/stores/{store['id']}").status_code == 403

        # unassign: gone for the other account, unchanged for the owner
        client.delete(f"/api/admin/tenants/{tid}/stores/{store['id']}")
        assert other.get("/api/stores").json() == [] and other.get("/api/orders").json()["total"] == 0
        assert other.get(f"/api/orders/{orders[0]['id']}").status_code == 404
        assert client.get("/api/orders").json()["total"] == 1
