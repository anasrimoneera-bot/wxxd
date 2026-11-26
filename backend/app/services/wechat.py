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
    6600120: "运费模板在这个店铺里不存在（可能是批量配置时用了别的店铺的模板），请在「发布配置」里给这个店铺重新选择运费模板",
    6600166: "退货地址在这个店铺里不存在（可能是批量配置时用了别的店铺的地址），请在「发布配置」里给这个店铺重新选择退货地址",
    40001: "AppSecret 错误或 access_token 失效",
    40013: "AppID 无效",
    40125: "AppSecret 无效",
    40164: "服务器 IP 不在小店的 API IP 白名单中，请把服务器公网 IP 加入白名单",
    45009: "接口调用超过频率限制",
    61007: "接口未授权给该账号",
}


def collect_cat_ids(d) -> set[str]:
    """Every positive id under a key like cat_id / first_cat_id / category_id, at any depth."""
    out: set[str] = set()
    if isinstance(d, dict):
        for k, v in d.items():
            kl = str(k).lower()
            if (kl.endswith("cat_id") or kl.endswith("category_id")) and not kl.startswith(("f_", "parent")):
                if isinstance(v, (int, str)) and not isinstance(v, bool) and str(v).isdigit() and int(v) > 0:
                    out.add(str(int(v)))
            out |= collect_cat_ids(v)
    elif isinstance(d, list):
        for v in d:
            out |= collect_cat_ids(v)
    return out


def is_quota_error(e: Exception) -> bool:
    return isinstance(e, WeChatError) and (e.errcode == 45009 or "quota" in (e.errmsg or "").lower())


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
            msg = data.get("errmsg", "")
            if data.get("delivery_error_msg"):  # 电子面单: the 快递公司's own reason
                msg = f"{msg}（快递公司：{data['delivery_error_msg']}）"
            raise WeChatError(code, msg, path)
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

    def freight_template_detail(self, template_id: str) -> dict:
        return self.post("/channels/ec/merchant/getfreighttemplatedetail",
                         {"template_id": str(template_id)}).get("freight_template", {})

    def add_freight_template(self, tpl: dict) -> str:
        return str(self.post("/channels/ec/merchant/addfreighttemplate", {"freight_template": tpl}).get("template_id", ""))

    def update_freight_template(self, tpl: dict) -> str:
        return str(self.post("/channels/ec/merchant/updatefreighttemplate", {"freight_template": tpl}).get("template_id", "")
                   or tpl.get("template_id", ""))

    def address_info(self, address_id: str) -> dict:
        """发货地址 of a 运费模板: the store address' address_info."""
        return (self.post("/channels/ec/merchant/address/get", {"address_id": str(address_id)})
                .get("address_detail", {}).get("address_info") or {})

    def freight_template_ids(self) -> list[str]:
        return [str(x) for x in self.post("/channels/ec/merchant/getfreighttemplatelist",
                                          {"offset": 0, "limit": 100}).get("template_id_list", [])]

    def address_ids(self) -> list[str]:
        return [str(x) for x in self.post("/channels/ec/merchant/address/list",
                                          {"offset": 0, "limit": 100}).get("address_id_list", [])]

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
    # ---- 商品视频: initupload -> uploadpart (1–2 MB parts) -> finishupload -> getplayinfo (after 转码) ----
    VIDEO_PART = 2 * 1024 * 1024
    VIDEO_MAX = 500 * 1024 * 1024  # scene 162 商品视频: 500 MB, 180 秒

    def upload_video(self, data: bytes, file_type: str = "mp4", wait: float = 90, sleep=None) -> str:
        """A 商品头图视频 uploaded to WeChat; returns the URL for head_videos.video_url (WeChat does not
        take outside links such as 1688's). Raises WeChatError, e.g. 10020324 too long (> 180 s) / too big."""
        import time
        sleep = sleep or time.sleep
        if not data:
            raise ValueError("视频是空的")
        if len(data) > self.VIDEO_MAX:
            raise ValueError("视频超过 500MB")
        key = (self.post("/shop/ec/basics/video/initupload", {"scene_type": 162, "file_type": file_type or "mp4",
                                                                 "file_size": len(data)}).get("data") or {})["video_upload_key"]
        parts = []
        for n, i in enumerate(range(0, len(data), self.VIDEO_PART), 1):
            r = self._call("POST", "/shop/ec/basics/video/uploadpart", params={"video_upload_key": key, "partnum": n},
                           files={"media": (f"part{n}", data[i:i + self.VIDEO_PART], "application/octet-stream")})
            parts.append({"partnum": n, "part_sha": (r.get("data") or {}).get("part_sha", "")})
        self.post("/shop/ec/basics/video/finishupload", {"video_upload_key": key, "finish_parts": parts})
        deadline = time.monotonic() + wait
        while True:  # 10020347: still 转码 — ask again
            try:
                url = ((self.get("/shop/ec/basics/video/getplayinfo", {"video_upload_key": key}).get("data") or {})
                       .get("url") or "")
                if url:
                    return url
            except WeChatError as e:
                if e.errcode != 10020347 or time.monotonic() > deadline:
                    raise
            if time.monotonic() > deadline:
                raise ValueError("视频转码超时")
            sleep(3)

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

    OPENED_PATHS = (
        ("post", "/channels/ec/category/get_category_relation_list"),  # 获取店铺的类目权限列表
        ("post", "/shop/ec/category/get_category_relation_list"),
        ("post", "/channels/ec/category/list/get"),  # 获取账号申请通过的类目和资质信息
    )

    def account_categories_raw(self) -> dict:
        """Raw response of every 已开通类目 endpoint (path -> response or error)."""
        out = {}
        for method, path in self.OPENED_PATHS:
            try:
                out[path] = getattr(self, method)(path, {})
            except WeChatError as e:
                out[path] = {"errcode": e.errcode, "errmsg": e.errmsg}
                if e.errcode in (40001, 42001, 40014, 40164):
                    break
        return out

    def account_categories(self) -> set[str]:
        """Category ids the shop has opened (类目权限). The response formats are not stable
        (cat_id / first_cat_id..third_cat_id / cats_v2 / nested lists), so every *cat_id /
        category_id value anywhere in the response is collected; the endpoints are merged."""
        out: set[str] = set()
        ok, last = False, None
        for method, path in self.OPENED_PATHS:
            try:
                d = getattr(self, method)(path, {})
            except WeChatError as e:
                if e.errcode in (40001, 42001, 40014, 40164, 45009):
                    raise
                last = e
                continue
            ok = True
            out |= collect_cat_ids(d)
        if not ok and last:
            raise last
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

    # ---- orders --------------------------------------------------------
    def list_orders(self, start: int, end: int, next_key: str = "", page_size: int = 100) -> dict:
        """获取订单列表 by update time (unix seconds, a window of at most 7 days).
        Returns {order_id_list, next_key, has_more}. VERIFY fields."""
        body = {"update_time_range": {"start_time": int(start), "end_time": int(end)}, "page_size": page_size}
        if next_key:
            body["next_key"] = next_key
        return self.post("/channels/ec/order/list/get", body)

    def decode_order_sensitive(self, order_id: str) -> dict:
        """解码订单包含的敏感数据: the buyer's full name / phone / address, which 获取订单详情 masks
        (郑** / 1********** / ****). Returns {address_info: {...}}. Counts against a daily quota."""
        return self.post("/channels/ec/order/sensitiveinfo/decode", {"order_id": str(order_id)})

    def apply_real_number(self, order_id: str) -> dict:
        """申请查看订单真实号码: after WeChat approves, 获取订单详情 returns the real tel_number."""
        return self.post("/channels/ec/order/realnumber/apply", {"order_id": str(order_id)})

    def real_number_audit(self, order_id: str) -> dict:
        """查看订单真实号审核状态. VERIFY fields."""
        return self.post("/channels/ec/order/realnumberviewaudit/get", {"order_id": str(order_id)})

    def delay_virtual_number(self, order_id: str) -> dict:
        """订单虚拟号延期 (from 7 days before it expires)."""
        return self.post("/channels/ec/order/virtualnumber/delay", {"order_id": str(order_id)})

    # ---- coupons (营销管理) ---------------------------------------------------
    def list_coupons(self, status: int, page: int = 1, page_ctx: str = "", page_size: int = 200) -> dict:
        """获取优惠券ID列表. Returns {coupons: [{coupon_id}], total_num, page_ctx}. VERIFY status values."""
        body = {"status": status, "page": page, "page_size": page_size}
        if page_ctx:
            body["page_ctx"] = page_ctx
        return self.post("/channels/ec/coupon/get_list", body)

    def get_coupon(self, coupon_id: str) -> dict:
        """获取优惠券详情. Returns {coupon: {type, status, coupon_info: {discount_info, ...}}}. VERIFY."""
        return self.post("/channels/ec/coupon/get", {"coupon_id": str(coupon_id)})

    # ---- delivery (物流发货) ------------------------------------------------
    def delivery_companies(self) -> list[dict]:
        """获取快递公司列表 (new version; the old one as fallback). [{delivery_id, delivery_name}]. VERIFY."""
        try:
            d = self.post("/channels/ec/order/deliverycompanylist/new/get", {})
        except WeChatError as e:
            if e.errcode in (40001, 42001, 40014, 40164, 45009):
                raise
            d = self.get("/channels/ec/order/deliverycompanylist/get")
        return d.get("company_list") or d.get("delivery_company_list") or []

    def send_delivery(self, order_id: str, delivery_list: list[dict]) -> dict:
        """订单发货. delivery_list: [{delivery_id, waybill_id, deliver_type (1 快递 / 3 无需物流),
        product_infos: [{product_id, sku_id, product_cnt}]}]."""
        return self.post("/channels/ec/order/delivery/send", {"order_id": str(order_id), "delivery_list": delivery_list})

    def compensate_delivery(self, order_id: str, delivery_list: list[dict], reason: str = "") -> dict:
        """订单补发货 (漏发 / 拆包 / 坏损 / 赠品; at most 10 times per order). VERIFY fields."""
        body = {"order_id": str(order_id), "delivery_list": delivery_list}
        if reason:
            body["reason"] = reason
        return self.post("/channels/ec/order/delivery/compensation", body)

    def get_order(self, order_id: str) -> dict:
        """获取订单详情. Returns {order: {...}}. VERIFY fields."""
        return self.post("/channels/ec/order/get", {"order_id": str(order_id)})

    # ---- products ------------------------------------------------------
    def add_product(self, body: dict) -> str:
        d = self.post("/channels/ec/product/add", body)
        return str((d.get("data") or {}).get("product_id") or d.get("product_id"))

    def listing(self, product_id: str) -> None:
        self.post("/channels/ec/product/listing", {"product_id": product_id})

    # ---- product management (商品管理) ---------------------------------------
    def compass_products(self, ds: str, offset: int = 0, limit: int = 100) -> dict:
        """罗盘 商品数据 of one day (YYYYMMDD): {product_list: [{product_id, data: {product_click_cnt, ...}}], total_count}."""
        return self.post("/channels/ec/compass/shop/product/list/get", {"ds": ds, "offset": offset, "limit": limit})

    def list_products(self, next_key: str = "", page_size: int = 30, status: int | None = None) -> dict:
        """One page of the store's product ids. Returns {product_ids, next_key, total_num}. status: only
        products in that state (0 = 草稿箱, products not yet submitted); None = the default list. VERIFY."""
        body = {"page_size": page_size}
        if status is not None:
            body["status"] = status
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

    # ---- 电子面单 (打单): 面单账号, 模板, 预取号, 取号 (with the print_info for the 打印组件), 打印通知 ----
    EWB = "/channels/ec/logistics/ewaybill/biz"

    def ewaybill_accounts(self) -> list[dict]:
        return self.post(f"{self.EWB}/account/get", {"need_balance": False, "limit": 50, "status": 3}).get("account_list") or []

    def ewaybill_templates(self) -> list[dict]:
        return self.post(f"{self.EWB}/template/get", {}).get("total_template") or []

    def ewaybill_precreate(self, body: dict) -> str:
        return str(self.post(f"{self.EWB}/order/precreate", body).get("ewaybill_order_id") or "")

    def ewaybill_create(self, body: dict) -> dict:
        """取号: {ewaybill_order_id, waybill_id, print_info (with template_id), ...}."""
        return self.post(f"{self.EWB}/order/create", body)

    def ewaybill_print_content(self, ewaybill_order_id: str, template_id: str) -> str:
        return self.post(f"{self.EWB}/print/get", {"ewaybill_order_id": ewaybill_order_id,
                                                   "template_id": template_id}).get("print_info") or ""

    def ewaybill_printed(self, ewaybill_order_id: str, delivery_id: str, waybill_id: str, re_print: bool) -> None:
        """打印成功通知 (the 订单发货 is a separate call)."""
        self.post(f"{self.EWB}/order/print", {"ewaybill_order_id": ewaybill_order_id, "delivery_id": delivery_id,
                                              "waybill_id": waybill_id, "re_print": 1 if re_print else 0})

    def ewaybill_cancel(self, ewaybill_order_id: str, delivery_id: str, waybill_id: str) -> None:
        """回收面单 (取消下单): the 面单 number goes back to the 网点; errors carry delivery_error_msg."""
        self.post(f"{self.EWB}/order/cancel", {"ewaybill_order_id": str(ewaybill_order_id), "delivery_id": delivery_id,
                                               "waybill_id": waybill_id})

    def dropship_get(self, ds_order_id: str) -> dict:
        """代发单详情: status (100 待发货 199 部分发货 200 已发货 300 已取消), supplier_name, order_id."""
        return self.post("/channels/ec/order/dropship/get", {"ds_order_id": str(ds_order_id)}).get("dropship_order") or {}

    def send_kf_text(self, openid: str, text: str) -> dict:
        """买家消息: reply to a buyer (商家客服, API mode). Allowed within 48 h of the buyer's last message,
        5 replies at most until the buyer writes again (commkf/sendmsg: open_id, msg_type, text.content)."""
        return self.post("/channels/ec/commkf/sendmsg", {"open_id": openid, "msg_type": "text", "text": {"content": text}})

    def delisting(self, product_id: str) -> None:
        self.post("/channels/ec/product/delisting", {"product_id": str(product_id)})

    def delete_product(self, product_id: str) -> None:
        self.post("/channels/ec/product/delete", {"product_id": str(product_id)})


def download(url: str, timeout: float = 60, referer: str = "") -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0 Safari/537.36"}
    if referer:
        headers["Referer"] = referer
    r = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
    r.raise_for_status()
    if not r.content:
        raise ValueError("empty image")
    return r.content
