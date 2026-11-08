import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["WORKER_ITEM_INTERVAL_SECONDS"] = "0"

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


class FakeWeChat:
    """Stands in for WeChatClient. Records calls; no network."""
    allowed = {"6422", "1001"}
    instances: list["FakeWeChat"] = []

    def __init__(self, appid, secret, http=None):
        if secret == "bad-secret":
            from app.services.wechat import WeChatError
            raise WeChatError(40125, "invalid appsecret")
        self.appid = appid
        self.added: list[dict] = []
        self.applied: list[list[str]] = []
        self.uploaded_bytes = 0
        FakeWeChat.instances.append(self)

    def shop_info(self):
        return {"nickname": f"店铺{self.appid[-3:]}", "headimg_url": "", "username": "gh_test", "status": "open_finished"}

    def freight_templates(self):
        return [{"id": "111", "name": "全国包邮(偏远地区除外)"}]

    def addresses(self):
        return [{"id": "222", "name": "北京市朝阳区测试路1号"}]

    def upload_image_url(self, url):
        return "https://wx.example/" + url.rsplit("/", 1)[-1]

    def upload_image_bytes(self, data):
        self.uploaded_bytes += len(data)
        return "https://wx.example/wm.jpg"

    def category_detail(self, cat_id):
        return {"info": {"name": "洗车刷" if cat_id == "6422" else f"类目{cat_id}"},
                "attr": {"product_attr_list": [{"name": "材质", "is_required": True, "value": "塑料;金属"}]}}

    def account_categories(self):
        return set(self.allowed)

    def apply_category(self, cats):
        self.applied.append(cats)
        return "16864193776"

    def add_product(self, body):
        self.added.append(body)
        return str(10000 + len(self.added))

    def listing(self, pid):
        return None


def make_package(pid: str, title: str, cats=("6263", "6416", "6422"), skus=None, video=False) -> bytes:
    skus = skus if skus is not None else [{"id": "1", "price": 2990, "attrs": [("规格", "标准款")]}]
    product = {
        "product_key": {"product_id": pid, "appid": "wx_source"},
        "info": {
            "title": title,
            "head_img": [f"https://src.example/{pid}_h{i}.jpg" for i in range(5)],
            "detail": {"detail_img": [f"https://src.example/{pid}_d{i}.jpg" for i in range(4)], "desc": ""},
            "category": [{"cat_id": c} for c in cats],
            "param": [{"param_v2": {"attr_name": "包装规格", "attr_value_name": "套装"}}],
            "total_sold_num": 3,
            "head_video_info": [{"video_url": "https://src.example/v.mp4", "video_len": 15}] if video else [],
        },
        "product_skus": [
            {"sku_key": {"sku_id": s["id"]},
             "product_sku_info": {"thumb_img": f"https://src.example/{pid}_s{s['id']}.jpg", "sale_price": s["price"],
                                  "stock_info": {"stock_num": "100"},
                                  "sale_param": [{"param_v2": {"attr_name": a, "attr_value_name": v}}
                                                 for a, v in s["attrs"]]}}
            for s in skus
        ],
    }
    inner = json.dumps({"errcode": 0, "product": product}, ensure_ascii=False)
    return json.dumps({"data": {"data": inner}, "errno": 0, "errMsg": "", "errorCode": 0}, ensure_ascii=False).encode()


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app.routers import stores
    monkeypatch.setattr(stores, "client_factory", FakeWeChat)
    FakeWeChat.instances.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        r = c.post("/api/auth/register", json={"username": "owner1", "password": "secret123"})
        c.headers["Authorization"] = "Bearer " + r.json()["token"]
        yield c
