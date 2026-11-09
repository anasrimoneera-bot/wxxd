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


def test_upload_bytes_falls_back_to_multipart_and_remembers():
    WeChatClient._bytes_mode = None
    seen = []

    def handler(req: httpx.Request):
        if req.url.path == "/cgi-bin/stable_token":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        ctype = req.headers.get("content-type", "")
        seen.append("multipart" if ctype.startswith("multipart/") else "raw")
        assert req.url.params["upload_type"] == "0"
        if not ctype.startswith("multipart/"):
            return httpx.Response(200, json={"errcode": 41005, "errmsg": "media data missing"})
        assert b'name="media"' in req.content
        return httpx.Response(200, json={"errcode": 0, "pic_file": {"img_url": "https://mmecimage.cn/p/1"}})

    c = make_client(handler)
    assert c.upload_image_bytes(b"\xff\xd8jpegdata") == "https://mmecimage.cn/p/1"
    assert c.upload_image_bytes(b"\xff\xd8jpegdata") == "https://mmecimage.cn/p/1"
    assert seen == ["raw", "multipart", "multipart"]  # second upload goes straight to multipart
    WeChatClient._bytes_mode = None
