"""WeChat Store (微信小店) server API client.

Every endpoint used by the system lives here, so a change in WeChat's API only needs
changes in this file. Endpoint paths and fields follow the official 微信小店 server API
docs. Items marked VERIFY must be checked against the current docs on a test store
before going live.
"""
import threading
import time

import httpx

from ..config import get_settings

_token_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


class WeChatError(Exception):
    def __init__(self, errcode: int, errmsg: str, path: str = ""):
        self.errcode, self.errmsg, self.path = errcode, errmsg, path
        super().__init__(f"微信接口错误 {errcode}: {errmsg}")


ERROR_HINTS = {
    40001: "AppSecret 错误或 access_token 失效",
    40013: "AppID 无效",
    40125: "AppSecret 无效",
    40164: "服务器 IP 不在小店的 API IP 白名单中，请把服务器公网 IP 加入白名单",
    45009: "接口调用超过频率限制",
    61007: "接口未授权给该账号",
}


def explain(e: Exception) -> str:
    if isinstance(e, WeChatError):
        hint = ERROR_HINTS.get(e.errcode)
        return f"{e.errmsg}（{e.errcode}{'：' + hint if hint else ''}）"
    return str(e)


class WeChatClient:
    def __init__(self, appid: str, secret: str, http: httpx.Client | None = None):
        self.appid, self.secret = appid, secret
        self.base = get_settings().wechat_api_base.rstrip("/")
        self.http = http or httpx.Client(timeout=30)

    # ---- token ---------------------------------------------------------
    def token(self, force: bool = False) -> str:
        with _lock:
            cached = _token_cache.get(self.appid)
            if cached and not force and cached[1] > time.time() + 300:
                return cached[0]
        r = self.http.post(
            f"{self.base}/cgi-bin/stable_token",
            json={"grant_type": "client_credential", "appid": self.appid, "secret": self.secret,
                  "force_refresh": False},
        )
        data = r.json()
        if "access_token" not in data:
            raise WeChatError(data.get("errcode", -1), data.get("errmsg", "获取 access_token 失败"), "stable_token")
        with _lock:
            _token_cache[self.appid] = (data["access_token"], time.time() + int(data.get("expires_in", 7200)))
        return data["access_token"]

    def _call(self, method: str, path: str, *, json=None, params=None, content=None, files=None, retry=True):
        p = dict(params or {})
        p["access_token"] = self.token()
        r = self.http.request(method, f"{self.base}{path}", params=p, json=json, content=content, files=files)
        data = r.json()
        code = data.get("errcode", 0)
        if code in (40001, 42001, 40014) and retry:
            self.token(force=True)
            return self._call(method, path, json=json, params=params, content=content, files=files, retry=False)
        if code:
            raise WeChatError(code, data.get("errmsg", ""), path)
        return data

    def post(self, path: str, body: dict | None = None, **kw):
        return self._call("POST", path, json=body or {}, **kw)

    # ---- shop ----------------------------------------------------------
    def shop_info(self) -> dict:
        return self.post("/channels/ec/basics/info/get").get("info", {})

    # ---- freight templates & addresses ---------------------------------
    def freight_templates(self) -> list[dict]:
        ids = self.post("/channels/ec/merchant/getfreighttemplatelist", {"offset": 0, "limit": 100}).get(
            "template_id_list", [])
        out = []
        for tid in ids:
            d = self.post("/channels/ec/merchant/getfreighttemplatedetail", {"template_id": tid}).get(
                "freight_template", {})
            out.append({"id": str(tid), "name": d.get("name", str(tid))})
        return out

    def addresses(self) -> list[dict]:
        ids = self.post("/channels/ec/merchant/address/list", {"offset": 0, "limit": 100}).get("address_id_list", [])
        out = []
        for aid in ids:
            d = self.post("/channels/ec/merchant/address/get", {"address_id": aid}).get("address_detail", {})
            addr = d.get("address_info") or {}
            text = d.get("name") or "".join(
                addr.get(k, "") for k in ("province_name", "city_name", "county_name", "detail_info"))
            out.append({"id": str(aid), "name": text or str(aid)})
        return out

    # ---- media ---------------------------------------------------------
    def upload_image_url(self, url: str) -> str:
        d = self.post("/channels/ec/basics/img/upload", {"img_url": url}, params={"upload_type": 1, "resp_type": 1})
        return d["pic_file"]["img_url"]

    def upload_image_bytes(self, data: bytes) -> str:
        d = self._call("POST", "/channels/ec/basics/img/upload", content=data,
                       params={"upload_type": 0, "resp_type": 1})
        return d["pic_file"]["img_url"]

    # ---- categories ----------------------------------------------------
    def category_detail(self, cat_id: str) -> dict:
        return self.post("/channels/ec/category/detail", {"cat_id": int(cat_id)})

    def account_categories(self) -> set[str]:
        """Leaf categories the shop may publish in. VERIFY endpoint/fields."""
        d = self.post("/channels/ec/category/list/get", {})
        out = set()
        for c in d.get("cats_list", d.get("cats", [])):
            cid = c.get("cat_id") or (c.get("cats") or [{}])[-1].get("cat_id")
            if cid:
                out.add(str(cid))
        return out

    def apply_category(self, cat_ids: list[str]) -> str:
        """Submit a category application (no qualification files). Returns audit id. VERIFY body."""
        cats = [{"level": i + 1, "cat_id": int(c)} for i, c in enumerate(cat_ids)]
        d = self.post("/channels/ec/category/add", {"category_info": {"cats_v2": cats, "certificate": []}})
        return str(d.get("audit_id", ""))

    # ---- products ------------------------------------------------------
    def add_product(self, body: dict) -> str:
        d = self.post("/channels/ec/product/add", body)
        return str((d.get("data") or {}).get("product_id") or d.get("product_id"))

    def listing(self, product_id: str) -> None:
        self.post("/channels/ec/product/listing", {"product_id": product_id})


def download(url: str, timeout: float = 60) -> bytes:
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.content
