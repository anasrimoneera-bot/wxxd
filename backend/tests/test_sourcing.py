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
        return [offer("111", 26.8, 0.93, sold=14000), offer("222", 29.8, 0.95), offer("333", 3.5, 0.30)]


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
    assert product_q["best_offer_id"] == "111" and len(product_q["offers"]) == 3   # cheapest close match
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
        tok = other.post("/api/auth/register", json={"username": "owner2", "password": "secret123"}).json()["token"]
        other.headers["Authorization"] = "Bearer " + tok
        assert other.get(f"/api/sourcing/jobs/{job['id']}").status_code == 404
        assert other.get("/api/sourcing/jobs").json() == []
