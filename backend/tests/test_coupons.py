"""优惠券: 到手价 in 商品管理, and 公式改价 = 货源价 × 利润倍率 + 优惠券金额 (compared with the current price)."""
from app.db import SessionLocal
from app.models import ProductSourceLink, Store, StoreCoupon, StoreProduct
from app.routers.products import formula_price
from app.services import coupons as cp

from .conftest import FakeWeChat
from .test_flow import setup_store


def C(**kw):
    return StoreCoupon(store_id=1, coupon_id="c", name=kw.pop("name", "券"), product_ids=kw.pop("pids", []),
                       discount_fee=kw.pop("fee", 0), discount_num=kw.pop("num", 0), threshold=kw.pop("th", 0), **kw)


def test_best_coupon_and_formula():
    coupons = [C(type=4, fee=300, pids=["p1"]), C(type=102, fee=500, th=3000), C(type=103, num=9000)]
    assert cp.best(2000, coupons, "p1") == (1700, coupons[0])  # 直减 3 元 beats 9折 (1800)
    assert cp.best(4000, coupons, "p2")[0] == 3500  # 满30减5 (other product: the 直减 is not for it)
    assert cp.best(2000, coupons, "p2")[0] == 1800  # under 30: only the 9折
    # 货源 ¥8 × 2 = ¥16 must remain after the best coupon
    price, value = formula_price(800, 2, 0, coupons, "p1")
    assert cp.best(price, coupons, "p1")[0] >= 1600 and price == 1900 and value == 300
    assert formula_price(800, 2, 0, [], "p1") == (1600, 0)
    assert cp.parse_coupon({"coupon": {"coupon_id": "9", "type": 4, "status": 2, "coupon_info": {
        "name": "直减", "discount_info": {"discount_fee": 300, "discount_condition": {"product_ids": [11]}}}}}
    )["product_ids"] == ["11"]


class CouponWeChat(FakeWeChat):
    pushed = []

    def list_coupons(self, status, page=1, page_ctx="", page_size=200):
        return {"coupons": [{"coupon_id": "c1"}], "total_num": 1}

    def get_coupon(self, cid):
        return {"coupon": {"coupon_id": cid, "type": 104, "status": 2,
                           "coupon_info": {"name": "店铺直减2元", "discount_info": {"discount_fee": 200}}}}

    def update_prices(self, pid, skus):
        CouponWeChat.pushed.append((pid, skus))


def test_deal_price_and_formula_repricing(client, monkeypatch):
    from app.routers import stores
    monkeypatch.setattr(stores, "client_factory", CouponWeChat)
    store, _ = setup_store(client)
    with SessionLocal() as db:
        s = db.get(Store, store["id"])
        for i, price in ((1, 2000), (2, 1000)):
            sp = StoreProduct(tenant_id=s.tenant_id, store_id=s.id, wx_product_id=f"w{i}", title=f"商品{i}",
                              price_min=price, price_max=price + 500,
                              skus=[{"sku_id": f"s{i}a", "price": price}, {"sku_id": f"s{i}b", "price": price + 500}])
            db.add(sp)
            db.flush()
            db.add(ProductSourceLink(tenant_id=s.tenant_id, store_product_id=sp.id, offer_id=f"o{i}", price=600))
        db.commit()
    r = client.post("/api/products/coupons/sync", params={"store_id": store["id"]}).json()
    assert r["count"] == 1 and r["items"][0]["type_name"] == "店铺直减券"
    items = {p["title"]: p for p in client.get("/api/products").json()["items"]}
    assert (items["商品1"]["deal_min"], items["商品1"]["coupon"]["off"]) == (1800, 200)

    # 货源 ¥6 × 2.5 = ¥15 to the buyer, + ¥2 coupon → ¥17 for the cheapest SKU; others keep their difference
    prev = {p["title"]: p for p in client.post("/api/products/formula-preview", json={
        "ids": [items["商品1"]["id"], items["商品2"]["id"]], "margin": 2.5}).json()["items"]}
    p1, p2 = prev["商品1"], prev["商品2"]
    assert (p1["new_min"], p1["coupon_value"], p1["over"]) == (1700, 200, -300)  # cheaper than now
    assert (p2["new_min"], p2["over"]) == (1700, 700)  # not from 商品云库: compared with the current price
    assert p2["compare_with"] == "现价"
    assert [k["new"] for k in p2["skus"]] == [1700, 2200]
    r = client.post("/api/products/apply-prices", json={"items": [
        {"id": p2["id"], "prices": {k["sku_id"]: k["new"] for k in p2["skus"]}}]}).json()
    assert r["results"][0]["ok"] and CouponWeChat.pushed[-1][1][0]["sale_price"] == 1700


def test_formula_compares_with_library_price(client, monkeypatch):
    """公式改价 compares the new price with the 商品云库 selling price of the product, not the current one."""
    from app.models import LibraryProduct, PublishedRecord
    from app.routers import stores

    from .conftest import make_package
    from .test_flow import upload
    monkeypatch.setattr(stores, "client_factory", CouponWeChat)
    store, _ = setup_store(client)
    upload(client, {"1.json": make_package("1", "阻门器", skus=[{"id": "a", "price": 1681, "attrs": []}])})
    with SessionLocal() as db:
        lib = db.query(LibraryProduct).one()
        db.add(PublishedRecord(store_id=store["id"], source_key="wechat:1", title_hash="h", wx_product_id="w9"))
        sp = StoreProduct(tenant_id=lib.tenant_id, store_id=store["id"], wx_product_id="w9", title="阻门器",
                          price_min=3000, price_max=3000, skus=[{"sku_id": "s", "price": 3000}])
        db.add(sp)
        db.flush()
        db.add(ProductSourceLink(tenant_id=lib.tenant_id, store_product_id=sp.id, offer_id="o", price=828))
        db.commit()
        sp_id = sp.id
    p = client.post("/api/products/formula-preview", json={"ids": [sp_id], "margin": 2}).json()["items"][0]
    assert p["compare_with"] == "云库售价" and p["library_min"] == 1681
    assert (p["new_min"], p["over"]) == (1656, 1656 - 1681)  # below the 云库 price, although below 现价 too
