import httpx

from app.services import titles
from app.worker import Worker

from .conftest import FakeWeChat, make_package
from .test_flow import run_all, setup_store, upload


def fake_deepseek(calls):
    def post(url, headers, json, timeout):
        calls.append(json["messages"][0]["content"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": '{"title": "新款伸缩汽车除雾刷 后视镜清洁工具"}'}}]})
    return post


def test_regenerate_edit_and_publish_uses_saved_title(client, monkeypatch):
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "伸缩款汽车前挡风玻璃除雾刷后视镜刮水清洁多功能清洁刷K"),
                    "2.json": make_package("2", "厨房剪刀不锈钢强力")})
    items = client.get("/api/library/products").json()["items"]
    ids = [i["id"] for i in items]
    assert all(i["ai_title"] == "" for i in items)

    # Without a DeepSeek key the button explains what to do
    r = client.post("/api/library/products/regenerate-titles", json={"ids": ids})
    assert r.status_code == 400 and "DeepSeek" in r.json()["detail"]

    client.put("/api/admin/settings", json={"deepseek_api_key": "sk-test1234567890"})
    calls = []
    monkeypatch.setattr(titles.httpx, "post", fake_deepseek(calls))
    res = client.post("/api/library/products/regenerate-titles", json={"ids": ids}).json()["results"]
    assert len(res) == 2 and all(x["ai_title"].startswith("新款伸缩汽车除雾刷") for x in res)
    assert all(len(x["ai_title"]) <= 30 for x in res)

    # Manual edit of one title
    p1 = [i for i in items if i["source_product_id"] == "1"][0]
    client.put(f"/api/library/products/{p1['id']}/title", json={"ai_title": "我改过的标题 汽车除雾刷"})
    listed = {i["id"]: i for i in client.get("/api/library/products").json()["items"]}
    assert listed[p1["id"]]["ai_title"] == "我改过的标题 汽车除雾刷"

    # Publishing uses the saved title and does not call DeepSeek again
    calls.clear()
    client.post("/api/tasks", json={"store_ids": [store["id"]], "product_ids": [p1["id"]]})
    run_all([])
    body = [f for f in FakeWeChat.instances if f.added][0].added[0]
    assert body["title"] == "我改过的标题 汽车除雾刷"
    assert calls == []


def test_each_store_gets_its_own_title(client, monkeypatch):
    """The same 商品云库 product published to a second store gets a new title, never one in use."""
    from app.services import titles

    from .conftest import FakeWeChat, make_package
    from .test_flow import run_all, setup_store, upload
    calls = []

    def fake_rewrite(title, hint, max_len, raise_errors=False, avoid=None):
        calls.append(list(avoid or []))
        return None  # no DeepSeek: the source title, then the same words reordered
    monkeypatch.setattr(titles, "rewrite_with_deepseek", fake_rewrite)
    upload(client, {"1.json": make_package("1", "伸缩款汽车前挡风玻璃除雾刷")})
    lib_id = client.get("/api/library/products").json()["items"][0]["id"]
    client.put(f"/api/library/products/{lib_id}/title", json={"ai_title": "汽车除雾刷伸缩款"})
    got = []
    for appid in ("wx0000000001", "wx0000000002", "wx0000000003"):
        client.put("/api/admin/settings", json={"max_overlap_pct": 0})  # the same product in each store
        s, _ = setup_store(client, appid=appid)
        run_all([t["id"] for t in client.post("/api/tasks", json={"store_ids": [s["id"]], "product_ids": [lib_id]}).json()])
        got.append([f for f in FakeWeChat.instances if f.added][-1].added[-1]["title"])
    assert "汽车除雾刷伸缩款" in got[0]  # first store: the title edited in 商品云库
    assert len(set(got)) == 3  # every store a different title
    assert calls and calls[-1] and len(calls[-1]) == 2  # DeepSeek is told the titles already in use


def test_reorder_changes_word_order():
    from app.services.titles import reorder
    t = "汽车前挡风玻璃除雾刷"
    assert reorder(t, 1) != t and sorted(reorder(t, 1)) == sorted(t)
