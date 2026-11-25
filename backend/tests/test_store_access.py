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
        # 发布配置: the assigned account uses the owner's 运费模板 / 退货地址 and picks its own 上货模板
        owner_cfg = client.get("/api/stores").json()[0]
        tpl = other.post("/api/templates", json={"name": "我的模板", "config": other.get("/api/templates/defaults").json()}).json()
        r = other.put(f"/api/stores/{store['id']}/config", json={"freight_template_id": "999", "freight_template_name": "我的运费",
                                                                 "address_id": "333", "publish_template_id": tpl["id"]}).json()
        assert (r["freight_template_id"], r["address_id"], r["publish_template_name"]) == (
            owner_cfg["freight_template_id"], owner_cfg["address_id"], "我的模板")
        assert other.post("/api/stores/bulk-config", json={"store_ids": [store["id"]], "address_id": "444", "address_name": "x"}).json()["missing"] == []
        mine = client.get("/api/stores").json()[0]
        assert (mine["freight_template_id"], mine["address_id"], mine["publish_template_id"]) == (
            owner_cfg["freight_template_id"], owner_cfg["address_id"], owner_cfg["publish_template_id"])
        assert other.post(f"/api/stores/{store['id']}/freight-templates", json={"name": "x", "address_id": "222"}).status_code == 403
        # its 云发布: the owner's 运费模板 / 地址, its own 上货模板
        from app.models import LibraryProduct, PublishTask
        with SessionLocal() as db:
            db.add(LibraryProduct(tenant_id=tid, platform="1688", source_product_id="9", title="锅", cover="x",
                                  normalized={"skus": []}, raw="{}"))
            db.commit()
        t = other.post("/api/tasks", json={"store_ids": [store["id"]], "platform": "1688"})
        assert t.status_code == 200, t.text
        with SessionLocal() as db:
            task = db.get(PublishTask, t.json()[0]["id"])
            assert (task.freight_template_id, task.address_id, task.template_name) == (
                owner_cfg["freight_template_id"], owner_cfg["address_id"], "我的模板")
        # the owner's 任务列表 shows the task the assigned account created on its store
        lst = client.get("/api/tasks").json()
        mine_task = next(x for x in lst["items"] if x["id"] == t.json()[0]["id"])
        assert mine_task["assigned"] and "worker2" in mine_task["creator"] and lst["assigned_count"] == 1
        assert client.get("/api/tasks", params={"who": "mine"}).json()["total"] == lst["total"] - 1
        assert client.get("/api/tasks", params={"who": "assigned"}).json()["total"] == 1
        assert client.get(f"/api/tasks/{mine_task['id']}/items").status_code == 200
        assert other.get("/api/tasks").json()["total"] == 1  # the assigned account sees only its own
        # the owner's 运费模板 no longer in the store (e.g. another store's from 批量配置): no task, a clear message
        client.put(f"/api/stores/{store['id']}/config", json={"freight_template_id": "777", "freight_template_name": "别的店的运费"})
        r = other.post("/api/tasks", json={"store_ids": [store["id"]], "platform": "1688"})
        assert r.status_code == 400 and "别的店的运费" in r.json()["detail"] and "不存在" in r.json()["detail"]
        # 批量配置 (owner): its own address by the same name; one the store does not have is reported, not saved
        r = client.post("/api/stores/bulk-config", json={"store_ids": [store["id"]], "address_id": "444", "address_name": "别的店的地址"}).json()
        assert r["missing"][0]["item"] == "退货地址" and client.get("/api/stores").json()[0]["address_id"] == owner_cfg["address_id"]
        r = client.post("/api/stores/bulk-config", json={"store_ids": [store["id"]], "address_id": "x", "address_name": "北京市朝阳区测试路1号"}).json()
        assert r["missing"] == [] and client.get("/api/stores").json()[0]["address_id"] == "222"
        assert other.delete(f"/api/stores/{store['id']}").status_code == 403

        # unassign: gone for the other account, unchanged for the owner
        client.delete(f"/api/admin/tenants/{tid}/stores/{store['id']}")
        assert other.get("/api/stores").json() == [] and other.get("/api/orders").json()["total"] == 0
        assert other.get(f"/api/orders/{orders[0]['id']}").status_code == 404
        assert client.get("/api/orders").json()["total"] == 1
