"""1688 货源助手 (browser extension) API: queue, item, AI 核对 (Gemini, faked), 设为货源 with
per-spec 货源价 and 运费."""
import io
import json
import json as _json

from PIL import Image

from app.db import SessionLocal
from app.models import Store, StoreProduct
from app.services import settings_store, vision

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
    assert item["source"]["offer_id"] == "777" and item["vision"] is False
    # 公式改价: 货源成本 = 货源价 + 运费
    p = client.post("/api/products/formula-preview", json={"ids": [sp_id], "margin": 2}).json()["items"][0]
    assert [k["source"] for k in p["skus"]] == [3990 + 550, 4190 + 550]


class FakeResp:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    sent = []
    reply = None

    def __init__(self, *a, **kw):
        pass

    def post(self, url, params=None, json=None):
        FakeHttp.sent.append((url, params, json))
        if FakeHttp.reply:
            return FakeResp({"candidates": [{"content": {"parts": [{"text": _json.dumps(FakeHttp.reply)}]}}]})
        text = '{"results": [{"key": "a", "verdict": "同款", "confidence": 93, "reason": "款式一致"},' \
               ' {"key": "b", "verdict": "不同", "confidence": 88, "reason": "不是同一款"}]}'
        return FakeResp({"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_ai_verify_with_gemini(client, monkeypatch):
    store, _ = setup_store(client)
    sp_id = _store_product(store["id"])
    cands = [{"key": "a", "image_url": "https://cbu01.alicdn.com/a.jpg", "title": "蕾丝半身裙"},
             {"key": "b", "image_url": "https://cbu01.alicdn.com/b.jpg", "title": "牛仔裤"}]
    r = client.post("/api/ext/verify", json={"kind": "store", "id": sp_id, "candidates": cands})
    assert r.status_code == 400 and "Gemini API Key" in r.json()["detail"]
    settings_store.set_many({"gemini_api_key": "test-key", "gemini_model": "gemini-2.5-flash"})
    monkeypatch.setattr("app.services.wechat.download", lambda url, timeout=60: _png())
    monkeypatch.setattr(vision.httpx, "Client", FakeHttp)
    res = client.post("/api/ext/verify", json={"kind": "store", "id": sp_id, "candidates": cands}).json()["results"]
    assert [(x["key"], x["verdict"], x["confidence"]) for x in res] == [("a", "同款", 93), ("b", "不同", 88)]
    url, params, body = FakeHttp.sent[-1]
    assert url.endswith("/models/gemini-2.5-flash:generateContent") and params == {"key": "test-key"}
    parts = body["contents"][0]["parts"]
    assert sum(1 for x in parts if "inline_data" in x) == 3  # our image + 2 candidates
    assert "法式蕾丝半身裙" in parts[0]["text"] and json.dumps(body["generationConfig"])
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}  # flash: no thinking, faster

    # 测试 in 系统设置 needs no image download
    monkeypatch.setattr("app.services.wechat.download", lambda url, timeout=60: (_ for _ in ()).throw(AssertionError(url)))
    FakeHttp.reply = {"results": [{"key": "1", "verdict": "同款", "confidence": 99, "reason": "同一张图"}]}
    t = client.post("/api/admin/settings/test-gemini").json()
    assert t["result"]["verdict"] == "同款" and "seconds" in t


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
    assert client.get(path.replace(path.split("/")[-1], "0" * 20 + ".jpg")).status_code == 404  # wrong signature
