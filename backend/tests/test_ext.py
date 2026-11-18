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
    # 公式改价: 货源成本 = 货源价 + 运费
    p = client.post("/api/products/formula-preview", json={"ids": [sp_id], "margin": 2}).json()["items"][0]
    assert [k["source"] for k in p["skus"]] == [3990 + 550, 4190 + 550]


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
