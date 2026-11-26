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
