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
