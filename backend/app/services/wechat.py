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

    def _call(self, method: str, path: str, *, json=None, params=None, content=None, files=None, retry=True,
              method_retry=True):
        p = dict(params or {})
        p["access_token"] = self.token()
        send_json = json
        if method == "GET" and json:  # GET endpoints take their arguments as query parameters
            p.update({k: v for k, v in json.items() if v is not None})
            send_json = None  # keep `json` itself intact for a POST retry
        r = self.http.request(method, f"{self.base}{path}", params=p, json=send_json, content=content, files=files)
        data = r.json()
        code = data.get("errcode", 0)
        if code in (40001, 42001, 40014) and retry:
            self.token(force=True)
            return self._call(method, path, json=json, params=params, content=content, files=files, retry=False,
                              method_retry=method_retry)
        # 43001 = "require GET method", 43002 = "require POST method": retry once with the other method.
        if code in (43001, 43002) and method_retry and content is None and files is None:
            other = "GET" if code == 43001 else "POST"
            if other != method:
                return self._call(other, path, json=json, params=params, retry=retry, method_retry=False)
        if code:
            raise WeChatError(code, data.get("errmsg", ""), path)
        return data

    def get(self, path: str, params: dict | None = None, **kw):
        return self._call("GET", path, params=params, **kw)

    def post(self, path: str, body: dict | None = None, **kw):
        return self._call("POST", path, json=body or {}, **kw)

    # ---- shop ----------------------------------------------------------
    def shop_info(self) -> dict:
        return self.get("/channels/ec/basics/info/get").get("info", {})  # confirmed: GET only (43001 on POST)

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
        """Upload an image file. Per the official 上传图片 docs: upload_type=0 needs the image's
        height and width (pixels) as query parameters and the file in the form field "media"."""
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
        d = self._call("POST", "/channels/ec/basics/img/upload",
                       params={"upload_type": 0, "resp_type": 1, "height": height, "width": width},
                       files={"media": ("image.jpg", data, "image/jpeg")})
        return d["pic_file"]["img_url"]

    # ---- categories ----------------------------------------------------
    def category_detail(self, cat_id: str) -> dict:
        return self.post("/channels/ec/category/detail", {"cat_id": int(cat_id)})

    def all_categories(self) -> dict:
        """获取所有类目 (GET /channels/ec/category/all): the whole category tree.
        cats_v2 (new multi-level tree, leaf=true marks a leaf) and cats (old 3-level tree);
        each item: {cat: {cat_id, f_cat_id, name, level, leaf}, qua: {qua_id, need_to_apply, tips, mandatory}}."""
        return self.get("/channels/ec/category/all")

    def category_deposit_rule(self, cat_id: str) -> dict:
        """获取保证金类目规则 (getcategoryrule, rule_id=1): the category's 保证金 in fen."""
        last: Exception | None = None
        for path in ("/channels/ec/category/getcategoryrule", "/shop/ec/category/getcategoryrule"):
            try:
                return self.post(path, {"category_id": int(cat_id)}, params={"rule_id": 1})
            except WeChatError as e:
                if e.errcode in (40001, 42001, 40014, 40164, 45009):
                    raise
                last = e
        raise last  # type: ignore[misc]

    def account_categories(self) -> set[str]:
        """Leaf categories the shop may publish in. VERIFY endpoint/fields."""
        d = self.post("/channels/ec/category/list/get", {})
        out = set()
        for c in d.get("cats_list", d.get("cats", [])):
            cid = c.get("cat_id") or (c.get("cats") or [{}])[-1].get("cat_id")
            if cid:
                out.add(str(cid))
        return out

    _apply_variant: int | None = None

    def apply_category(self, cat_ids: list[str]) -> str:
        """Submit a category application without qualification files. Returns the audit id.
        Uses the new application protocol (申请类目 docs): is_new_apply_cat=true with
        license_group_list instead of the deprecated certificate (old format -> 9700250).
        level1..level3 are required; cats_v2 carries the full tree (up to 4 levels)."""
        ids = [int(c) for c in cat_ids]
        levels = {f"level{i + 1}": c for i, c in enumerate(ids[:3])}
        base = {"is_new_apply_cat": True, "license_group_list": []}
        variants = [
            {"category_info": {**levels, "cats_v2": [{"cat_id": c} for c in ids], **base}},
            {"category_info": {**levels, **base}},
        ]
        order = [self._apply_variant] if self._apply_variant is not None else range(len(variants))
        last: Exception | None = None
        for i in order:
            try:
                d = self.post("/channels/ec/category/add", variants[i])
                WeChatClient._apply_variant = i
                return str(d.get("audit_id") or d.get("audit_id_list") or "")
            except WeChatError as e:
                if e.errcode in (40001, 42001, 40014, 40164, 45009):
                    raise
                last = e
        raise last  # type: ignore[misc]

    # ---- products ------------------------------------------------------
    def add_product(self, body: dict) -> str:
        d = self.post("/channels/ec/product/add", body)
        return str((d.get("data") or {}).get("product_id") or d.get("product_id"))

    def listing(self, product_id: str) -> None:
        self.post("/channels/ec/product/listing", {"product_id": product_id})

    # ---- product management (商品管理) ---------------------------------------
    def list_products(self, next_key: str = "", page_size: int = 30) -> dict:
        """One page of the store's product ids. Returns {product_ids, next_key, total_num}. VERIFY fields."""
        body = {"page_size": page_size}
        if next_key:
            body["next_key"] = next_key
        return self.post("/channels/ec/product/list/get", body)

    def get_product(self, product_id: str) -> dict:
        """Online version ("product") and draft ("edit_product"); data_type 3 = both. VERIFY."""
        return self.post("/channels/ec/product/get", {"product_id": str(product_id), "data_type": 3})

    def update_prices(self, product_id: str, skus: list[dict]) -> None:
        """免审更新商品: change SKU prices of a listed product without re-review.
        skus: [{"sku_id": ..., "sale_price": fen}]."""
        self.post("/channels/ec/product/auditfree", {"product_id": str(product_id), "skus": skus})

    STOCK_ADD, STOCK_SUB, STOCK_SET = 1, 2, 3

    def update_stock(self, product_id: str, sku_id: str, diff_type: int, num: int) -> None:
        """快速更新库存: diff_type 1 = add num, 2 = subtract num, 3 = set to num."""
        self.post("/channels/ec/product/stock/update", {"product_id": str(product_id), "sku_id": str(sku_id),
                                                        "diff_type": diff_type, "num": int(num)})

    def delisting(self, product_id: str) -> None:
        self.post("/channels/ec/product/delisting", {"product_id": str(product_id)})

    def delete_product(self, product_id: str) -> None:
        self.post("/channels/ec/product/delete", {"product_id": str(product_id)})


def download(url: str, timeout: float = 60) -> bytes:
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36"})
    r.raise_for_status()
    if not r.content:
        raise ValueError("empty image")
    return r.content
