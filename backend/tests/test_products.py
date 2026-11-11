from app.services.wechat import WeChatError

from .conftest import FakeWeChat


class StoreWithProducts(FakeWeChat):
    """A store with 45 products (two pages); product 10007 cannot be deleted."""
    catalog: dict = {}
    calls: list = []

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.catalog = {
            str(10000 + i): {
                "product_id": str(10000 + i), "title": f"测试商品{i}", "status": 5 if i % 3 else 11,
                "head_imgs": [f"https://mmecimage.cn/p/{i}"], "spu_code": f"SPU{i}",
                "skus": [{"sku_id": f"{i}01", "sku_code": f"A{i}", "sale_price": 1990, "stock_num": 10,
                          "sku_attrs": [{"attr_key": "颜色", "attr_value": "红"}]},
                         {"sku_id": f"{i}02", "sku_code": f"B{i}", "sale_price": 2990, "stock_num": 5,
                          "sku_attrs": [{"attr_key": "颜色", "attr_value": "蓝"}]}],
            } for i in range(45)}

    def list_products(self, next_key="", page_size=30):
        ids = sorted(self.catalog)
        start = int(next_key or 0)
        page = ids[start:start + page_size]
        return {"product_ids": page, "next_key": str(start + page_size) if start + page_size < len(ids) else "",
                "total_num": len(ids)}

    def get_product(self, pid):
        return {"product": self.catalog[pid]}

    def update_prices(self, pid, skus):
        self.calls.append(("price", pid, skus))
        for s in self.catalog[pid]["skus"]:
            for n in skus:
                if n["sku_id"] == s["sku_id"]:
                    s["sale_price"] = n["sale_price"]

    def listing(self, pid):
        self.calls.append(("listing", pid))

    def delisting(self, pid):
        self.calls.append(("delisting", pid))

    def delete_product(self, pid):
        if pid == "10007":
            raise WeChatError(10020001, "商品有未完成订单，不能删除")
        self.calls.append(("delete", pid))
        self.catalog.pop(pid)


def _store(client, monkeypatch):
    from app.routers import stores
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    StoreWithProducts.reset()
    return client.post("/api/stores", json={"appid": "wx1234567890", "secret": "good-secret"}).json()


def test_sync_lists_products_per_store(client, monkeypatch):
    store = _store(client, monkeypatch)
    assert client.post("/api/products/sync", json={"store_ids": [store["id"]]}).json()["started"] == [store["id"]]
    summary = client.get("/api/products/stores").json()[0]
    assert summary["count"] == 45 and summary["on_sale"] == 30 and summary["sync_status"] == "done"
    assert "共 45 个商品" in summary["sync_message"]

    page = client.get("/api/products", params={"store_id": store["id"], "size": 50}).json()
    assert page["total"] == 45
    p = [x for x in page["items"] if x["wx_product_id"] == "10001"][0]
    assert p["cover"] == "https://mmecimage.cn/p/1" and p["spu_code"] == "SPU1"
    assert (p["price_min"], p["price_max"], p["stock"]) == (1990, 2990, 15)
    assert p["skus"][0]["sku_code"] == "A1" and p["status_name"] == "上架"
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["total"] == 1
    assert client.get("/api/products", params={"status": 11}).json()["total"] == 15

    # A product deleted in the WeChat back office disappears on the next sync
    StoreWithProducts.catalog.pop("10044")
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    assert client.get("/api/products").json()["total"] == 44


def test_price_listing_and_delete(client, monkeypatch):
    store = _store(client, monkeypatch)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    items = {x["wx_product_id"]: x for x in client.get("/api/products", params={"size": 50}).json()["items"]}

    # Single product, one SKU: other SKUs keep their price
    r = client.post(f"/api/products/{items['10001']['id']}/price", json={"skus": [{"sku_id": "101", "price": 25.5}]})
    assert r.status_code == 200 and r.json()["price_min"] == 2550 and r.json()["price_max"] == 2990
    assert StoreWithProducts.calls[-1] == ("price", "10001", [{"sku_id": "101", "sale_price": 2550},
                                                             {"sku_id": "102", "sale_price": 2990}])

    # Batch: +10%
    ids = [items["10002"]["id"], items["10004"]["id"]]
    res = client.post("/api/products/batch-price", json={"ids": ids, "mode": "multiply", "value": 1.1}).json()
    assert all(x["ok"] for x in res["results"])
    p2 = [x for x in client.get("/api/products", params={"keyword": "SPU2"}).json()["items"]][0]
    assert (p2["price_min"], p2["price_max"]) == (2189, 3289)

    # Delist / list
    client.post("/api/products/batch-action", json={"ids": [items["10001"]["id"]], "action": "delisting"})
    assert ("delisting", "10001") in StoreWithProducts.calls
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]["status"] == 11

    # Delete: on-sale products are delisted first; per-item errors are reported, not fatal
    res = client.post("/api/products/batch-action", json={
        "ids": [items["10004"]["id"], items["10007"]["id"]], "action": "delete"}).json()["results"]
    ok = {r["id"]: r for r in res}
    assert ok[items["10004"]["id"]]["ok"] and not ok[items["10007"]["id"]]["ok"]
    assert "未完成订单" in ok[items["10007"]["id"]]["error"]
    assert ("delisting", "10004") in StoreWithProducts.calls and ("delete", "10004") in StoreWithProducts.calls
    assert client.get("/api/products").json()["total"] == 44


def test_products_are_tenant_isolated(client, monkeypatch):
    store = _store(client, monkeypatch)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    pid = client.get("/api/products").json()["items"][0]["id"]
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as other:
        from .conftest import new_tenant_client
        new_tenant_client(client, other, "owner2")
        assert other.get("/api/products").json()["total"] == 0
        assert other.post(f"/api/products/{pid}/price", json={"skus": [{"sku_id": "1", "price": 1}]}).status_code == 404
        assert other.post("/api/products/sync", json={"store_ids": [store["id"]]}).status_code == 404


def test_library_bulk_delete_also_removes_store_products(client, monkeypatch):
    """Unavailable source products are deleted from the library and their WeChat copies deleted/delisted."""
    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload

    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "已断货商品一"), "2.json": make_package("2", "已断货商品二"),
                    "3.json": make_package("3", "还在卖的商品")})
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])  # publishes all three -> wx ids 10001..10003
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    assert all(i["published"] == 1 for i in lib.values())

    from app.routers import stores
    StoreWithProducts.reset()
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    for wx in ("10001", "10002", "10003"):
        StoreWithProducts.catalog[wx] = {"product_id": wx, "title": wx, "status": 5, "skus": []}

    res = client.post("/api/library/products/delete", json={
        "ids": [lib["1"]["id"], lib["2"]["id"]], "store_action": "delete"}).json()
    assert res["deleted"] == 2 and len(res["store_results"]) == 2 and all(r["ok"] for r in res["store_results"])
    assert ("delisting", "10001") in StoreWithProducts.calls and ("delete", "10002") in StoreWithProducts.calls
    assert "10003" in StoreWithProducts.catalog  # untouched
    left = client.get("/api/library/products").json()["items"]
    assert [i["source_product_id"] for i in left] == ["3"]

    # store_action=delisting only takes it off sale and keeps the publish record
    res = client.post("/api/library/products/delete", json={"ids": [left[0]["id"]], "store_action": "delisting"}).json()
    assert res["store_results"][0]["ok"] and ("delisting", "10003") in StoreWithProducts.calls
    assert ("delete", "10003") not in StoreWithProducts.calls


def test_stock_edit_syncs_to_wechat(client, monkeypatch):
    store = _store(client, monkeypatch)
    calls = []
    monkeypatch.setattr(StoreWithProducts, "update_stock",
                        lambda self, pid, sku, diff_type, num: calls.append((pid, sku, diff_type, num)), raising=False)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    p = client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]
    assert p["stock"] == 15

    # 改库存: set per SKU
    r = client.post(f"/api/products/{p['id']}/stock", json={"skus": [{"sku_id": "101", "stock": 100}]}).json()
    assert calls == [("10001", "101", 3, 100)] and r["stock"] == 105
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]["skus"][0]["stock"] == 100
    assert client.post(f"/api/products/{p['id']}/stock", json={"skus": [{"sku_id": "x", "stock": 1}]}).status_code == 400

    # 批量改库存: add / subtract / set on every SKU
    other = client.get("/api/products", params={"keyword": "SPU2"}).json()["items"][0]
    calls.clear()
    res = client.post("/api/products/batch-stock", json={"ids": [p["id"], other["id"]], "mode": "add", "value": 10}).json()
    assert all(r["ok"] for r in res["results"]) and len(calls) == 4 and {c[2] for c in calls} == {1}
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]["stock"] == 125
    client.post("/api/products/batch-stock", json={"ids": [p["id"]], "mode": "sub", "value": 200})
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]["stock"] == 0
    client.post("/api/products/batch-stock", json={"ids": [p["id"]], "mode": "set", "value": 0})
    assert calls[-1][2:] == (3, 0)
    assert client.post("/api/products/batch-stock", json={"ids": [p["id"]], "mode": "add", "value": 0}).status_code == 400


def test_stock_error_reported_per_product(client, monkeypatch):
    store = _store(client, monkeypatch)

    def fail(self, pid, sku, diff_type, num):
        raise WeChatError(10020050, "商品未上架")
    monkeypatch.setattr(StoreWithProducts, "update_stock", fail, raising=False)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    p = client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]
    r = client.post(f"/api/products/{p['id']}/stock", json={"skus": [{"sku_id": "101", "stock": 5}]})
    assert r.status_code == 400 and "商品未上架" in r.json()["detail"]
    res = client.post("/api/products/batch-stock", json={"ids": [p["id"]], "mode": "set", "value": 5}).json()
    assert res["results"][0]["ok"] is False and "商品未上架" in res["results"][0]["error"]
    assert client.get("/api/products", params={"keyword": "SPU1"}).json()["items"][0]["stock"] == 15
