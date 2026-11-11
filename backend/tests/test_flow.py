import io
import zipfile

from app.worker import Worker

from .conftest import FakeWeChat, make_package


def setup_store(client, appid="wx1234567890"):
    s = client.post("/api/stores", json={"appid": appid, "secret": "good-secret"}).json()
    opts = client.get(f"/api/stores/{s['id']}/options").json()
    tpl = client.post("/api/templates", json={"name": "十元卷店铺 222",
                                              "config": client.get("/api/templates/defaults").json()}).json()
    client.put(f"/api/stores/{s['id']}/config", json={
        "freight_template_id": opts["freight_templates"][0]["id"],
        "freight_template_name": opts["freight_templates"][0]["name"],
        "address_id": opts["addresses"][0]["id"], "address_name": opts["addresses"][0]["name"],
        "publish_template_id": tpl["id"]})
    return s, tpl


def upload(client, files: dict[str, bytes], group_id=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    data = {"group_id": str(group_id)} if group_id else {}
    return client.post("/api/library/upload", files=[("files", ("pack.zip", buf.getvalue(), "application/zip"))],
                       data=data).json()


def run_all(task_ids):
    w = Worker("worker-test", client_factory=FakeWeChat)
    while (item := w.claim()) is not None:
        w.process(item)


def test_bad_secret_rejected(client):
    r = client.post("/api/stores", json={"appid": "wx1234567890", "secret": "bad-secret"})
    assert r.status_code == 400 and "40125" in r.json()["detail"]


def test_full_publish_flow(client):
    store, tpl = setup_store(client)
    assert client.get("/api/stores").json()[0]["config_status"] == "configured"
    g = client.post("/api/library/groups", json={"name": "杨继芸"}).json()
    res = upload(client, {
        "1.json": make_package("1", "伸缩款汽车前挡风玻璃除雾刷后视镜刮水清洁多功能清洁刷K"),
        "2.json": make_package("2", "多规格商品", skus=[{"id": "a", "price": 990, "attrs": []},
                                                    {"id": "b", "price": 1990, "attrs": []}]),
        "3.json": make_package("3", "厨房剪刀不锈钢", cats=("1", "2", "999")),
        "4.json": make_package("4", "厨房剪刀加厚款", cats=("1", "2", "999")),
        "bad.json": b'{"data": [[1]]}',
    }, group_id=g["id"])
    assert res["created"] == 4 and res["failed"] == 1

    # re-upload updates instead of duplicating
    assert upload(client, {"1.json": make_package("1", "伸缩款汽车除雾刷")})["updated"] == 1
    lib = client.get("/api/library/products", params={"group_id": g["id"]}).json()
    assert lib["total"] == 4
    assert client.get("/api/library/stats").json()[-1]["count"] == 4

    tasks = client.post("/api/tasks", json={"store_ids": [store["id"]], "platform": "wechat",
                                            "group_id": g["id"]}).json()
    assert tasks[0]["total"] == 4 and tasks[0]["scope"] == "微信小店 / 杨继芸"
    run_all([t["id"] for t in tasks])

    t = client.get("/api/tasks").json()["items"][0]
    assert (t["success"], t["failed"], t["remaining"], t["status"]) == (1, 3, 0, "partial")
    items = {i["source_product_id"]: i for i in client.get(f"/api/tasks/{t['id']}/items").json()["items"]}
    assert items["1"]["status"] == "success" and items["1"]["wx_product_id"] == "10001"
    assert "已上架" in items["1"]["message"] and items["1"]["wx_category"] == "洗车刷（6422）"
    assert "但数据包未提供销售规格" in items["2"]["message"]
    assert "提交微信类目申请" in items["3"]["message"] and "16864193776" in items["3"]["message"]
    assert "申请审核中" in items["4"]["message"]  # same category: not applied for twice
    assert sum(len(f.applied) for f in FakeWeChat.instances) == 1

    fake = [f for f in FakeWeChat.instances if f.added][0]
    body = fake.added[0]
    assert body["skus"][0]["sale_price"] == 4340  # 29.90 + 13.50
    assert len(body["title"]) <= 30 and body["title"] != ""
    assert {"attr_key": "材质", "attr_value": "见详情"} in body["attrs"]  # required attr from custom attrs
    assert body["express_info"]["template_id"] == "111"

    # Export works
    assert client.get(f"/api/tasks/{t['id']}/export").status_code == 200

    # Second task: product 1 is filtered as a duplicate for this store
    first = [p for p in lib["items"] if p["source_product_id"] == "1"][0]
    t2 = client.post("/api/tasks", json={"store_ids": [store["id"]], "product_ids": [first["id"]]}).json()
    run_all([t2[0]["id"]])
    items2 = client.get(f"/api/tasks/{t2[0]['id']}/items").json()["items"]
    assert items2[0]["status"] == "skipped" and "过滤重复商品" in items2[0]["message"]


def test_unconfigured_store_cannot_publish(client):
    s = client.post("/api/stores", json={"appid": "wx1234567890", "secret": "good-secret"}).json()
    upload(client, {"1.json": make_package("1", "测试商品")})
    r = client.post("/api/tasks", json={"store_ids": [s["id"]]})
    assert r.status_code == 400 and "未配置" in r.json()["detail"]


def test_tenant_isolation(client):
    store, _ = setup_store(client)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as other:
        from .conftest import new_tenant_client
        new_tenant_client(client, other, "owner2")
        assert other.get("/api/stores").json() == []
        assert other.get(f"/api/stores/{store['id']}/options").status_code == 404


def test_subaccounts(client):
    sub = client.post("/api/subaccounts", json={"username": "op1", "password": "secret123"}).json()
    assert sub["role"] == "operator"
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c2:
        tok = c2.post("/api/auth/login", json={"username": "op1", "password": "secret123"}).json()["token"]
        c2.headers["Authorization"] = "Bearer " + tok
        assert c2.get("/api/stores").status_code == 200
        assert c2.get("/api/subaccounts").status_code == 403


def test_add_product_without_category_permission_auto_applies(client):
    """Real store: product/add fails with 6600145 when the category check could not run beforehand."""
    from app.services.wechat import WeChatError

    class NoPermission(FakeWeChat):
        def account_categories(self):
            raise WeChatError(-1, "endpoint unavailable")  # pre-check skipped, like on the real store

        def add_product(self, body):
            raise WeChatError(10020110, "商品信息检查不通过, 错误码:6600145 原因:无该类目权限，请申请类目后再发品。")

    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "机械密码锁"), "2.json": make_package("2", "机械挂锁")})
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    w = Worker("w", client_factory=NoPermission)
    while (i := w.claim()) is not None:
        w.process(i)
    items = sorted(client.get(f"/api/tasks/{client.get('/api/tasks').json()['items'][0]['id']}/items").json()["items"],
                   key=lambda x: x["source_product_id"])
    assert "已自动提交微信类目申请" in items[0]["message"] and "重试失败" in items[0]["message"]
    assert "申请审核中" in items[1]["message"]  # same category: applied only once
    assert sum(len(f.applied) for f in FakeWeChat.instances if isinstance(f, NoPermission)) == 1


def test_brand_attribute_dropped_and_no_brand_id_sent(client):
    import json as _json

    store, _ = setup_store(client)
    pkg = _json.loads(make_package("1", "机械密码锁"))
    inner = _json.loads(pkg["data"]["data"])
    inner["product"]["info"]["param"].append({"param_v2": {"attr_name": "品牌", "attr_value_name": "某某牌"}})
    pkg["data"]["data"] = _json.dumps(inner, ensure_ascii=False)
    upload(client, {"1.json": _json.dumps(pkg, ensure_ascii=False).encode()})
    client.post("/api/tasks", json={"store_ids": [store["id"]]})
    run_all([])
    body = [f for f in FakeWeChat.instances if f.added][0].added[0]
    assert body["brand_id"] == "2100000000"
    assert not any(a["attr_key"] == "品牌" for a in body["attrs"])
