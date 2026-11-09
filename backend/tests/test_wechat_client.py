import json

import httpx

from app.services import wechat
from app.services.wechat import WeChatClient


def make_client(handler):
    wechat._token_cache.clear()
    return WeChatClient("wxappid0001", "secret", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_shop_info_uses_get():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": "TOKEN", "expires_in": 7200})
        if req.method != "GET":
            return httpx.Response(200, json={"errcode": 43001, "errmsg": "require GET method"})
        assert req.url.params["access_token"] == "TOKEN"
        return httpx.Response(200, json={"errcode": 0, "info": {"nickname": "测试小店"}})

    info = make_client(handler).shop_info()
    assert info["nickname"] == "测试小店"
    assert ("GET", "/channels/ec/basics/info/get") in calls
    assert ("POST", "/channels/ec/basics/info/get") not in calls


def test_method_fallback_both_ways():
    seen = []

    def handler(req: httpx.Request):
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        seen.append(req.method)
        if req.url.path == "/needs-get":
            if req.method == "POST":
                return httpx.Response(200, json={"errcode": 43001, "errmsg": "require GET method"})
            assert req.url.params["offset"] == "0"  # body moved to query string
            return httpx.Response(200, json={"errcode": 0, "ok": 1})
        if req.method == "GET":
            return httpx.Response(200, json={"errcode": 43002, "errmsg": "require POST method"})
        assert json.loads(req.content) == {"x": 1}
        return httpx.Response(200, json={"errcode": 0, "ok": 2})

    c = make_client(handler)
    assert c.post("/needs-get", {"offset": 0})["ok"] == 1
    assert c.get("/needs-post", None, json={"x": 1})["ok"] == 2
    assert seen == ["POST", "GET", "GET", "POST"]


def test_expired_token_refreshes_once():
    tokens = iter(["OLD", "NEW"])

    def handler(req: httpx.Request):
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 7200})
        if req.url.params["access_token"] == "OLD":
            return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid credential"})
        return httpx.Response(200, json={"errcode": 0, "info": {"nickname": "x"}})

    assert make_client(handler).shop_info()["nickname"] == "x"


def test_upload_bytes_sends_size_and_media_field():
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "JPEG")
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        seen.update(dict(req.url.params), ctype=req.headers.get("content-type", ""), body=req.content)
        return httpx.Response(200, json={"errcode": 0, "pic_file": {"img_url": "https://mmecimage.cn/p/1"}})

    assert make_client(handler).upload_image_bytes(buf.getvalue()) == "https://mmecimage.cn/p/1"
    assert seen["upload_type"] == "0" and seen["resp_type"] == "1"
    assert seen["width"] == "800" and seen["height"] == "600"  # required for binary uploads
    assert seen["ctype"].startswith("multipart/form-data") and b'name="media"' in seen["body"]


def test_apply_category_uses_new_protocol():
    WeChatClient._apply_variant = None
    bodies = []

    def handler(req: httpx.Request):
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        info = json.loads(req.content)["category_info"]
        bodies.append(info)
        assert info["is_new_apply_cat"] is True and info["license_group_list"] == []
        assert "certificate" not in info  # old protocol is rejected with 9700250
        assert (info["level1"], info["level2"], info["level3"]) == (1001, 1002, 1710)
        if "cats_v2" in info:
            return httpx.Response(200, json={"errcode": 47001, "errmsg": "data format error"})
        return httpx.Response(200, json={"errcode": 0, "audit_id": "123456"})

    c = make_client(handler)
    assert c.apply_category(["1001", "1002", "1710"]) == "123456"
    assert c.apply_category(["1001", "1002", "1710"]) == "123456"
    assert len(bodies) == 3  # second call goes straight to the format that worked
    WeChatClient._apply_variant = None
