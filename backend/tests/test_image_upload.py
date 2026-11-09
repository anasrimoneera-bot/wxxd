import io

from PIL import Image

from app.services import publisher
from app.services.images import MAX_UPLOAD_BYTES, normalize_for_upload
from app.services.publisher import PublishFailed, StoreContext, publish
from app.services.template_config import TemplateConfig
from app.services.wechat import WeChatError

from .conftest import FakeWeChat, make_package
from app.services.parser import parse_package


def _img(fmt="JPEG", mode="RGB", size=(800, 800)):
    buf = io.BytesIO()
    Image.new(mode, size, (200, 100, 50, 128) if mode == "RGBA" else "white").save(buf, fmt)
    return buf.getvalue()


def test_normalize_converts_and_limits_size():
    for data in (_img("WEBP"), _img("PNG", "RGBA"), _img("PNG", "P")):
        out = normalize_for_upload(data)
        assert Image.open(io.BytesIO(out)).format == "JPEG"
    import os
    noisy = Image.frombytes("RGB", (3000, 3000), os.urandom(3000 * 3000 * 3))
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    assert len(normalize_for_upload(buf.getvalue())) <= MAX_UPLOAD_BYTES


class UrlRejectingWeChat(FakeWeChat):
    url_calls = 0

    def upload_image_url(self, url):
        UrlRejectingWeChat.url_calls += 1
        raise WeChatError(10020061, "图片URL非法")


def test_url_rejected_switches_to_download_and_file_upload(monkeypatch):
    downloads = []
    monkeypatch.setattr(publisher, "download", lambda url: downloads.append(url) or _img("WEBP"))
    client = UrlRejectingWeChat("wx1234567890", "s")
    ctx = StoreContext(client=client, store_name="店", freight_template_id="111", address_id="222")
    norm = parse_package(make_package("9", "伸缩款汽车除雾刷"))
    res = publish(norm, TemplateConfig(ai_rewrite_title=False), ctx, lambda n: False)
    assert res.wx_product_id
    assert UrlRejectingWeChat.url_calls == 1  # rejected once, then never tried again for this store
    assert not ctx.url_upload_ok
    assert len(downloads) == len(set(downloads)) >= 5  # main + detail + sku images, each once
    assert client.uploaded_bytes > 0


def test_failure_keeps_category(monkeypatch):
    def broken(url):
        raise OSError("403 Forbidden")
    monkeypatch.setattr(publisher, "download", broken)
    ctx = StoreContext(client=UrlRejectingWeChat("wx1234567890", "s"), store_name="店",
                       freight_template_id="111", address_id="222")
    norm = parse_package(make_package("9", "伸缩款汽车除雾刷"))
    try:
        publish(norm, TemplateConfig(ai_rewrite_title=False), ctx, lambda n: False)
        raise AssertionError("should fail")
    except PublishFailed as e:
        assert "图片下载失败" in str(e) and e.wx_category == "洗车刷（6422）"
