import pytest

from app.services.images import add_watermark, apply_delete
from app.services.parser import ParseError, parse_package
from app.services.pricing import apply_price, drop_lure_skus
from app.services.template_config import DeleteRange, KeywordReplace, TemplateConfig, Watermark
from app.services.titles import build_title, dedupe_keywords, truncate

from .conftest import make_package


def test_parse_primary_format():
    n = parse_package(make_package("123", "伸缩款汽车除雾刷K", video=True))
    assert n["source_product_id"] == "123"
    assert n["cats"] == ["6263", "6416", "6422"]
    assert n["skus"][0]["price"] == 2990 and n["skus"][0]["attrs"] == [{"name": "规格", "value": "标准款"}]
    assert n["video"]["url"].endswith("v.mp4")
    assert n["attrs"] == [{"name": "包装规格", "value": "套装"}]


def test_parse_sale_specs_from_product_sale_attrs_and_bare_object():
    """Real package layout (10000649257498): each SKU's sale_param is empty, no thumb; the specs and
    pictures are only in info.product_sale_attrs, keyed by sku_ids (ints)."""
    import json
    product = {
        "product_key": {"product_id": 10000649257498},
        "info": {"title": "纯钛炒锅", "head_img": ["https://src.example/h.jpg"], "category": [{"cat_id": 1}],
                 "product_sale_attrs": [
                     {"key": "颜色", "items": [{"value": "26cm", "img_url": "https://src.example/26.jpg", "sku_ids": [11]},
                                              {"value": "28cm", "img_url": "https://src.example/28.jpg", "sku_ids": [12]}]},
                     {"key": "规格", "items": [{"value": "德国品质", "sku_ids": [11, 12]}]}]},
        "product_skus": [{"sku_key": {"sku_id": i}, "product_sku_info": {"sale_param": [], "sale_price": 20000,
                                                                        "stock_info": {"stock_num": 5}}}
                         for i in (11, 12)],
    }
    n = parse_package(json.dumps(product, ensure_ascii=False))
    assert [s["attrs"] for s in n["skus"]] == [[{"name": "颜色", "value": "26cm"}, {"name": "规格", "value": "德国品质"}],
                                               [{"name": "颜色", "value": "28cm"}, {"name": "规格", "value": "德国品质"}]]
    assert n["skus"][1]["thumb"] == "https://src.example/28.jpg"


def test_parse_shop_product_list_picks_the_named_product():
    import json
    prods = [{"product_key": {"product_id": pid}, "info": {"title": f"商品{pid}", "head_img": ["https://x/h.jpg"]},
              "product_skus": [{"sku_key": {"sku_id": 1}, "product_sku_info": {"sale_price": 100}}]} for pid in (7, 8)]
    inner = json.dumps({"base_resp": {"errcode": 0}, "shop_prime_product_list": {"products": prods}})
    n = parse_package(json.dumps({"data": {"data": inner}}), "8.json")
    assert n["source_product_id"] == "8" and n["title"] == "商品8"


def test_parse_rejects_page_state_dump():
    with pytest.raises(ParseError, match="data.data"):
        parse_package(b'{"data": [[2, 1, 179]]}')


def test_price_formula_and_rounding():
    cfg = TemplateConfig()  # x1.00 + 13.50
    assert apply_price(2990, cfg) == 4340
    cfg.price.rounding = "yuan"
    assert apply_price(2990, cfg) == 4300
    cfg.price.rounding, cfg.fixed_tail = "fen", True
    assert apply_price(2990, cfg) == 4390
    cfg2 = TemplateConfig()
    cfg2.price.threshold = 50  # below threshold: unchanged
    assert apply_price(2990, cfg2) == 2990


def test_drop_lure_skus():
    skus = [{"price": 100}, {"price": 2000}, {"price": 2100}]
    assert [s["price"] for s in drop_lure_skus(skus, 0.4)] == [2000, 2100]


def test_title_rules():
    cfg = TemplateConfig(ai_rewrite_title=False, title_prefix="【好物】")
    cfg.keyword_replacements = [KeywordReplace(source="神器", target="")]
    t = build_title("最新款 清洁刷 汽车 清洁刷 除雾神器 伸缩款前挡风玻璃后视镜刮水多功能清洁工具K", cfg)
    assert t.startswith("【好物】") and len(t) <= 30
    assert "最" not in t and "神器" not in t and t.count("清洁刷") == 1
    assert dedupe_keywords("清洁刷清洁刷汽车") == "清洁刷汽车"
    assert len(truncate("伸缩款汽车前挡风玻璃除雾刷后视镜刮水清洁多功能清洁刷", 12)) <= 12


def test_image_rules_and_watermark():
    imgs = list("abcdef")
    assert apply_delete(imgs, DeleteRange(enabled=True, front=1, back=2)) == ["b", "c", "d"]
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(buf, "JPEG")
    for style in ["simple_text", "half_label", "outline_badge", "tiled", "center_light", "vertical_corner"]:
        out = add_watermark(buf.getvalue(), Watermark(enabled=True, style=style, opacity=30), "测试店铺")
        assert Image.open(io.BytesIO(out)).size == (400, 400)


def test_worker_rereads_sku_specs_from_the_original_package():
    """Products imported before product_sale_attrs was read: their stored SKUs have no specs; the
    worker reads them again from the original file before publishing."""
    import json

    from app.worker import reread_sku_specs
    product = {"product_key": {"product_id": 5}, "info": {"title": "t", "head_img": ["https://x/h.jpg"],
               "product_sale_attrs": [{"key": "颜色", "items": [{"value": "红", "sku_ids": [1]},
                                                               {"value": "蓝", "sku_ids": [2]}]}]},
               "product_skus": [{"sku_key": {"sku_id": i}, "product_sku_info": {"sale_price": 100}} for i in (1, 2)]}
    raw = json.dumps(product, ensure_ascii=False)
    old = {"title": "t", "skus": [{"source_sku_id": "1", "attrs": []}, {"source_sku_id": "2", "attrs": []}]}
    assert [s["attrs"][0]["value"] for s in reread_sku_specs(old, raw)["skus"]] == ["红", "蓝"]
    assert reread_sku_specs(old, "not json") is old  # unreadable original: keep the stored data


def test_upload_video_parts_and_waits_for_transcoding():
    from app.services.wechat import WeChatClient, WeChatError
    c = WeChatClient.__new__(WeChatClient)
    calls, polls = [], []

    def post(path, body=None, **kw):
        calls.append((path, body))
        return {"data": {"video_upload_key": "K"}} if path.endswith("initupload") else {}

    def _call(method, path, params=None, files=None, **kw):
        calls.append((path, params["partnum"], len(files["media"][1])))
        return {"data": {"part_sha": f"sha{params['partnum']}"}}

    def get(path, params=None, **kw):
        polls.append(params)
        if len(polls) < 3:
            raise WeChatError(10020347, "转码中")
        return {"data": {"url": "https://173.wxapp.tc.qq.com/v.mp4"}}
    c.post, c._call, c.get = post, _call, get
    url = c.upload_video(b"x" * (5 * 1024 * 1024), "mp4", sleep=lambda s: None)
    assert url == "https://173.wxapp.tc.qq.com/v.mp4" and len(polls) == 3
    assert calls[0] == ("/shop/ec/basics/video/initupload", {"scene_type": 162, "file_type": "mp4", "file_size": 5 * 1024 * 1024})
    assert [x[1:] for x in calls[1:4]] == [(1, 2 * 1024 * 1024), (2, 2 * 1024 * 1024), (3, 1024 * 1024)]  # parts of 2 MB
    assert calls[4] == ("/shop/ec/basics/video/finishupload", {"video_upload_key": "K", "finish_parts": [
        {"partnum": 1, "part_sha": "sha1"}, {"partnum": 2, "part_sha": "sha2"}, {"partnum": 3, "part_sha": "sha3"}]})


def test_overlap_limit_when_already_over_by_other_products():
    """The store shares too much with store 2 already; a new product shared only with store 3 goes in
    (it does not add to the store-2 overlap) — it used to fail with max() of an empty list."""
    from collections import namedtuple
    from app.routers.tasks import Listings, limit_overlap
    lst = Listings(None)
    for sid, keys in {1: ["a", "b"], 2: ["a", "b"], 3: ["c", "d", "e", "f", "g"]}.items():
        lst.loaded_stores.add(sid)
        for k in keys:
            lst.add(sid, f"1688:{k}")
    lst.loaded_keys.update(f"1688:{k}" for k in "abcdefg")
    Row = namedtuple("Row", "id platform source_product_id title cover")
    picked, left = limit_overlap(lst, 1, [Row(1, "1688", "c", "", "")], None, 40)
    assert [p.source_product_id for p in picked] == ["c"] and left == 0
