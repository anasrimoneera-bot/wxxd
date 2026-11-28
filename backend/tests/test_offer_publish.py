"""1688 上架: a 商品云库 product is listed with its 1688 offer — 1688 images and the picked specs, the
云库 title and category, prices from 自动改价 on the 1688 price + 运费 — and the store product gets its
货源 (each spec's 货源价) right away."""
from app.db import SessionLocal
from app.models import LibraryProduct, ProductSourceLink, PublishTask, StoreProduct
from app.worker import Worker

from .conftest import FakeWeChat, make_package
from .test_flow import setup_store, upload


class OfferWeChat(FakeWeChat):
    def get_product(self, pid):
        body = self.added[-1]
        return {"product": {"product_id": pid, "title": body["title"], "status": 5, "head_imgs": body["head_imgs"],
                            "skus": [{"sku_id": f"wx{i}", "sale_price": k["sale_price"], "stock_num": k["stock_num"],
                                      "sku_attrs": k["sku_attrs"]} for i, k in enumerate(body["skus"])]}}


OFFER = {"offer_id": "650000000123", "title": "1688 杯子批发", "supplier": "义乌杯厂", "freight": 3,
         "images": ["https://cbu01.alicdn.com/img/a.jpg", "https://cbu01.alicdn.com/img/b.jpg"],
         "detail_images": ["https://cbu01.alicdn.com/img/d1.jpg"],
         "skus": [{"key": "s1", "name": "红色 / 大号", "attrs": [{"name": "颜色", "value": "红色"}, {"name": "尺寸", "value": "大号"}],
                   "price": 5.5, "stock": 200, "image": "https://cbu01.alicdn.com/img/r.jpg"},
                  {"key": "s2", "name": "蓝色 / 大号", "attrs": [{"name": "颜色", "value": "蓝色"}, {"name": "尺寸", "value": "大号"}],
                   "price": 6.5, "stock": 50}]}


def test_publish_from_1688_offer(client):
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库里的玻璃杯标题")})
    client.put("/api/products/auto-price", json={"enabled": False, "margin": 2, "add": 1})
    with SessionLocal() as db:
        lib_id = db.query(LibraryProduct).one().id
    r = client.post("/api/ext/publish-offer", json={"id": lib_id, "store_ids": [store["id"]], "offer": OFFER})
    assert r.status_code == 200, r.text
    assert [t["store_id"] for t in r.json()["tasks"]] == [store["id"]]
    with SessionLocal() as db:
        lk = db.query(ProductSourceLink).filter_by(library_product_id=lib_id).one()
        assert (lk.offer_id, lk.price, lk.freight) == ("650000000123", 850, 300)  # ¥5.50 + ¥3 运费
        assert db.get(PublishTask, r.json()["tasks"][0]["id"]).offer_mode
    w = Worker("w", client_factory=OfferWeChat)
    while (item := w.claim()) is not None:
        w.process(item)
    fake = [f for f in FakeWeChat.instances if f.added][-1]
    body = fake.added[-1]
    assert "玻璃杯" in body["title"]  # the 云库 title, not the 1688 one
    assert body["cats_v2"][-1]["cat_id"] == 6422  # the 云库 category
    assert len(body["head_imgs"]) == 2 and len(body["desc_info"]["imgs"]) == 1  # 1688 images
    # each spec: (1688 price + 运费) × 2 + ¥1 加价 (no coupons)
    assert [(k["sku_attrs"][0]["attr_value"], k["sale_price"], k["stock_num"]) for k in body["skus"]] == [
        ("红色", 1800, 200), ("蓝色", 2000, 50)]
    with SessionLocal() as db:
        sp = db.query(StoreProduct).filter_by(store_id=store["id"]).one()
        link = db.query(ProductSourceLink).filter_by(store_product_id=sp.id).one()
        assert link.offer_id == "650000000123" and link.sku_prices == {"wx0": 850, "wx1": 950}
    items = client.get(f"/api/tasks/{r.json()['tasks'][0]['id']}/items").json()["items"]
    assert items[0]["status"] == "success" and "没对应上" not in items[0]["message"]
    # the product is no longer in the 找货源 list of 商品云库 (it has its 货源)
    q = client.get("/api/ext/queue", params={"kind": "library", "missing": "true"}).json()["items"]
    assert lib_id not in [i["id"] for i in q]


def test_publish_offer_needs_configured_store(client):
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "杯子")})
    with SessionLocal() as db:
        lib_id = db.query(LibraryProduct).one().id
    client.put(f"/api/stores/{store['id']}/config", json={"freight_template_id": "777", "freight_template_name": "别的店的"})
    r = client.post("/api/ext/publish-offer", json={"id": lib_id, "store_ids": [store["id"]], "offer": OFFER})
    assert r.status_code == 400 and "不存在" in r.json()["detail"]


def test_1688_video_uploaded_to_wechat(client, monkeypatch):
    """The 1688 video goes to WeChat's own video upload first (WeChat takes no outside links); a video
    WeChat refuses does not keep the product from being listed."""
    from app.services import publisher
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "云库杯子")})
    with SessionLocal() as db:
        lib_id = db.query(LibraryProduct).one().id
    got = []
    monkeypatch.setattr(publisher, "download", lambda url, timeout=60, referer="": got.append((url, referer)) or b"x" * 10)

    class VideoWeChat(OfferWeChat):
        uploaded = []
        refuse = False

        def upload_video(self, data, file_type="mp4"):
            VideoWeChat.uploaded.append((len(data), file_type))
            return "https://173.wxapp.tc.qq.com/v.mp4"

        def add_product(self, body):
            if VideoWeChat.refuse and "head_videos" in body:
                from app.services.wechat import WeChatError
                raise WeChatError(10020035, "图片/视频 url 非法")
            return super().add_product(body)

    offer = {**OFFER, "video_url": "https://cloud.video.taobao.com/play/u/1/p/1/e/6/t/1/abc.mp4", "detail_url": "https://detail.1688.com/offer/650000000123.html"}
    client.put("/api/admin/settings", json={"max_overlap_pct": 0})  # the same item in a 2nd store
    store2, _ = setup_store(client, appid="wx0000000002")
    for refuse, st in ((False, store), (True, store2)):
        VideoWeChat.refuse = refuse
        r = client.post("/api/ext/publish-offer", json={"id": lib_id, "store_ids": [st["id"]], "offer": offer})
        assert r.status_code == 200, r.text
        w = Worker("w", client_factory=VideoWeChat)
        while (item := w.claim()) is not None:
            w.process(item)
        it = client.get(f"/api/tasks/{r.json()['tasks'][0]['id']}/items").json()["items"][0]
        body = [f for f in FakeWeChat.instances if f.added][-1].added[-1]
        if not refuse:
            assert it["status"] == "success" and "已上传主图视频" in it["message"]
            assert body["head_videos"] == {"video_url": "https://173.wxapp.tc.qq.com/v.mp4"}
            assert got[-1] == (offer["video_url"], offer["detail_url"]) and VideoWeChat.uploaded[-1] == (10, "mp4")
        else:  # refused: listed without the video, with the reason
            assert it["status"] == "success" and "head_videos" not in body and "已不带视频上架" in it["message"]


def test_library_search_for_swap(client):
    upload(client, {"1.json": make_package("12345", "高清放大镜老人阅读"), "2.json": make_package("777", "保温杯")})
    by_id = client.get("/api/ext/library-search", params={"q": "12345"}).json()["items"]
    assert [i["source_product_id"] for i in by_id] == ["12345"] and by_id[0]["kind"] == "library"
    assert [i["title"] for i in client.get("/api/ext/library-search", params={"q": "放大镜"}).json()["items"]] == ["高清放大镜老人阅读"]
    assert client.get("/api/ext/library-search", params={"q": "没有"}).json()["items"] == []


def test_retry_all_failed_tasks(client):
    from app.models import PublishTaskItem, Store
    s, _ = setup_store(client)
    with SessionLocal() as db:
        tenant = db.get(Store, s["id"]).tenant_id
        ids = []
        for status, failed, n_fail in (("failed", 1, 1), ("partial", 2, 2), ("success", 0, 0), ("running", 1, 1)):
            t = PublishTask(tenant_id=tenant, name="t", store_id=s["id"], store_name="店", template_snapshot={},
                            status=status, total=3, failed=failed)
            db.add(t); db.flush(); ids.append(t.id)
            for k in range(n_fail):
                db.add(PublishTaskItem(task_id=t.id, library_product_id=k, status="failed", message="创建商品失败"))
            db.add(PublishTaskItem(task_id=t.id, library_product_id=9, status="success"))
        db.commit()
    assert client.post("/api/tasks/retry-all-failed", json={"store_name": "别的店"}).json() == {"tasks": 0, "requeued": 0}
    assert client.post("/api/tasks/retry-all-failed", json={}).json() == {"tasks": 2, "requeued": 3}
    with SessionLocal() as db:
        got = {i: (db.get(PublishTask, i).status, db.get(PublishTask, i).failed) for i in ids}
    assert got == {ids[0]: ("pending", 0), ids[1]: ("pending", 0), ids[2]: ("success", 0), ids[3]: ("running", 1)}


def test_delete_finished_tasks(client):
    from app.models import PublishTaskItem, Store
    s, _ = setup_store(client)
    with SessionLocal() as db:
        tenant = db.get(Store, s["id"]).tenant_id
        ids = []
        for status in ("success", "failed", "running"):
            t = PublishTask(tenant_id=tenant, name="t", store_id=s["id"], store_name="店", template_snapshot={}, status=status, total=1)
            db.add(t); db.flush(); ids.append(t.id)
            db.add(PublishTaskItem(task_id=t.id, library_product_id=1, status="success"))
        db.commit()
    assert client.post("/api/tasks/delete", json={"ids": ids + [999999]}).json() == {"deleted": 2, "busy": 1}
    with SessionLocal() as db:
        assert [i for i in ids if db.get(PublishTask, i)] == [ids[2]]
        assert db.query(PublishTaskItem).filter(PublishTaskItem.task_id.in_(ids[:2])).count() == 0


def test_unexpected_error_says_what_went_wrong(client, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import ext
    monkeypatch.setattr(ext, "_get", lambda *a: (_ for _ in ()).throw(ValueError("坏数据")))
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update(client.headers)
    r = c.post("/api/ext/publish-offer", json={"id": 1, "store_ids": [1], "offer": OFFER})
    assert r.status_code == 500 and "ValueError：坏数据" in r.json()["detail"]


def test_plugin_uses_shared_library(client):
    from fastapi.testclient import TestClient
    from app.main import app
    from .conftest import new_tenant_client
    upload(client, {"1.json": make_package("4321", "共享的折叠伞")})
    with TestClient(app) as other:
        new_tenant_client(client, other, "nie2")
        assert other.get("/api/ext/queue", params={"kind": "library"}).json()["items"] == []
        q = other.get("/api/ext/queue", params={"kind": "shared"}).json()["items"]
        assert [(i["kind"], i["title"]) for i in q] == [("shared", "共享的折叠伞")]
        found = other.get("/api/ext/library-search", params={"q": "折叠伞"}).json()["items"]
        assert [i["kind"] for i in found] == ["shared"]
        r = other.post("/api/ext/use-shared", json={"ids": [q[0]["id"]]}).json()["items"]
        assert r[0]["kind"] == "library" and r[0]["id"] != q[0]["id"]
        # now in its own 商品云库: the plugin works on the copy; the 共享云库 no longer lists it
        assert other.get(f"/api/ext/item?kind=library&id={r[0]['id']}").status_code == 200
        assert other.get("/api/ext/queue", params={"kind": "shared"}).json()["items"] == []
        assert [i["kind"] for i in other.get("/api/ext/library-search", params={"q": "折叠伞"}).json()["items"]] == ["library"]
        assert other.post("/api/ext/use-shared", json={"ids": [q[0]["id"]]}).json()["items"][0]["id"] == r[0]["id"]


def test_publish_straight_from_1688_with_picked_category(client):
    from app.models import CategoryInfo
    store, _ = setup_store(client)
    with SessionLocal() as db:
        for cid, name, path, names in (("7001", "帽子", "100/200/7001", "服饰配件 / 帽子 / 帽子"),
                                       ("7002", "围巾", "100/200/7002", "服饰配件 / 围巾 / 围巾"),
                                       ("6422", "水杯", "300/6422", "厨房用品 / 水杯")):
            db.add(CategoryInfo(cat_id=cid, name=name, path=path, path_names=names, leaf=True))
        db.commit()
    from app.services import category_pick
    category_pick._cache["at"] = 0
    # 违禁词 from the plugin -> every 上货模板 of the account
    r = client.post("/api/ext/banned-words", json={"words": ["爆款", " 爆款 ", "抗寒"]}).json()
    assert r["added"] == 2 and set(client.get("/api/ext/banned-words").json()["words"]) >= {"爆款", "抗寒"}
    offer = {**OFFER, "offer_id": "880001", "title": "爆款大容量不锈钢保温水杯"}
    r = client.post("/api/ext/publish-1688", json={"store_ids": [store["id"]], "offer": offer,
                                                    "attrs": [{"name": "材质", "value": "腈纶"}],
                                                    "category": "厨房用品>杯子/水杯"})
    assert r.status_code == 200, r.text
    assert "水杯" in r.json()["category"] and r.json()["tasks"][0]["store_id"] == store["id"]
    with SessionLocal() as db:
        p = db.get(LibraryProduct, r.json()["id"])
        assert (p.platform, p.source_product_id, p.normalized["cats"]) == ("1688", "880001", ["300", "6422"])
    w = Worker("w", client_factory=OfferWeChat)
    while (item := w.claim()) is not None:
        w.process(item)
    items = client.get(f"/api/tasks/{r.json()['tasks'][0]['id']}/items").json()["items"]
    assert items[0]["status"] == "success", items[0]["message"]
    body = [f for f in FakeWeChat.instances if f.added][-1].added[-1]
    assert body["cats_v2"][-1]["cat_id"] == 6422 and "爆款" not in body["title"] and "水杯" in body["title"]
    assert {"attr_key": "材质", "attr_value": "腈纶"} in body["attrs"]
    assert [k["sale_price"] for k in body["skus"]] == [1700, 1900]  # (¥5.5/6.5 + ¥3 运费) × 2 + 0
    # again: the same 商品云库 product, its category kept
    r2 = client.post("/api/ext/publish-1688", json={"store_ids": [store["id"]], "offer": offer})
    assert r2.status_code == 200 and r2.json()["id"] == r.json()["id"]


def test_publish_1688_without_category_library(client):
    store, _ = setup_store(client)
    from app.services import category_pick
    category_pick._cache["at"] = 0
    r = client.post("/api/ext/publish-1688", json={"store_ids": [store["id"]], "offer": {**OFFER, "offer_id": "880002"}})
    assert r.status_code == 400 and "同步类目库" in r.json()["detail"]


def test_admin_banned_words_apply_to_every_account(client):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import titles
    from app.services.template_config import TemplateConfig
    from .conftest import new_tenant_client
    setup_store(client)  # the admin's 上货模板
    assert client.post("/api/ext/banned-words", json={"words": ["爆款"]}).json()["shared"] is True
    titles._shared["at"] = 0
    assert titles.clean("爆款大容量保温水杯", TemplateConfig()) == "大容量保温水杯"  # any account's template
    with TestClient(app) as other:
        new_tenant_client(client, other, "user9")
        setup_store(other, "wx9999999999")
        r = other.get("/api/ext/banned-words").json()
        assert "爆款" in r["words"] and r["shared"] >= 1 and r["is_admin"] is False
        assert other.post("/api/ext/banned-words", json={"words": ["秒杀"]}).json()["shared"] is False
        titles._shared["at"] = 0
        assert "秒杀" not in titles.shared_banned_words()  # an ordinary account's words stay its own


def test_unit_attrs_and_rejected_attrs(client):
    """6601140: 重量 needs "数值 单位" in a unit of the category; 颜色 at most 100 characters; when WeChat
    still rejects a copied 商品参数, the product is listed with only the required ones."""
    from app.models import CategoryInfo
    from app.services.wechat import WeChatError

    class UnitWeChat(OfferWeChat):
        def category_detail(self, cat_id):
            d = super().category_detail(cat_id)
            d["attr"]["product_attr_list"] += [{"name": "重量", "is_required": True, "type_v2": "decimal4_unit", "value": "kg;g"},
                                               {"name": "颜色", "is_required": False, "type_v2": "string", "value": ""}]
            return d

        bodies = []

        def add_product(self, body):
            UnitWeChat.bodies.append(dict(body))
            if any(a["attr_key"] == "产地" for a in body["attrs"]):
                self.added.append(body)  # seen, then rejected
                raise WeChatError(10020110, "商品信息检查不通过, 错误码:6601140 原因:商品参数产地不在可选范围内")
            return super().add_product(body)

    store, _ = setup_store(client)
    with SessionLocal() as db:
        db.add(CategoryInfo(cat_id="6422", name="水杯", path="300/6422", path_names="厨房用品 / 水杯", leaf=True))
        db.commit()
    from app.services import category_pick
    category_pick._cache["at"] = 0
    colours = "、".join(f"颜色{i}号款" for i in range(40))
    r = client.post("/api/ext/publish-1688", json={"store_ids": [store["id"]], "offer": {**OFFER, "offer_id": "880009", "title": "保温水杯"},
                                                    "attrs": [{"name": "重量", "value": "0.5千克"}, {"name": "颜色", "value": colours[:128]},
                                                              {"name": "产地", "value": "浙江"}]})
    assert r.status_code == 200, r.text
    w = Worker("w", client_factory=UnitWeChat)
    while (item := w.claim()) is not None:
        w.process(item)
    first, second = UnitWeChat.bodies[0], UnitWeChat.bodies[-1]
    a1 = {a["attr_key"]: a["attr_value"] for a in first["attrs"]}
    assert a1["重量"] == "500 g" and len(a1["颜色"]) <= 100 and a1["颜色"].endswith("号款")
    assert {a["attr_key"] for a in second["attrs"]} == {"材质", "重量"}  # only the required ones
    items = client.get(f"/api/tasks/{r.json()['tasks'][0]['id']}/items").json()["items"]
    assert items[0]["status"] == "success" and "只带类目必填参数" in items[0]["message"]
