"""微信小店联盟官方接口客户端。

只调用官方 HTTPS 接口，不涉及任何微信客户端进程操作。
文档：https://developers.weixin.qq.com/doc/store/ （小店·联盟带货）
"""
from __future__ import annotations
import time
from typing import Any, Iterator, Optional

try:
    import requests
except ImportError:  # 允许无 requests 时仍能导入做样例演示
    requests = None  # type: ignore

BASE = "https://api.weixin.qq.com"


class WxShopApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"微信接口错误 {code}: {msg}")
        self.code, self.msg = code, msg


class LeagueClient:
    """联盟带货客户端。

    用法：
        c = LeagueClient(access_token="...")           # 直接给 token
        c = LeagueClient(appid="...", secret="...")     # 或让它自己换 token
        for p in c.iter_products():
            detail = c.get_product_detail(p["product_id"])
    """

    def __init__(self, access_token: str = "", appid: str = "",
                 secret: str = "", timeout: int = 15):
        self._token = access_token
        self._appid = appid
        self._secret = secret
        self._token_exp = 0.0
        self.timeout = timeout

    # --- 鉴权 ---
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        if self._token and not self._secret:
            return self._token  # 外部传入的 token，自己负责有效期
        if not (self._appid and self._secret):
            raise WxShopApiError(-1, "缺少 access_token，或 appid/secret")
        data = self._raw_get("/cgi-bin/token", {
            "grant_type": "client_credential",
            "appid": self._appid, "secret": self._secret,
        })
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 7200)) - 300
        return self._token

    # --- 底层请求 ---
    def _raw_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if requests is None:
            raise WxShopApiError(-1, "未安装 requests")
        r = requests.get(BASE + path, params=params, timeout=self.timeout)
        return self._check(r.json())

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if requests is None:
            raise WxShopApiError(-1, "未安装 requests")
        r = requests.post(BASE + path, params={"access_token": self._access_token()},
                          json=body, timeout=self.timeout)
        return self._check(r.json())

    @staticmethod
    def _check(data: dict[str, Any]) -> dict[str, Any]:
        code = data.get("errcode", 0)
        if code:
            raise WxShopApiError(code, data.get("errmsg", ""))
        return data

    # --- 业务接口 ---
    def list_products(self, last_buffer: str = "", page_size: int = 100,
                      extra: Optional[dict] = None) -> dict[str, Any]:
        """联盟商品列表（单页）。返回含 items / last_buffer / continue_flag。"""
        body = {"page_size": min(page_size, 100)}
        if last_buffer:
            body["last_buffer"] = last_buffer
        if extra:
            body.update(extra)
        return self._post("/channels/ec/league/item/list/get", body)

    def iter_products(self, page_size: int = 100, max_items: int = 0,
                     extra: Optional[dict] = None) -> Iterator[dict[str, Any]]:
        """自动翻页，逐个产出商品原始结构。"""
        buf, got = "", 0
        while True:
            data = self.list_products(buf, page_size, extra)
            items = data.get("items", data.get("item_list", []) or [])
            if not items:
                break
            for it in items:
                yield it
                got += 1
                if max_items and got >= max_items:
                    return
            buf = data.get("last_buffer", "")
            if not data.get("continue_flag") or not buf:
                break

    def get_product_detail(self, product_id: str, promote_type: int = 1,
                          info_id: str = "") -> dict[str, Any]:
        """联盟商品详情。promote_type: 普通推广传 product_id，特殊推广传 info_id。"""
        body: dict[str, Any] = {"type": promote_type}
        if info_id:
            body["info_id"] = info_id
        else:
            body["product_id"] = product_id
        data = self._post("/channels/ec/league/item/get", body)
        return data.get("item", data.get("product", data))
