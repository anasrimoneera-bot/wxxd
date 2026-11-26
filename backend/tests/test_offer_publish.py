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
