"""共享云库: one library for every account, duplicates skipped, daily upload limit, 积分 per publish of a
product someone else uploaded (refunded when the publish does not succeed), admin top-ups, and store
changes (货源, 改价) written back to 商品云库."""
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.migrate import backfill_origins
from app.models import LibraryProduct, ProductSourceLink, PublishedRecord, StoreProduct
from app.services import library_sync

from .conftest import make_package, new_tenant_client
from .test_flow import run_all, setup_store, upload


def _tenant_id(admin, username):
    return next(t["id"] for t in admin.get("/api/admin/tenants").json() if t["username"] == username)


def _publish(client, store_id, lib_ids):
    tasks = client.post("/api/tasks", json={"store_ids": [store_id], "platform": "wechat",
                                            "product_ids": lib_ids}).json()
    run_all([t["id"] for t in tasks])
    tid = client.get("/api/tasks").json()["items"][0]["id"]
    return client.get(f"/api/tasks/{tid}/items").json()["items"]


def test_shared_library_points_and_duplicates(client):
    assert upload(client, {"1.json": make_package("1", "伸缩款汽车除雾刷")})["created"] == 1
    with TestClient(app) as other:
        new_tenant_client(client, other, "buyer")
        # the same source product uploaded by another account is skipped
        res = upload(other, {"1.json": make_package("1", "伸缩款汽车除雾刷"), "2.json": make_package("2", "车载挂钩")})
        assert res["created"] == 1 and res["skipped"] == 1 and "共享云库" in res["skipped_items"][0]["reason"]

        shared = other.get("/api/library/shared").json()
        assert [i["source_product_id"] for i in shared["items"]] == ["1"] and shared["cost"] == 1
        assert other.post("/api/library/shared/add", json={"ids": [shared["items"][0]["id"]]}).json()["added"] == 1
        assert other.get("/api/library/shared").json()["total"] == 0  # already added
        mine = {i["source_product_id"]: i for i in other.get("/api/library/products").json()["items"]}
        assert mine["1"]["from_shared"] is True and mine["2"]["from_shared"] is False

        store, _ = setup_store(other, appid="wxbuyer000001")
        # no points: the shared product fails (nothing charged), the own upload publishes for free
        items = {i["source_product_id"]: i for i in _publish(other, store["id"], [mine["1"]["id"], mine["2"]["id"]])}
        assert items["1"]["status"] == "failed" and "积分不足" in items["1"]["message"]
        assert items["2"]["status"] == "success" and "积分" not in items["2"]["message"]
        assert other.get("/api/points/me").json()["points"] == 0

        # admin tops up 5; publishing the shared product costs 1
        tid = _tenant_id(client, "buyer")
        assert client.post(f"/api/admin/tenants/{tid}/points", json={"change": 5, "note": "微信转账"}).json()["points"] == 5
        items = _publish(other, store["id"], [mine["1"]["id"]])
        assert items[0]["status"] == "success" and "扣除 1 积分" in items[0]["message"]
        me = other.get("/api/points/me").json()
        assert me["points"] == 4
        logs = other.get("/api/points/logs").json()["items"]
        assert [(r["kind"], r["change"], r["balance"]) for r in logs] == [("publish", -1, 4), ("topup", 5, 5)]

        # skipped (already in this store): refunded, so still 4
        items = _publish(other, store["id"], [mine["1"]["id"]])
        assert items[0]["status"] == "skipped" and other.get("/api/points/me").json()["points"] == 4


def test_daily_upload_limit(client):
    client.put("/api/admin/settings", json={"daily_upload_limit": 2})
    res = upload(client, {f"{i}.json": make_package(str(i), f"商品{i}") for i in range(1, 5)})
    assert res["created"] == 2 and res["failed"] == 2 and "今日上传已达上限" in res["errors"][0]["error"]
    tid = _tenant_id(client, "owner1")
    client.put(f"/api/admin/tenants/{tid}", json={"daily_upload_limit": 0})  # 0 = no limit for this account
    assert upload(client, {"3.json": make_package("3", "商品3")})["created"] == 1


def test_backfill_keeps_first_uploader(client):
    with SessionLocal() as db:
        for tid, pid in ((1, "9"), (2, "9"), (2, "8")):
            db.add(LibraryProduct(tenant_id=tid, platform="wechat", source_product_id=pid, title="t",
                                  normalized={}, raw=""))
        db.commit()
    from app.db import engine
    assert backfill_origins(engine) == 3
    with SessionLocal() as db:
        got = {(p.tenant_id, p.source_product_id): p.origin_tenant_id for p in db.query(LibraryProduct)}
    assert got == {(1, "9"): 1, (2, "9"): 1, (2, "8"): 2}
    assert backfill_origins(engine) == 0  # runs once per row


def test_match_store_skus_to_library_skus():
    lib = [{"source_sku_id": "a", "attrs": [{"name": "颜色", "value": "红色【热卖】"}, {"name": "尺码", "value": "XL"}]},
           {"source_sku_id": "b", "attrs": [{"name": "颜色", "value": "蓝色"}, {"name": "尺码", "value": "XL"}]}]
    store = [{"sku_id": "s2", "attrs": "颜色:蓝色；尺码:XL"}, {"sku_id": "s1", "attrs": "颜色:红色；尺码:XL"}]
    assert library_sync.match_skus(store, lib) == {"s2": "b", "s1": "a"}  # 热卖 removed by SKU 违禁词
    named = [{"sku_id": "x", "attrs": "规格:款式2"}, {"sku_id": "y", "attrs": "规格:款式1"}]
    assert library_sync.match_skus(named, lib) == {"x": "b", "y": "a"}


def test_store_price_and_source_written_back(client):
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "伸缩款汽车除雾刷", skus=[
        {"id": "a", "price": 990, "attrs": [("颜色", "红色")]}, {"id": "b", "price": 1990, "attrs": [("颜色", "蓝色")]}])})
    with SessionLocal() as db:
        lib = db.query(LibraryProduct).one()
        db.add(PublishedRecord(store_id=store["id"], source_key="wechat:1", title_hash="h", wx_product_id="w1"))
        sp = StoreProduct(tenant_id=lib.tenant_id, store_id=store["id"], wx_product_id="w1",
                          skus=[{"sku_id": "s1", "price": 2500, "attrs": "颜色:红色"},
                                {"sku_id": "s2", "price": 3500, "attrs": "颜色:蓝色"}])
        db.add(sp)
        db.flush()
        assert library_sync.sync_prices(db, sp) == 1
        link = ProductSourceLink(tenant_id=lib.tenant_id, store_product_id=sp.id, offer_id="o1", price=800)
        db.add(link)
        db.flush()
        assert library_sync.sync_source(db, link) == 1
        db.commit()
        lib_id = lib.id
    item = client.get(f"/api/library/products/{lib_id}").json()
    assert (item["sale_price_min"], item["sale_price_max"]) == (2500, 3500)
    with SessionLocal() as db:
        lib = db.get(LibraryProduct, lib_id)
        assert lib.sale_prices == {"a": 2500, "b": 3500}
        own = db.query(ProductSourceLink).filter(ProductSourceLink.library_product_id == lib_id).one()
        assert (own.offer_id, own.price) == ("o1", 800)
    # the kept prices are used for the next publish instead of the template formula
    store2, _ = setup_store(client, appid="wx0000000002")
    items = _publish(client, store2["id"], [lib_id])
    assert items[0]["status"] == "success"
    from .conftest import FakeWeChat
    body = [f for f in FakeWeChat.instances if f.added][-1].added[-1]
    assert [s["sale_price"] for s in body["skus"]] == [2500, 3500]
    assert client.delete(f"/api/library/products/{lib_id}/sale-prices").json()["ok"]


def test_source_price_change_reaches_every_store(client):
    """货源价 changed in one store → 商品云库 → the same product in the account's other stores."""
    s1, _ = setup_store(client, appid="wx0000000001")
    s2, _ = setup_store(client, appid="wx0000000002")
    upload(client, {"1.json": make_package("1", "伸缩款汽车除雾刷")})
    with SessionLocal() as db:
        lib = db.query(LibraryProduct).one()
        sps = []
        for st, wx in ((s1, "a1"), (s2, "a2")):
            db.add(PublishedRecord(store_id=st["id"], source_key="wechat:1", title_hash="h", wx_product_id=wx))
            sp = StoreProduct(tenant_id=lib.tenant_id, store_id=st["id"], wx_product_id=wx, skus=[])
            db.add(sp)
            db.flush()
            db.add(ProductSourceLink(tenant_id=lib.tenant_id, store_product_id=sp.id, offer_id="o1", price=500))
            sps.append(sp.id)
        db.commit()
        lib_id = lib.id
    assert client.post("/api/sourcing/price", json={"store_product_id": sps[0], "price": 6.5}).status_code == 200
    with SessionLocal() as db:
        prices = {l.store_product_id or f"lib{l.library_product_id}": l.price for l in db.query(ProductSourceLink)}
    assert prices == {sps[0]: 650, sps[1]: 650, f"lib{lib_id}": 650}


def test_backfill_store_sources_to_library_and_other_stores(client):
    """货源 set on a store product before the write-back: written to 商品云库 at start-up, so the same
    product in the other store shows it too; 商品管理 also shows the 云库 price."""
    from app.services.library_sync import backfill_sources
    s1, _ = setup_store(client, appid="wx0000000001")
    s2, _ = setup_store(client, appid="wx0000000002")
    upload(client, {"1.json": make_package("1", "伸缩款汽车除雾刷")})
    with SessionLocal() as db:
        lib = db.query(LibraryProduct).one()
        sps = {}
        for st, wx in ((s1, "a1"), (s2, "a2")):
            db.add(PublishedRecord(store_id=st["id"], source_key="wechat:1", title_hash="h", wx_product_id=wx))
            sp = StoreProduct(tenant_id=lib.tenant_id, store_id=st["id"], wx_product_id=wx, title=wx, skus=[])
            db.add(sp)
            db.flush()
            sps[wx] = sp.id
        db.add(ProductSourceLink(tenant_id=lib.tenant_id, store_product_id=sps["a1"], offer_id="o1", price=478))
        db.commit()
        assert backfill_sources(db) == 1 and backfill_sources(db) == 0
        lib_price = (lib.price_min, lib.price_max)
    items = {p["wx_product_id"]: p for p in client.get("/api/products").json()["items"]}
    assert items["a2"]["source"]["price"] == 478 and items["a2"]["source"]["from_library"]  # the other store
    assert (items["a2"]["library_price_min"], items["a2"]["library_price_max"]) == lib_price
    lib_item = client.get("/api/library/products").json()["items"][0]
    assert lib_item["source"]["price"] == 478
