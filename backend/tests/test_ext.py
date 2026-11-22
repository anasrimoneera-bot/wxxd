"""1688 货源助手 (browser extension) API: queue, item, image link, 设为货源 with
per-spec 货源价 and 运费."""
import io

from PIL import Image

from app.db import SessionLocal
from app.models import Store, StoreProduct

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
