from .conftest import FakeWeChat, make_package
from .test_flow import setup_store, upload


class CatWeChat(FakeWeChat):
    """6422 is open; 7001 no deposit, 7002 deposit 2000元, 7003 needs 资质 (10000元), 7004 fails to apply."""
    def category_detail(self, cat_id):
        info = {"7001": ("钥匙扣", 0, False), "7002": ("收纳架", 200000, False), "7003": ("保健品", 1000000, True),
                "7004": ("厨具", 50000, False)}.get(cat_id, ("洗车刷", 0, False))
        return {"info": {"name": info[0]}, "attr": {"deposit": info[1], "access_permit_required": info[2],
                                                   "product_attr_list": []}}

    def apply_category(self, cats):
        if cats[-1] == "7004":
            from app.services.wechat import WeChatError
            raise WeChatError(9700201, "类目需要资质")
        return super().apply_category(cats)


def test_one_click_category_apply(client, monkeypatch):
    from app.routers import stores
    monkeypatch.setattr(stores, "client_factory", CatWeChat)
    store, _ = setup_store(client)
    upload(client, {
        "1.json": make_package("1", "已开通类目商品", cats=("6263", "6416", "6422")),
        "2.json": make_package("2", "钥匙扣A", cats=("1", "2", "7001")),
        "3.json": make_package("3", "钥匙扣B", cats=("1", "2", "7001")),
        "4.json": make_package("4", "收纳架", cats=("1", "3", "7002")),
        "5.json": make_package("5", "保健品", cats=("5", "6", "7003")),
        "6.json": make_package("6", "厨具", cats=("8", "9", "7004")),
    })
    needed = client.get("/api/categories/needed", params={"store_id": store["id"]}).json()
    rows = {r["cat_id"]: r for r in needed["items"]}
    assert rows["6422"]["opened"] is True and rows["7001"]["opened"] is False
    assert rows["7001"]["products"] == 2 and rows["7001"]["path"] == ["1", "2", "7001"]
    assert rows["7001"]["info"] is None  # not fetched yet

    infos = client.post("/api/categories/details", json={"store_id": store["id"],
                                                          "cat_ids": ["7001", "7002", "7003", "7004"]}).json()
    assert infos["7002"] == {"cat_id": "7002", "name": "收纳架", "deposit": 200000, "needs_qualification": False}
    assert infos["7003"]["needs_qualification"] is True and infos["7001"]["deposit"] == 0
    # cached: shown by /needed without asking WeChat again
    rows = {r["cat_id"]: r for r in client.get("/api/categories/needed", params={"store_id": store["id"]}).json()["items"]}
    assert rows["7002"]["info"]["deposit"] == 200000

    res = client.post("/api/categories/apply", json={"store_id": store["id"], "items": [
        {"cat_id": "7001", "path": ["1", "2", "7001"]}, {"cat_id": "7004", "path": ["8", "9", "7004"]}]}).json()["results"]
    assert res[0]["ok"] and res[0]["audit_id"] == "16864193776"
    assert res[1]["ok"] is False and "资质" in res[1]["message"]
    rows = {r["cat_id"]: r for r in client.get("/api/categories/needed", params={"store_id": store["id"]}).json()["items"]}
    assert rows["7001"]["application"]["ok"] and rows["7004"]["application"]["ok"] is False
    assert rows["7002"]["application"] is None
    bad = client.post("/api/categories/apply", json={"store_id": store["id"], "items": [{"cat_id": "7001", "path": ["1", "2"]}]})
    assert bad.status_code == 400


def test_categories_are_tenant_isolated(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import stores

    from .conftest import new_tenant_client
    monkeypatch.setattr(stores, "client_factory", CatWeChat)
    store, _ = setup_store(client)
    with TestClient(app) as other:
        new_tenant_client(client, other, "owner2")
        assert other.get("/api/categories/needed", params={"store_id": store["id"]}).status_code == 404
