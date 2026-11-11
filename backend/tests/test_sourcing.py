import base64
import json

import httpx
import pytest

from app.services import ali1688, sourcing
from app.services.ali1688 import Ali1688Client, Ali1688Error, sign_headers, split_ak

from .conftest import make_package
from .test_flow import upload

SECRET, KID = "S" * 32, "my-key-id"
AK = base64.urlsafe_b64encode((SECRET + KID).encode()).decode().rstrip("=")


def test_ak_and_signature_match_1688_reference():
    assert split_ak(AK) == (KID, SECRET)
    # Vector produced by 1688's own reference implementation (next-1688/1688-product-find _auth.build_signature)
    h = sign_headers("POST", "/api/alibaba.1688.find.product/1.0.0/github", '{"a": "中文"}', KID, SECRET,
                     "1700000000", "abcdef12")
    assert h["x-csk-sign"] == "pDAlP/2H/RlH2HvJ+qiFQze5/DyN+UGEl4BEdQKeedI="
    assert h["x-csk-ak"] == KID and h["x-csk-version"] == "1.7.0"
    with pytest.raises(Ali1688Error):
        split_ak("short")


def test_client_request_and_error_mapping():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"], seen["body"], seen["sign"] = req.url.path, json.loads(req.content), req.headers["x-csk-sign"]
        if seen["body"]["pageSize"] == 99:
            return httpx.Response(200, json={"success": False, "code": "SignatureInvalid"})
        return httpx.Response(200, json={"success": True, "model": {"data": [
            {"itemId": 123, "title": "钛不粘微压锅", "currentPrice": "26.80", "quantityBegin": 1, "unit": "个",
             "company": "义乌市趣厨贸易有限公司", "soldOut": 14000, "score": 0.93,
             "imageUrl": "https://cbu01.alicdn.com/a.jpg", "promotionTags": ["包邮"], "serviceInfos": [{"text": "一件代发"}]}]}})

    c = Ali1688Client(AK, http=httpx.Client(transport=httpx.MockTransport(handler)))
    offers = c.image_search(image_url="https://cbu01.alicdn.com/x.jpg", page_size=20, purchase_amount=2, sort_type="price_asc")
    assert seen["path"] == "/api/alibaba.1688.find.product/1.0.0/github"
    assert seen["body"]["imageUrl"] == "https://cbu01.alicdn.com/x.jpg" and seen["body"]["purchaseAmount"] == 2
    assert seen["body"]["sortType"] == "price_asc" and seen["sign"]
    o = offers[0]
    assert (o["offer_id"], o["price"], o["moq"], o["supplier"]) == ("123", 26.8, 1, "义乌市趣厨贸易有限公司")
    assert o["detail_url"] == "https://detail.1688.com/offer/123.html" and o["tags"] == ["包邮", "一件代发"]
    with pytest.raises(Ali1688Error, match="签名校验失败"):
        c.image_search(image_url="https://cbu01.alicdn.com/x.jpg", page_size=99)


def offer(oid, price, score, moq=1, sold=0):
    return {"offer_id": oid, "title": f"货源{oid}", "image_url": "", "detail_url": f"https://detail.1688.com/offer/{oid}.html",
            "price": price, "moq": moq, "unit": "个", "sold": sold, "stock": 0, "supplier": f"供应商{oid}",
            "score": score, "sku_title": "", "tags": []}


def test_sales_counts_parsed_from_numbers_and_text():
    from app.services.ali1688 import _count
    assert [_count(v) for v in (None, "", 0, 2000, "2000", "1.2万", "100+", "1,500", "abc")] == \
        [0, 0, 0, 2000, 2000, 12000, 100, 1500, 0]


def test_all_zero_sales_explained(client, fake_1688, monkeypatch):
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    monkeypatch.setattr(FakeAli, "image_search", lambda self, **kw: [offer("9", 5.0, 0.9, sold=0)])
    job = client.post("/api/sourcing/jobs", json={"image_urls": ["https://cbu01.alicdn.com/z.jpg"]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    assert q["offers"] == [] and q["hidden_count"] == 1 and "都没有销量" in q["error"]


def test_pick_best_cheapest_among_close_matches():
    offers = [offer("a", 29.8, 0.95), offer("b", 26.8, 0.90), offer("c", 5.0, 0.40),  # c: cheap but a poor match
              offer("d", 20.0, 0.92, moq=50)]                                         # d: MOQ too high for 1
    assert sourcing.pick_best(offers, 1) == "b"
    assert sourcing.pick_best(offers, 100) == "d"
    assert sourcing.pick_best([], 1) == ""


class FakeAli:
    calls: list = []

    def __init__(self, ak, channel="github"):
        assert ak == AK

    def image_search(self, *, image_url="", image_base64="", page_size=20, purchase_amount=1, sort_type=None):
        FakeAli.calls.append("url" if image_url else "base64")
        return [offer("111", 26.8, 0.93, sold=14000), offer("222", 29.8, 0.95, sold=5),
                offer("333", 3.5, 0.30, sold=100), offer("444", 9.9, 0.94, sold=0)]  # 444: cheapest, never sold


@pytest.fixture
def fake_1688(monkeypatch):
    import io

    from PIL import Image
    FakeAli.calls = []
    monkeypatch.setattr(sourcing, "client_factory", FakeAli)
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(buf, "JPEG")
    monkeypatch.setattr(sourcing, "downloader", lambda url: buf.getvalue())


def test_job_without_ak_reports_clearly(client, fake_1688):
    job = client.post("/api/sourcing/jobs", json={"image_urls": ["https://cbu01.alicdn.com/a.jpg"]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    assert q["status"] == "failed" and "1688 AK" in q["error"]


def test_batch_search_best_pick_link_and_cache(client, fake_1688):
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    assert client.get("/api/admin/settings").json()["ali1688_ak_set"] is True
    upload(client, {"1.json": make_package("1", "钛不粘微压锅")})
    lib = client.get("/api/library/products").json()["items"][0]

    r = client.post("/api/sourcing/jobs", json={"image_urls": [f"https://x.com/{i}.jpg" for i in range(11)]})
    assert r.status_code == 400 and "最多 10" in r.json()["detail"]

    job = client.post("/api/sourcing/jobs", json={
        "library_product_ids": [lib["id"]], "image_urls": ["https://cbu01.alicdn.com/a.jpg"]}).json()
    assert job["total"] == 2
    detail = client.get(f"/api/sourcing/jobs/{job['id']}").json()
    assert detail["status"] == "done" and detail["done"] == 2
    pasted, product_q = sorted(detail["queries"], key=lambda q: q["linkable"])
    assert product_q["best_offer_id"] == "111" and len(product_q["offers"]) == 3   # cheapest close match with sales
    assert product_q["hidden_count"] == 1 and "444" not in [o["offer_id"] for o in product_q["offers"]]
    assert sorted(FakeAli.calls) == ["base64", "url"]  # WeChat CDN image sent as file, 1688 image as URL

    # 设为货源 only for queries that came from a product
    assert client.post("/api/sourcing/link", json={"query_id": pasted["id"], "offer_id": "111"}).status_code == 400
    link = client.post("/api/sourcing/link", json={"query_id": product_q["id"], "offer_id": "222"}).json()
    assert link["price"] == 2980 and link["supplier"] == "供应商222"
    src = client.get("/api/library/products").json()["items"][0]["source"]
    assert src["offer_id"] == "222" and src["detail_url"].endswith("222.html")
    # Re-linking replaces the source
    client.post("/api/sourcing/link", json={"query_id": product_q["id"], "offer_id": "111"})
    assert client.get("/api/library/products").json()["items"][0]["source"]["price"] == 2680

    # Same image again: served from cache, no new 1688 call
    client.post("/api/sourcing/jobs", json={"image_urls": ["https://cbu01.alicdn.com/a.jpg"]})
    assert sorted(FakeAli.calls) == ["base64", "url"]
    assert len(client.get("/api/sourcing/jobs").json()) == 2  # the 11-image request was rejected


def test_sourcing_jobs_are_tenant_isolated(client, fake_1688):
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    job = client.post("/api/sourcing/jobs", json={"image_urls": ["https://cbu01.alicdn.com/a.jpg"]}).json()
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as other:
        from .conftest import new_tenant_client
        new_tenant_client(client, other, "owner2")
        assert other.get(f"/api/sourcing/jobs/{job['id']}").status_code == 404
        assert other.get("/api/sourcing/jobs").json() == []


def test_next_batch_flags_no_source_and_deletes_them(client, fake_1688, monkeypatch):
    from .test_products import StoreWithProducts, _store
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store = _store(client, monkeypatch)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    listed = [p["id"] for p in client.get("/api/products", params={"size": 50}).json()["items"]]

    # No selection: the first 10 in list order, then the next 10
    first = client.get("/api/products/next-batch").json()
    assert [p["id"] for p in first] == listed[:10] and first[0]["cover"].startswith("https://")
    job = client.post("/api/sourcing/jobs", json={"store_product_ids": [p["id"] for p in first]}).json()
    queries = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"]
    assert [p["id"] for p in client.get("/api/products/next-batch").json()] == listed[10:20]

    # Set a source for one; the other 9 searched products are 无货源
    client.post("/api/sourcing/link", json={"query_id": queries[0]["id"], "offer_id": "111"})
    page = client.get("/api/products", params={"size": 50}).json()
    states = {p["id"]: p["source_state"] for p in page["items"]}
    assert page["no_source_count"] == 9
    assert list(states.values()).count("linked") == 1 and list(states.values()).count("none") == 9
    assert all(states[i] == "unsearched" for i in listed[10:])
    assert client.get("/api/products", params={"source": "none"}).json()["total"] == 9
    assert client.get("/api/products", params={"source": "unsearched"}).json()["total"] == 35

    # 删除全部无货源商品: ids, then the normal delete (also deletes in the WeChat store)
    ids = client.get("/api/products/no-source-ids").json()
    assert len(ids) == 9 and set(ids) == {i for i, s in states.items() if s == "none"}
    res = client.post("/api/products/batch-action", json={"ids": ids, "action": "delete"}).json()["results"]
    deleted = sum(r["ok"] for r in res)
    assert len([c for c in StoreWithProducts.calls if c[0] == "delete"]) == deleted
    assert client.get("/api/products").json()["no_source_count"] == 9 - deleted


def test_next_batch_skips_failed_searches_only_when_recent(client, fake_1688, monkeypatch):
    from .test_products import _store
    store = _store(client, monkeypatch)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    first = client.get("/api/products/next-batch").json()
    # Without an AK every query fails: those products are not 无货源 and are offered again
    client.post("/api/sourcing/jobs", json={"store_product_ids": [p["id"] for p in first]})
    assert client.get("/api/products").json()["no_source_count"] == 0
    assert client.get("/api/products/next-batch").json() == first


def test_crop_sends_only_the_framed_subject_and_research(client, fake_1688, monkeypatch):
    import base64
    import io

    from PIL import Image
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    big = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(big, "JPEG")
    monkeypatch.setattr(sourcing, "downloader", lambda url: big.getvalue())
    sent = []

    def search(self, *, image_url="", image_base64="", **kw):
        sent.append(Image.open(io.BytesIO(base64.b64decode(image_base64))).size if image_base64 else image_url)
        return [offer("111", 26.8, 0.93, sold=10)]
    monkeypatch.setattr(FakeAli, "image_search", search)

    url = "https://cbu01.alicdn.com/rack.jpg"
    r = client.post("/api/sourcing/jobs", json={"image_urls": [url], "crops": {url: [0, 0, 0.01, 1]}})
    assert r.status_code == 400 and "太小" in r.json()["detail"]

    # Framed right half: only that region is sent, as an image (even for a 1688 URL)
    job = client.post("/api/sourcing/jobs", json={"image_urls": [url], "crops": {url: [0.5, 0, 0.5, 1]}}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    assert sent == [(200, 200)] and q["crop"] == [0.5, 0, 0.5, 1] and q["status"] == "done"

    # 重新框选 on the result row: new region searched in place
    assert client.post(f"/api/sourcing/queries/{q['id']}/research", json={"crop": [0, 0, 0.25, 0.5]}).json()["ok"]
    detail = client.get(f"/api/sourcing/jobs/{job['id']}").json()
    assert sent[-1] == (100, 100) and detail["status"] == "done"
    assert detail["queries"][0]["crop"] == [0, 0, 0.25, 0.5] and len(detail["queries"][0]["offers"]) == 1

    # 整张图: crop cleared, the URL is sent as before
    client.post(f"/api/sourcing/queries/{q['id']}/research", json={"crop": None})
    assert sent[-1] == url
    assert client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]["crop"] is None


def test_deleted_no_source_products_are_marked_in_library_and_not_relisted(client, fake_1688, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "有货源的商品"), "2.json": make_package("2", "找不到货源的商品")})
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])  # -> wx ids 10001, 10002
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}

    StoreWithProducts.reset()
    monkeypatch.setattr(stores, "client_factory", StoreWithProducts)
    StoreWithProducts.catalog = {wx: {"product_id": wx, "title": wx, "status": 5, "head_imgs": [f"https://mmecimage.cn/{wx}"],
                                      "skus": []} for wx in ("10001", "10002")}
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    sp = {p["wx_product_id"]: p for p in client.get("/api/products").json()["items"]}

    job = client.post("/api/sourcing/jobs", json={"store_product_ids": [p["id"] for p in sp.values()]}).json()
    qs = {q["store_product_id"]: q for q in client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"]}
    client.post("/api/sourcing/link", json={"query_id": qs[sp["10001"]["id"]]["id"], "offer_id": "111"})

    # 删除全部无货源商品 -> deleted in WeChat, the library product is kept but marked 无货源
    ids = client.get("/api/products/no-source-ids").json()
    assert ids == [sp["10002"]["id"]]
    assert client.post("/api/products/batch-action", json={"ids": ids, "action": "delete"}).json()["results"][0]["ok"]
    items = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    assert items["2"]["no_source"] is True and "无货源" in items["2"]["no_source_note"]
    assert items["1"]["no_source"] is False
    assert client.get("/api/library/products", params={"no_source": True}).json()["total"] == 1

    # Publishing again skips it with a clear reason
    t = client.post("/api/tasks", json={"store_ids": [store["id"]], "product_ids": [lib["2"]["id"]]}).json()[0]
    run_all([])
    item = client.get(f"/api/tasks/{t['id']}/items").json()["items"][0]
    assert item["status"] == "skipped" and "无货源" in item["message"]

    # 取消标记 manually, or setting a source on the library product clears it
    client.post("/api/library/products/no-source", json={"ids": [lib["2"]["id"]], "no_source": False})
    assert client.get("/api/library/products", params={"no_source": True}).json()["total"] == 0
    client.post("/api/library/products/no-source", json={"ids": [lib["2"]["id"]], "no_source": True})
    job = client.post("/api/sourcing/jobs", json={"library_product_ids": [lib["2"]["id"]]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})
    assert client.get("/api/library/products", params={"no_source": True}).json()["total"] == 0


def test_library_source_shows_in_product_management_after_upload(client, fake_1688, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库找到货源"), "2.json": make_package("2", "云库没找货源")})
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    # 找货源 in 商品云库 before uploading
    job = client.post("/api/sourcing/jobs", json={"library_product_ids": [lib["1"]["id"]]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})

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
    assert [p["id"] for p in client.get("/api/products/next-batch").json()] == [sp["10002"]["id"]]

    # Changing the source in 商品云库 follows through; a source set in 商品管理 takes precedence
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "222"})
    assert client.get("/api/products", params={"keyword": "10001"}).json()["items"][0]["source"]["price"] == 2980
    job2 = client.post("/api/sourcing/jobs", json={"store_product_ids": [sp["10001"]["id"]]}).json()
    q2 = client.get(f"/api/sourcing/jobs/{job2['id']}").json()["queries"][0]
    client.post("/api/sourcing/link", json={"query_id": q2["id"], "offer_id": "111"})
    src = client.get("/api/products", params={"keyword": "10001"}).json()["items"][0]["source"]
    assert src["price"] == 2680 and "from_library" not in src


def test_cancel_source(client, fake_1688, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库商品"), "2.json": make_package("2", "另一个商品")})
    lib = {i["source_product_id"]: i for i in client.get("/api/library/products").json()["items"]}
    job = client.post("/api/sourcing/jobs", json={"library_product_ids": [lib["1"]["id"], lib["2"]["id"]]}).json()
    qs = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"]
    assert all(q["linked_offer_id"] == "" for q in qs)
    for q in qs:
        client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})
    assert {q["linked_offer_id"] for q in client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"]} == {"111"}

    # From the 找货源 result card
    assert client.post("/api/sourcing/unlink", json={"query_id": qs[0]["id"]}).json()["ok"]
    assert client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]["linked_offer_id"] == ""
    assert client.post("/api/sourcing/unlink", json={"query_id": qs[0]["id"]}).status_code == 404

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


def test_unsearched_counts_in_both_lists(client, fake_1688, monkeypatch):
    from .conftest import make_package
    from .test_flow import upload
    from .test_products import _store
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store = _store(client, monkeypatch)
    client.post("/api/products/sync", json={"store_ids": [store["id"]]})
    assert client.get("/api/products").json()["unsearched_count"] == 45
    first = client.get("/api/products/next-batch").json()
    client.post("/api/sourcing/jobs", json={"store_product_ids": [p["id"] for p in first]})
    page = client.get("/api/products", params={"source": "none"}).json()
    assert page["unsearched_count"] == 35 and page["total"] == 10  # count ignores the 货源 filter

    upload(client, {f"{i}.json": make_package(str(i), f"商品{i}") for i in range(1, 5)})
    lib = client.get("/api/library/products").json()
    assert lib["unlinked_count"] == 4
    job = client.post("/api/sourcing/jobs", json={"library_product_ids": [lib["items"][0]["id"]]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})
    client.post("/api/library/products/no-source", json={"ids": [lib["items"][1]["id"]], "no_source": True})
    lib = client.get("/api/library/products", params={"source": "unlinked"}).json()
    assert lib["unlinked_count"] == 2 and lib["total"] == 2
    assert client.get("/api/library/products", params={"source": "linked"}).json()["total"] == 1
    assert client.get("/api/library/products", params={"source": "no_source"}).json()["total"] == 1


def test_edit_source_price_keeps_link(client, fake_1688, monkeypatch):
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import run_all, setup_store, upload
    from .test_products import StoreWithProducts
    client.put("/api/admin/settings", json={"ali1688_ak": AK})
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库商品")})
    lib = client.get("/api/library/products").json()["items"][0]
    job = client.post("/api/sourcing/jobs", json={"library_product_ids": [lib["id"]]}).json()
    q = client.get(f"/api/sourcing/jobs/{job['id']}").json()["queries"][0]
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})

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
    client.post("/api/sourcing/link", json={"query_id": q["id"], "offer_id": "111"})
    src = client.get("/api/library/products").json()["items"][0]["source"]
    assert src["price"] == 2680 and src["price_edited"] is False

    # No source set
    client.post("/api/sourcing/unlink", json={"library_product_id": lib["id"]})
    assert client.post("/api/sourcing/price", json={"store_product_id": sp["id"], "price": 5}).status_code == 404
