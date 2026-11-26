"""1688 货源助手 (browser extension) API: queue, item, image link, 设为货源 with
per-spec 货源价 and 运费."""
import io

from PIL import Image

from app.db import SessionLocal
from app.models import LibraryProduct, PublishedRecord, Store, StoreProduct

from .test_flow import setup_store


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(b, format="PNG")
    return b.getvalue()


def _store_product(store_id):
    with SessionLocal() as db:
        s = db.get(Store, store_id)
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w1", title="法式蕾丝半身裙",
                          cover="https://img.example/main.jpg", price_min=7900, price_max=7900,
                          skus=[{"sku_id": "k1", "price": 7900, "attrs": "颜色:杏色加长款；尺码:S 75-90"},
                                {"sku_id": "k2", "price": 7900, "attrs": "颜色:杏色加长款；尺码:M 90-105"}])
        db.add(sp)
        db.commit()
        return sp.id


def test_queue_item_and_link(client):
    store, _ = setup_store(client)
    sp_id = _store_product(store["id"])
    q = client.get("/api/ext/queue").json()
    assert [i["id"] for i in q["items"]] == [sp_id]
    assert q["items"][0]["specs"][0]["label"] == "杏色加长款 / S 75-90"
    r = client.post("/api/ext/link", json={
        "kind": "store", "id": sp_id, "freight": 5.5, "sku_prices": {"k1": 39.9, "k2": 41.9},
        "offer": {"offer_id": "777", "title": "法式蕾丝刺绣拼接半身裙", "price": 39.9, "supplier": "江阴市慕格雅服饰商行",
                  "image_url": "https://cbu01.alicdn.com/x.jpg"}}).json()
    s = r["source"]
    assert (s["price"], s["sku_prices"], s["freight"]) == (3990, {"k1": 3990, "k2": 4190}, 550)
    assert s["detail_url"] == "https://detail.1688.com/offer/777.html"
    assert client.get("/api/ext/queue").json()["items"] == []  # has a 货源 now
    item = client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()
    assert item["source"]["offer_id"] == "777" and "vision" not in item
    # 公式改价: 货源成本 = 货源价 (the plugin sends it with the 运费 already in)
    p = client.post("/api/products/formula-preview", json={"ids": [sp_id], "margin": 2}).json()["items"][0]
    assert [k["source"] for k in p["skus"]] == [3990, 4190]


def test_public_image_link_for_1688(client, monkeypatch):
    store, _ = setup_store(client)
    sp_id = _store_product(store["id"])
    item = client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()
    path = item["image_proxy"]
    assert path.startswith(f"/api/ext/img/store/{sp_id}/") and path.endswith(".jpg")
    monkeypatch.setattr("app.services.wechat.download", lambda url, timeout=60: _png())
    client.headers.pop("Authorization", None)  # 1688 fetches it without logging in
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.head(path).status_code == 200  # some image fetchers ask with HEAD first
    monkeypatch.setattr("app.services.wechat.download", lambda url, timeout=60: (_ for _ in ()).throw(AssertionError("cached")))
    assert client.get(path).content == r.content  # served from the disk cache
    assert client.get(path.replace(path.split("/")[-1], "0" * 20 + ".jpg")).status_code == 404  # wrong signature


def test_skip_marks_no_source_and_leaves_the_queue(client):
    store, _ = setup_store(client)
    sp_id = _store_product(store["id"])
    assert [i["id"] for i in client.get("/api/ext/queue").json()["items"]] == [sp_id]
    assert client.post("/api/ext/skip", json={"kind": "store", "id": sp_id}).json()["ok"]
    assert client.get("/api/ext/queue").json()["items"] == []
    assert client.get("/api/products", params={"source": "none"}).json()["total"] == 1
    assert client.get("/api/products", params={"source": "unsearched"}).json()["total"] == 0
    # a 货源 set later clears the mark
    client.post("/api/ext/link", json={"kind": "store", "id": sp_id, "offer": {"offer_id": "1", "price": 3}})
    assert client.get("/api/products").json()["items"][0]["source_state"] == "linked"
    client.post("/api/sourcing/unlink", json={"store_product_id": sp_id})
    assert [i["id"] for i in client.get("/api/ext/queue").json()["items"]] == [sp_id]


def test_claims_keep_two_accounts_off_the_same_products(client):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import PublishedRecord

    from .conftest import new_tenant_client
    store, _ = setup_store(client)
    a = _store_product(store["id"])
    with SessionLocal() as db:  # two more: b, and c published from the same source product as b
        s = db.get(Store, store["id"])
        s2 = Store(tenant_id=s.tenant_id, name="二店", appid="wx2", secret_enc=s.secret_enc)  # the same product in another store
        db.add(s2)
        db.flush()
        ids = []
        for st, wx in ((s, "w2"), (s2, "w3")):
            sp = StoreProduct(tenant_id=s.tenant_id, store_id=st.id, wx_product_id=wx, title=wx, cover="x", skus=[])
            db.add(sp)
            db.flush()
            db.add(PublishedRecord(store_id=st.id, source_key="1688:999", title_hash="h" + wx, wx_product_id=wx))
            ids.append(sp.id)
        db.commit()
        s2_id = s2.id
    b, c = ids
    q = lambda cl: sorted(i["id"] for i in cl.get("/api/ext/queue").json()["items"])  # noqa: E731
    assert q(client) in ([a, b], [a, c])  # b and c are one sourcing job: listed once
    with TestClient(app) as other:
        new_tenant_client(client, other, "worker2")
        tid = next(t["id"] for t in client.get("/api/admin/tenants").json() if t["username"] == "worker2")
        client.post(f"/api/admin/tenants/{tid}/stores", json={"store_ids": [store["id"], s2_id]})
        r = client.post("/api/ext/claim", json={"items": [{"kind": "store", "id": a}, {"kind": "store", "id": b}]}).json()
        assert sorted(r["claimed"]) == sorted([f"store:{a}", f"store:{b}"]) and r["taken"] == []
        assert q(other) == []  # both jobs are claimed by owner1
        r = other.post("/api/ext/claim", json={"items": [{"kind": "store", "id": c}]}).json()
        assert r["claimed"] == [] and r["taken"][0]["by"] == "owner1"  # c is b's job
        assert q(client) in ([a, b], [a, c])  # the owner of the claims still sees them
        # 换商品 by its 微信商品 ID: not a product another account is on (w3 = c, b's job)
        r = other.get("/api/ext/by-wx", params={"product_id": "w3"})
        assert r.status_code == 409 and "owner1" in r.json()["detail"]
        assert client.get("/api/ext/by-wx", params={"product_id": "w3"}).status_code == 200  # the owner may
        client.post("/api/ext/release", json={"items": [{"kind": "store", "id": b}]})
        assert q(other) in ([b], [c])
        client.post("/api/ext/skip", json={"kind": "store", "id": a})  # skipping ends the claim (and the job)
        assert a not in q(other)


def test_spec_images_and_remove_spec(client, monkeypatch):
    """货源助手: each spec's picture; a spec the 货源 lacks is removed (stock 0 in the store, hidden here)."""
    from app.routers import stores
    from app.services import product_sync
    from tests.conftest import FakeWeChat
    store, _ = setup_store(client)
    sp_id = _store_product(store["id"])
    calls = []

    class SkuWeChat(FakeWeChat):
        def get_product(self, pid):
            return {"product": {"product_id": pid, "title": "法式蕾丝半身裙", "status": 5, "skus": [
                {"sku_id": "k1", "sale_price": 7900, "stock_num": 5, "thumb_img": "https://img/k1.jpg",
                 "sku_attrs": [{"attr_key": "颜色", "attr_value": "杏色加长款"}, {"attr_key": "尺码", "attr_value": "S 75-90"}]},
                {"sku_id": "k2", "sale_price": 7900, "stock_num": 6, "thumb_img": "https://img/k2.jpg",
                 "sku_attrs": [{"attr_key": "颜色", "attr_value": "杏色加长款"}, {"attr_key": "尺码", "attr_value": "M 90-105"}]}]}}

        def update_stock(self, product_id, sku_id, diff_type, num):
            calls.append((product_id, sku_id, diff_type, num))

    monkeypatch.setattr(stores, "client_factory", SkuWeChat)
    item = client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()  # pictures read once
    assert [x["image"] for x in item["specs"]] == ["https://img/k1.jpg", "https://img/k2.jpg"]
    r = client.post("/api/ext/sku-remove", json={"kind": "store", "id": sp_id, "sku_id": "k2"}).json()
    assert r["ok"] and r["specs"] == 1 and calls == [("w1", "k2", 3, 0)]
    assert [x["key"] for x in client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()["specs"]] == ["k1"]
    prod = next(x for x in client.get("/api/products").json()["items"] if x["id"] == sp_id)
    assert [k["sku_id"] for k in prod["skus"]] == ["k1"]
    # a re-sync keeps it removed; the last spec cannot be removed
    with SessionLocal() as db:
        product_sync.upsert(db, db.get(Store, store["id"]), product_sync.parse_product(SkuWeChat("a", "b").get_product("w1")))
        db.commit()
    assert [x["key"] for x in client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()["specs"]] == ["k1"]
    assert client.post("/api/ext/sku-remove", json={"kind": "store", "id": sp_id, "sku_id": "k1"}).status_code == 400
    # several at once: all but one can go, never every spec
    assert client.post("/api/ext/sku-remove", json={"kind": "store", "id": sp_id, "sku_ids": ["k1", "k2"]}).status_code == 400


def test_remove_several_specs(client, monkeypatch):
    from app.routers import stores
    from tests.conftest import FakeWeChat
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w9", title="衣架", cover="x",
                          skus=[{"sku_id": f"k{i}", "price": 100, "stock": 9, "attrs": f"尺寸:{i}个装", "thumb": ""} for i in (1, 2, 3)])
        db.add(sp)
        db.commit()
        sp_id = sp.id
    calls = []

    class W(FakeWeChat):
        def update_stock(self, product_id, sku_id, diff_type, num):
            calls.append(sku_id)

    monkeypatch.setattr(stores, "client_factory", W)
    r = client.post("/api/ext/sku-remove", json={"kind": "store", "id": sp_id, "sku_ids": ["k2", "k3"]}).json()
    assert r["removed"] == ["k2", "k3"] and r["specs"] == 1 and calls == ["k2", "k3"]
    assert [x["key"] for x in client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()["specs"]] == ["k1"]


def test_auto_price_after_link(client, monkeypatch):
    """设为货源 → 自动改价 with the account's preset 公式 (off by default)."""
    from app.routers import stores
    from tests.conftest import FakeWeChat
    pushed = []

    class P(FakeWeChat):
        def update_prices(self, pid, skus):
            pushed.append((pid, skus))

    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", P)
    sp_id = _store_product(store["id"])
    body = {"kind": "store", "id": sp_id, "freight": 0, "sku_prices": {"k1": 10, "k2": 12},
            "offer": {"offer_id": "777", "title": "裙", "price": 10, "supplier": "x", "image_url": "https://cbu01.alicdn.com/x.jpg"}}
    assert client.post("/api/ext/link", json=body).json()["auto_price"] == [] and pushed == []  # off
    assert client.get("/api/products/auto-price").json() == {"enabled": False, "margin": 2, "add": 0}
    client.put("/api/products/auto-price", json={"enabled": True, "margin": 2, "add": 1})
    r = client.post("/api/ext/link", json=body).json()["auto_price"]
    assert r[0]["ok"] and (r[0]["min"], r[0]["max"]) == (2100, 2500)  # 10 × 2 + 1, 12 × 2 + 1
    assert pushed[-1] == ("w1", [{"sku_id": "k1", "sale_price": 2100}, {"sku_id": "k2", "sale_price": 2500}])
    prod = next(x for x in client.get("/api/products").json()["items"] if x["id"] == sp_id)
    assert (prod["price_min"], prod["price_max"]) == (2100, 2500)


def test_queue_on_sale_first(client):
    """找货源: products 上架 in the store come first, then 未上架 / 草稿箱; each shows its 商品ID."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        for wx, st in (("d1", 0), ("s1", 5), ("d2", 11), ("s2", 5)):
            db.add(StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id=wx, title=wx, cover="x", skus=[], status=st))
        db.commit()
    items = client.get("/api/ext/queue").json()["items"]
    assert [i["wx_product_id"] for i in items] == ["s2", "s1", "d2", "d1"]
    assert [i["on_sale"] for i in items] == [True, True, False, False]


def test_exposure_from_store_page_orders_queue(client):
    """曝光 read on the 小店后台 商品列表 → kept on the products; 找货源 lists the most 曝光 first."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        for wx in ("10001931893717", "10001932018959", "10001931943395"):
            db.add(StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id=wx, title=wx, cover="x", skus=[], status=5))
        db.commit()
    r = client.post("/api/ext/exposure", json={"items": [
        {"product_id": "10001931893717", "exposure": 639}, {"product_id": "10001932018959", "exposure": 6},
        {"product_id": "10001931943395", "exposure": 188}, {"product_id": "99999999999", "exposure": 5}]}).json()
    assert r["matched"] == 3 and r["unknown"] == ["99999999999"]
    # the exposed ones still without a 货源, most 曝光 first
    assert [i["wx_product_id"] for i in r["need_source"]] == ["10001931893717", "10001931943395", "10001932018959"]
    items = client.get("/api/ext/queue").json()["items"]
    assert [i["wx_product_id"] for i in items] == ["10001931893717", "10001931943395", "10001932018959"]
    assert items[0]["exposure"] == 639
    prods = client.get("/api/products", params={"sort": "exposure"}).json()["items"]
    assert prods[0]["exposure_30d"] == 639


def test_priority_from_exposure_in_any_store(client):
    """曝光 of the item in any store makes its 找货源 优先 in every store it is listed in (and in 商品云库);
    products another account is on stay out."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        s2 = Store(tenant_id=s.tenant_id, name="二店", appid="wx2", secret_enc=s.secret_enc)
        db.add(s2)
        db.flush()
        lib = LibraryProduct(tenant_id=s.tenant_id, platform="1688", source_product_id="555", title="衣架", cover="x",
                             normalized={"skus": []}, raw="{}")
        db.add(lib)
        for st, wx, exp in ((s, "a1", 900), (s2, "b1", None), (s2, "b2", None), (s2, "b3", 5)):
            db.add(StoreProduct(tenant_id=s.tenant_id, store_id=st.id, wx_product_id=wx, title=wx, cover="x", skus=[],
                                status=5, exposure_30d=exp))
        db.add_all([PublishedRecord(store_id=s.id, source_key="1688:555", title_hash="h1", wx_product_id="a1"),
                    PublishedRecord(store_id=s2.id, source_key="1688:555", title_hash="h2", wx_product_id="b1")])
        db.commit()
        s2_id = s2.id
    items = client.get("/api/ext/queue", params={"store_id": s2_id}).json()["items"]
    # b1 is the same item as a1 (900 曝光 in the other store): first, 优先; then b3 (5); then b2
    assert [i["wx_product_id"] for i in items] == ["b1", "b3", "b2"]
    assert items[0]["priority"] and items[0]["exposure"] == 900 and not items[2]["priority"]
    lib_items = client.get("/api/ext/queue", params={"kind": "library"}).json()["items"]
    assert lib_items[0]["title"] == "衣架" and lib_items[0]["priority"]


def test_two_stages_found_skip_zero_stock(client, monkeypatch):
    """第一阶段: 有货源 → 第二阶段 list; 跳过 → 无货源 + stock 0 in every store it is listed in."""
    from app.routers import stores
    from tests.conftest import FakeWeChat
    calls = []

    class W(FakeWeChat):
        def update_stock(self, product_id, sku_id, diff_type, num):
            calls.append((product_id, sku_id, diff_type, num))

    store, _ = setup_store(client)
    monkeypatch.setattr(stores, "client_factory", W)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        s2 = Store(tenant_id=s.tenant_id, name="二店", appid="wx2", secret_enc=s.secret_enc)
        db.add(s2)
        db.flush()
        db.add(LibraryProduct(tenant_id=s.tenant_id, platform="1688", source_product_id="777", title="锅", cover="x",
                              normalized={"skus": []}, raw="{}"))
        ids = {}
        for st, wx in ((s, "f1"), (s, "k1"), (s2, "k2")):
            sp = StoreProduct(tenant_id=s.tenant_id, store_id=st.id, wx_product_id=wx, title=wx, cover="x",
                              status=0 if wx == "k2" else 5,  # k2: in the 草稿箱 of 二店
                              skus=[{"sku_id": "a", "stock": 9, "price": 100, "attrs": ""}, {"sku_id": "b", "stock": 0, "price": 100, "attrs": ""}])
            db.add(sp)
            db.flush()
            ids[wx] = sp.id
        # k1 / k2: the same item (published from 1688:777) in two stores
        db.add_all([PublishedRecord(store_id=s.id, source_key="1688:777", title_hash="h1", wx_product_id="k1"),
                    PublishedRecord(store_id=s2.id, source_key="1688:777", title_hash="h2", wx_product_id="k2")])
        db.commit()
    q = lambda stage: sorted(i["wx_product_id"] for i in client.get("/api/ext/queue", params={"stage": stage}).json()["items"])  # noqa: E731
    assert q(1) in (["f1", "k1"], ["f1", "k2"]) and q(2) == []  # k1 / k2: one job, listed once
    client.post("/api/ext/found", json={"kind": "store", "id": ids["f1"], "offer_id": "650000000001", "price": 3.5, "title": "锅"})
    assert q(1) in (["k1"], ["k2"]) and q(2) == ["f1"]
    assert client.get("/api/ext/queue", params={"stage": 2}).json()["items"][0]["hint"]["offer_id"] == "650000000001"
    assert ids["k2"] in client.get("/api/products/draft-ids").json()
    r = client.post("/api/ext/skip", json={"kind": "store", "id": ids["k1"]}).json()
    # 上架 k1: stock 0 (spec b had 0 already); 草稿箱 k2: WeChat would refuse it — not counted as failed
    assert (r["zeroed"], r["not_listed"], r["failed"]) == (1, 1, []) and calls == [("k1", "a", 3, 0)]
    assert q(1) == [] and q(2) == ["f1"]  # k2 is 无货源 too
    with SessionLocal() as db:
        assert db.get(StoreProduct, ids["k1"]).stock == 0 and db.get(StoreProduct, ids["k2"]).no_source
    assert ids["k2"] not in client.get("/api/products/draft-ids").json()  # 发布草稿箱 leaves 无货源 ones

    # 找货源统计: 有货源 / 跳过 per account per day, with 明细
    st = client.get("/api/ext/sourcing-stats").json()
    assert len(st["days"]) == 7 and st["days"][0] == st["today"] and st["total"] == 2
    u = st["users"][0]
    assert (u["total"], u["today"], u["有货源"], u["跳过"], u["设货源"]) == (2, 2, 1, 1, 0)
    assert u["days"][st["today"]] == {"total": 2, "设货源": 0, "有货源": 1, "跳过": 1}
    logs = client.get("/api/ext/sourcing-logs", params={"user_id": u["user_id"], "date": st["today"]}).json()
    assert [(x["action"], x["wx_product_id"]) for x in logs] == [("跳过", "k1"), ("有货源", "f1")]
    assert client.get("/api/ext/sourcing-stats", params={"start": "2020-01-01", "end": "2020-01-02"}).json()["total"] == 0
    assert client.get("/api/ext/sourcing-stats", params={"start": "2020-01-01", "end": "2021-01-02"}).status_code == 400
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.conftest import new_tenant_client
    with TestClient(app) as other:  # only the platform admin sees it
        new_tenant_client(client, other, "cust9")
        assert other.get("/api/ext/sourcing-stats").status_code == 403
        assert other.get("/api/ext/sourcing-logs", params={"user_id": u["user_id"]}).status_code == 403


def test_library_exposure_filter_and_confirm_best(client):
    """商品云库: 有曝光 filter (most 曝光 first) and 已确认最优 on the 货源 (cleared by another link)."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        libs = {}
        for sid, exp in (("100", 50), ("200", 800), ("300", None)):
            lp = LibraryProduct(tenant_id=s.tenant_id, platform="1688", source_product_id=sid, title=sid, cover="x",
                                normalized={"skus": []}, raw="{}")
            db.add(lp)
            db.add(StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w" + sid, title=sid, cover="x",
                                skus=[], status=5, exposure_30d=exp))
            db.add(PublishedRecord(store_id=s.id, source_key=f"1688:{sid}", title_hash="h" + sid, wx_product_id="w" + sid))
            db.flush()
            libs[sid] = lp.id
        db.commit()
    r = client.get("/api/library/products", params={"exposure": True}).json()
    assert r["exposed_count"] == 2 and [x["source_product_id"] for x in r["items"]] == ["200", "100"]
    assert r["items"][0]["exposure"] == 800
    assert client.get("/api/ext/item", params={"kind": "library", "id": libs["200"]}).json()["priority"]
    offer = {"offer_id": "650000000001", "title": "锅", "price": 3, "supplier": "x", "image_url": "https://cbu01.alicdn.com/x.jpg"}
    client.post("/api/ext/link", json={"kind": "library", "id": libs["200"], "sku_prices": {}, "freight": 0, "offer": offer})
    c = client.post("/api/sourcing/confirm", json={"library_product_id": libs["200"]}).json()
    assert c["source"]["confirmed"] and c["source"]["confirmed_by"]
    row = next(x for x in client.get("/api/library/products", params={"exposure": True}).json()["items"] if x["source_product_id"] == "200")
    assert row["source"]["confirmed"]
    # another link: 已确认最优 is gone
    client.post("/api/ext/link", json={"kind": "library", "id": libs["200"], "sku_prices": {}, "freight": 0,
                                       "offer": {**offer, "offer_id": "650000000002"}})
    row = next(x for x in client.get("/api/library/products").json()["items"] if x["source_product_id"] == "200")
    assert not row["source"]["confirmed"]


def test_plugin_shows_only_main_spec(client):
    """主规格: the plugin shows a 商品云库 product's named (best-selling) spec only, and for a store product
    published from it (as 款式1–款式3) only the store spec that matches it."""
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        skus = [{"source_sku_id": "a", "price": 900, "attrs": []},
                {"source_sku_id": "b", "price": 1200, "attrs": [{"name": "颜色", "value": "红"}]},
                {"source_sku_id": "c", "price": 1500, "attrs": []}]
        lp = LibraryProduct(tenant_id=s.tenant_id, platform="1688", source_product_id="55", title="杯子", cover="x",
                            normalized={"skus": skus}, raw="{}")
        sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="w55", title="杯子", cover="x", status=5,
                          skus=[{"sku_id": f"s{i}", "price": 2000, "stock": 5, "attrs": f"规格:款式{i}"} for i in (1, 2, 3)])
        db.add_all([lp, sp, PublishedRecord(store_id=s.id, source_key="1688:55", title_hash="h55", wx_product_id="w55")])
        db.commit()
        lib_id, sp_id = lp.id, sp.id
    lib_item = client.get("/api/ext/item", params={"kind": "library", "id": lib_id}).json()
    assert [k["key"] for k in lib_item["specs"]] == ["b"]
    store_item = client.get("/api/ext/item", params={"kind": "store", "id": sp_id}).json()
    assert [k["key"] for k in store_item["specs"]] == ["s2"]  # 款式2 = the 2nd library spec, the named one
    assert client.get(f"/api/library/products/{lib_id}").json()["normalized"]["skus"][0]["source_sku_id"] == "b"


def test_zero_non_main_specs(client, monkeypatch):
    """其余规格清零: the specs besides the 主规格 get stock 0 in WeChat and are hidden; 草稿箱 ones only hidden."""
    from app.routers import stores
    from tests.conftest import FakeWeChat
    calls = []

    class W(FakeWeChat):
        def update_stock(self, product_id, sku_id, diff_type, num):
            calls.append((product_id, sku_id, num))
    monkeypatch.setattr(stores, "client_factory", W)
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        skus = [{"source_sku_id": "a", "price": 900, "attrs": []},
                {"source_sku_id": "b", "price": 1200, "attrs": [{"name": "颜色", "value": "红"}]}]
        db.add(LibraryProduct(tenant_id=s.tenant_id, platform="1688", source_product_id="66", title="碗", cover="x",
                              normalized={"skus": skus}, raw="{}"))
        s2 = Store(tenant_id=s.tenant_id, name="二店", appid="wx2", secret_enc=s.secret_enc)
        db.add(s2)
        db.flush()
        ids = {}
        for wx, status, st in (("on", 5, s), ("draft", 0, s2)):  # the same item in two stores
            sp = StoreProduct(tenant_id=s.tenant_id, store_id=st.id, wx_product_id=wx, title=wx, cover="x", status=status,
                              skus=[{"sku_id": "x1", "price": 2000, "stock": 5, "attrs": "规格:款式1"},
                                    {"sku_id": "x2", "price": 2000, "stock": 5, "attrs": "规格:款式2"}])
            db.add(sp)
            db.add(PublishedRecord(store_id=st.id, source_key="1688:66", title_hash="h" + wx, wx_product_id=wx))
            db.flush()
            ids[wx] = sp.id
        db.add(StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id="own", title="自己的", cover="x", status=5,
                            skus=[{"sku_id": "y1", "price": 1, "stock": 1, "attrs": "a:1"}, {"sku_id": "y2", "price": 1, "stock": 1, "attrs": "a:2"}]))
        db.commit()
    r = client.get("/api/ext/non-main").json()
    assert sorted(r["ids"]) == sorted(ids.values()) and r["specs"] == 2  # the product not from 商品云库 is left alone
    res = {x["id"]: x for x in client.post("/api/ext/non-main/zero", json={"ids": r["ids"]}).json()["results"]}
    assert calls == [("on", "x1", 0)]  # only the 上架 one in WeChat; 款式2 = the named 主规格 stays
    assert res[ids["on"]]["zeroed"] == 1 and res[ids["draft"]]["not_listed"] and res[ids["draft"]]["hidden"] == 1
    assert client.get("/api/ext/non-main").json()["ids"] == []
    item = client.get("/api/ext/item", params={"kind": "store", "id": ids["on"]}).json()
    assert [k["key"] for k in item["specs"]] == ["x2"]
