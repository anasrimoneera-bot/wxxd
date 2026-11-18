"""货源 kept on products: shown in 商品管理 / 商品云库, 修改货源价, 取消货源. 货源 are set from the
1688 货源助手 browser extension (/api/ext/link)."""
PRICES = {"111": 26.8, "222": 29.8}


def _link(client, kind, pid, offer_id):
    r = client.post("/api/ext/link", json={"kind": kind, "id": pid, "offer": {
        "offer_id": offer_id, "title": f"货源{offer_id}", "image_url": "https://cbu01.alicdn.com/x.jpg",
        "supplier": "厂家", "price": PRICES[offer_id]}})
    assert r.status_code == 200, r.text


def test_library_source_shows_in_product_management_after_upload(client, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库找到货源"), "2.json": make_package("2", "云库没找货源")})
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    # 找货源 in 商品云库 before uploading
    _link(client, "library", lib["1"]["id"], "111")

    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])  # -> wx ids 10001 (product 1), 10002 (product 2)
    StoreWithProducts.reset()
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    StoreWithProducts.catalog = {wx: {"product_id": wx, "title": wx, "status": 5, "head_imgs": [f"https://mmecimage.cn/{wx}"],
                                      "skus": []} for wx in ("10001", "10002")}
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})

    sp = {p["wx_product_id"]: p for p in client.get("/api/products").json()["items"]}
    assert sp["10001"]["source"]["price"] == 2680 and sp["10001"]["source"]["from_library"] is True
    assert sp["10001"]["source_state"] == "linked" and sp["10002"]["source"] is None
    assert client.get("/api/products", params={"source": "linked"}).json()["total"] == 1

    # Changing the source in 商品云库 follows through; a source set in 商品管理 takes precedence
    _link(client, "library", lib["1"]["id"], "222")
    assert client.get("/api/products", params={"keyword": "10001"}).json()["items"][0]["source"]["price"] == 2980
    _link(client, "store", sp["10001"]["id"], "111")
    src = client.get("/api/products", params={"keyword": "10001"}).json()["items"][0]["source"]
    assert src["price"] == 2680 and "from_library" not in src


def test_cancel_source(client, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库商品"), "2.json": make_package("2", "另一个商品")})
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    for pid in ("1", "2"):
        _link(client, "library", lib[pid]["id"], "111")

    # From 商品云库
    assert client.post("/api/sourcing/unlink", json={"library_product_id": lib["1"]["id"]}).json()["ok"]
    assert client.post("/api/sourcing/unlink", json={"library_product_id": lib["1"]["id"]}).status_code == 404

    # From 商品管理, for a source that came from 商品云库: the 商品云库 source is cancelled
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])
    StoreWithProducts.reset()
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    StoreWithProducts.catalog = {wx: {"product_id": wx, "title": wx, "status": 5, "skus": []} for wx in ("10001", "10002")}
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    sp = [p for p in client.get("/api/products").json()["items"] if p["source"]][0]
    r = client.post("/api/sourcing/unlink", json={"store_product_id": sp["id"]}).json()
    assert r["from_library"] is True
    assert all(p["source"] is None for p in client.get("/api/products").json()["items"])
    assert all(i["source"] is None for i in client.get("/api/library/products").json()["items"])


def test_edit_source_price_keeps_link(client, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库商品")})
    lib = client.get("/api/library/products").json()["items"][0]
    _link(client, "library", lib["id"], "111")

    # 商品云库
    r = client.post("/api/sourcing/price", json={"library_product_id": lib["id"], "price": 23.5}).json()
    assert r["price"] == 2350 and r["offer_id"] == "111" and r["from_library"] is False
    assert r["price_edited"] is True and r["original_price"] == 2680
    # Editing again keeps the 1688 price; setting it back to that price clears the mark
    r = client.post("/api/sourcing/price", json={"library_product_id": lib["id"], "price": 22}).json()
    assert r["original_price"] == 2680
    assert client.post("/api/sourcing/price", json={"library_product_id": lib["id"], "price": 26.8}).json()["price_edited"] is False
    client.post("/api/sourcing/price", json={"library_product_id": lib["id"], "price": 23.5})
    assert client.get("/api/library/products", params={}).json()["items"][0]["source"]["price_edited"] is True
    src = client.get("/api/library/products").json()["items"][0]["source"]
    assert src["price"] == 2350 and src["detail_url"].endswith("111.html")
    assert client.post("/api/sourcing/price", json={"library_product_id": lib["id"], "price": 0}).status_code == 422

    # 商品管理, source inherited from 商品云库: the same source changes
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])
    StoreWithProducts.reset()
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    StoreWithProducts.catalog = {"10001": {"product_id": "10001", "title": "x", "status": 5, "skus": []}}
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    sp = client.get("/api/products").json()["items"][0]
    r = client.post("/api/sourcing/price", json={"store_product_id": sp["id"], "price": 19.9}).json()
    assert r["from_library"] is True and r["price"] == 1990
    assert client.get("/api/products").json()["items"][0]["source"]["price"] == 1990
    assert client.get("/api/library/products").json()["items"][0]["source"]["offer_id"] == "111"
    assert client.get("/api/products").json()["items"][0]["source"]["price_edited"] is True
    # Choosing an offer again (设为货源) resets to the 1688 price, unmarked
    _link(client, "library", lib["id"], "111")
    src = client.get("/api/library/products").json()["items"][0]["source"]
    assert src["price"] == 2680 and src["price_edited"] is False

    # No source set
    client.post("/api/sourcing/unlink", json={"library_product_id": lib["id"]})
    assert client.post("/api/sourcing/price", json={"store_product_id": sp["id"], "price": 5}).status_code == 404
