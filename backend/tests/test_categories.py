from app.services import category_info
from app.services.wechat import WeChatError

from .conftest import FakeWeChat
from .test_flow import setup_store

TREE = {"cats_v2": [{"cat_and_qua": [
    {"cat": {"cat_id": 1, "f_cat_id": 0, "name": "家居", "level": 1, "leaf": False}, "qua": {}},
    {"cat": {"cat_id": 11, "f_cat_id": 1, "name": "收纳", "level": 2, "leaf": False}, "qua": {}},
    {"cat": {"cat_id": 111, "f_cat_id": 11, "name": "收纳架", "level": 3, "leaf": True}, "qua": {}},
    {"cat": {"cat_id": 112, "f_cat_id": 11, "name": "收纳盒", "level": 3, "leaf": True}, "qua": {}},
    {"cat": {"cat_id": 113, "f_cat_id": 11, "name": "真空袋", "level": 3, "leaf": True}, "qua": {}},
    {"cat": {"cat_id": 2, "f_cat_id": 0, "name": "保健", "level": 1, "leaf": False}, "qua": {}},
    {"cat": {"cat_id": 21, "f_cat_id": 2, "name": "保健品", "level": 2, "leaf": True},
     "qua": {"qua_id": 9, "need_to_apply": True, "tips": "需要食品经营许可证"}},
    {"cat": {"cat_id": 6422, "f_cat_id": 2, "name": "洗车刷", "level": 2, "leaf": True}, "qua": {}},
]}]}
RULE = {"111": 100000, "112": 200000, "21": 1000000, "6422": 0}  # fen; 113: rule fails -> detail


class CatWeChat(FakeWeChat):
    def all_categories(self):
        return TREE

    def category_deposit_rule(self, cat_id):
        if cat_id not in RULE:
            raise WeChatError(10020001, "规则不存在")
        return {"errcode": 0, "data": {"rule": {"deposit": RULE[cat_id]}}}

    def category_detail(self, cat_id):
        return {"info": {"name": "x"}, "attr": {"deposit": 50000, "product_attr_list": []}}

    def apply_category(self, cats):
        if cats[-1] == "112":
            raise WeChatError(9700201, "当前类目暂不支持申请")
        return super().apply_category(cats)


def test_parse_tree_paths_and_qualification():
    leaves = {x["cat_id"]: x for x in category_info.parse_tree(TREE)}
    assert set(leaves) == {"111", "112", "113", "21", "6422"}
    assert leaves["111"]["path"] == ["1", "11", "111"] and leaves["111"]["path_names"] == ["家居", "收纳", "收纳架"]
    assert leaves["21"]["needs_qualification"] is True and "许可证" in leaves["21"]["qua_tips"]
    # old 3-level tree without leaf flags: leaves are the nodes nobody points to
    old = {"cats": [{"cat": {"cat_id": 5, "f_cat_id": 0, "name": "a"}}, {"cat": {"cat_id": 6, "f_cat_id": 5, "name": "b"}}]}
    assert [x["cat_id"] for x in category_info.parse_tree(old)] == ["6"]
    assert category_info.find_deposit({"data": [{"x": 1}, {"rule": {"deposit": "300000"}}]}) == 300000


def test_sync_library_filter_by_deposit_and_apply(client, monkeypatch):
    from app.routers import categories, stores
    monkeypatch.setattr(stores, "client_factory", CatWeChat)
    monkeypatch.setattr(category_info, "PAUSE", 0)
    monkeypatch.setattr(categories, "APPLY_PAUSE", 0)
    store, _ = setup_store(client)

    assert client.post("/api/categories/catalog/sync", json={"store_id": store["id"]}).json()["status"] in ("running", "done")
    st = client.get("/api/categories/catalog/status").json()
    assert st["status"] == "done" and st["leaves"] == 5 and st["with_deposit"] == 5

    ov = client.get("/api/categories/overview", params={"store_id": store["id"]}).json()
    rows = {r["cat_id"]: r for r in ov["items"]}
    assert rows["111"]["deposit"] == 100000 and rows["111"]["deposit_source"] == "rule"
    assert rows["113"]["deposit"] == 50000 and rows["113"]["deposit_source"] == "detail"  # fallback
    assert rows["111"]["path_names"] == "家居 / 收纳 / 收纳架"
    assert rows["6422"]["opened"] is True and rows["111"]["opened"] is False  # FakeWeChat.allowed
    assert rows["21"]["needs_qualification"] is True

    res = client.post("/api/categories/apply", json={"store_id": store["id"], "cat_ids": ["111", "112", "999"]}).json()["results"]
    by = {r["cat_id"]: r for r in res}
    assert by["111"]["ok"] and by["111"]["audit_id"] == "16864193776"
    assert by["112"]["ok"] is False and "暂不支持" in by["112"]["message"]
    assert by["999"]["ok"] is False and "同步类目库" in by["999"]["message"]
    applied = [inst.applied for inst in CatWeChat.instances if inst.applied]
    assert ["1", "11", "111"] in [a for lst in applied for a in lst]  # full path from the library
    rows = {r["cat_id"]: r for r in client.get("/api/categories/overview", params={"store_id": store["id"]}).json()["items"]}
    assert rows["111"]["application"]["ok"] and rows["112"]["application"]["ok"] is False

    raw = client.get("/api/categories/raw", params={"store_id": store["id"], "cat_id": "111"}).json()
    assert raw["deposit_read"] == 100000 and raw["getcategoryrule"]["data"]["rule"]["deposit"] == 100000

    # second sync: nothing stale, nothing re-read; a sync can't start twice
    calls = []
    monkeypatch.setattr(CatWeChat, "category_deposit_rule", lambda self, c: calls.append(c) or {"deposit": 1})
    client.post("/api/categories/catalog/sync", json={"store_id": store["id"]})
    assert calls == []
    client.post("/api/categories/catalog/sync", json={"store_id": store["id"], "full": True})
    assert len(calls) == 5


def test_categories_are_tenant_isolated(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import stores

    from .conftest import new_tenant_client
    monkeypatch.setattr(stores, "client_factory", CatWeChat)
    store, _ = setup_store(client)
    with TestClient(app) as other:
        new_tenant_client(client, other, "owner2")
        assert other.get("/api/categories/overview", params={"store_id": store["id"]}).status_code == 404
        assert other.post("/api/categories/catalog/sync", json={"store_id": store["id"]}).status_code == 404


def test_apply_stops_on_daily_quota_and_keeps_rest_eligible(client, monkeypatch):
    from app.routers import categories, stores

    class QuotaWeChat(CatWeChat):
        def apply_category(self, cats):
            if cats[-1] == "113":
                raise WeChatError(45009, "reach max api daily quota limit")
            return super().apply_category(cats)

    monkeypatch.setattr(stores, "client_factory", QuotaWeChat)
    monkeypatch.setattr(category_info, "PAUSE", 0)
    monkeypatch.setattr(categories, "APPLY_PAUSE", 0)
    store, _ = setup_store(client)
    client.post("/api/categories/catalog/sync", json={"store_id": store["id"]})
    out = client.post("/api/categories/apply", json={"store_id": store["id"], "cat_ids": ["111", "113", "21"]}).json()
    assert out["quota_exhausted"] is True
    assert [r["cat_id"] for r in out["results"]] == ["111"]  # stopped at 113, 21 never tried
    rows = {r["cat_id"]: r for r in client.get("/api/categories/overview", params={"store_id": store["id"]}).json()["items"]}
    assert rows["111"]["application"]["ok"] and rows["113"]["application"] is None and rows["21"]["application"] is None


def test_opened_categories_collected_from_any_format():
    from app.services.wechat import WeChatClient, collect_cat_ids
    assert collect_cat_ids({"cats_list": [{"first_cat_id": 1, "second_cat_id": 11, "third_cat_id": 111}],
                            "data": {"list": [{"cat_id": "6422", "f_cat_id": 2, "status": 1}]}}) == {"1", "11", "111", "6422"}

    class C(WeChatClient):
        def __init__(self):
            pass

        def post(self, path, body=None, **kw):
            if "relation" in path and path.startswith("/channels"):
                raise WeChatError(48001, "api unauthorized")
            if "relation" in path:
                return {"list": [{"category_id": 21}]}
            return {"cats_list": [{"first_cat_id": 2, "second_cat_id": 6422}]}

    assert C().account_categories() == {"21", "2", "6422"}
    assert "errcode" in C().account_categories_raw()["/channels/ec/category/get_category_relation_list"]
