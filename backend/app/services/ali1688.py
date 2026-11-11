"""1688 official AI gateway client (image search / 找货源).

Protocol (from 1688's published agent skill `next-1688/1688-product-find`, re-implemented here):
  POST https://gateway.1688.com/api/alibaba.1688.find.product/1.0.0/{channel}
  JSON body; headers signed with HMAC-SHA256 using the AK from clawhub.1688.com.
  AK = base64url(secret[32] + key_id).
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qs, quote, urlparse

import httpx

BASE_URL = "https://gateway.1688.com"
FIND_PRODUCT_API = "/api/alibaba.1688.find.product/1.0.0"
DEFAULT_CHANNEL = "github"  # channel suffix used by the published skill; configurable in 系统设置
SKILL_CODE = "1688-product-find"
SKILL_VERSION = "1.7.0"
CONTENT_TYPE = "application/json"


class Ali1688Error(Exception):
    pass


ERROR_TEXT = {
    "SignatureInvalid": "签名校验失败：AK 不正确或已失效",
    "ParamMissing": "缺少必填参数",
    "APIUnsupported": "接口不可用",
    "QosAppFrequencyLimit": "1688 调用频率超限，请稍后再试",
    "QosApiFrequencyLimit": "1688 调用频率超限，请稍后再试",
    "ISPInvokeError": "1688 后端服务错误",
    "ISPInvokeTimeout": "1688 后端服务超时",
}


def split_ak(raw: str) -> tuple[str, str]:
    """Return (key_id, secret) from the AK copied from clawhub.1688.com."""
    raw = (raw or "").strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        if decoded[32:]:
            return decoded[32:], decoded[:32]
    except Exception:
        pass
    if len(raw) > 32:
        return raw[32:], raw[:32]
    raise Ali1688Error("AK 格式不正确，请从 clawhub.1688.com 右上角 🔑 重新复制")


def _content_md5(body: str) -> str:
    return base64.b64encode(hashlib.md5(body.encode("utf-8")).digest()).decode() if body else ""


def _canonical_resource(uri: str) -> str:
    u = urlparse(uri)
    path = u.path or "/"
    if not u.query:
        return path
    pairs = []
    for key, values in sorted(parse_qs(u.query, keep_blank_values=True).items()):
        for v in sorted(values):
            pairs.append(f"{quote(key, safe='')}={quote(v, safe='')}")
    return f"{path}?{'&'.join(pairs)}"


def sign_headers(method: str, uri: str, body: str, key_id: str, secret: str,
                 timestamp: str | None = None, nonce: str | None = None) -> dict:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex[:8]
    md5 = _content_md5(body)
    csk = {"x-csk-ak": key_id, "x-csk-time": timestamp, "x-csk-nonce": nonce,
           "x-csk-content-md5": md5, "x-csk-version": SKILL_VERSION}
    canonical_headers = "".join(f"{k.lower()}:{csk[k].strip()}\n" for k in sorted(csk))
    to_sign = f"{method.upper()}\n{md5}\n{CONTENT_TYPE}\n{timestamp}\n{canonical_headers}{_canonical_resource(uri)}"
    sig = base64.b64encode(hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).digest()).decode()
    return {"Content-Type": CONTENT_TYPE, "x-csk-sign": sig, **csk}


def _count(v) -> int:
    """Sales/stock counts may arrive as numbers or strings ("2000", "1.2万", "100+")."""
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    t = str(v).strip().replace("+", "").replace(",", "")
    try:
        return int(float(t[:-1]) * 10000) if t.endswith("万") else int(float(t))
    except ValueError:
        return 0


def parse_offer(item: dict) -> dict:
    offer_id = str(item.get("itemId") or "")
    price = item.get("currentPrice")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    tags = [t if isinstance(t, str) else (t.get("text") or t.get("name") or "")
            for t in (item.get("promotionTags") or []) + (item.get("serviceInfos") or [])]
    return {
        "offer_id": offer_id,
        "title": item.get("title") or "",
        "image_url": item.get("imageUrl") or "",
        "detail_url": item.get("detailUrl") or (f"https://detail.1688.com/offer/{offer_id}.html" if offer_id else ""),
        "price": price,  # 货价 (CNY), shipping NOT included
        "moq": item.get("quantityBegin"),
        "unit": item.get("unit") or "",
        "sold": _count(item.get("soldOut")),
        "stock": _count(item.get("storeAmount")),
        "supplier": item.get("company") or "",
        "score": float(item.get("score") or 0),
        "sku_title": item.get("skuTitle") or "",
        "tags": [t for t in tags if t][:6],
    }


class Ali1688Client:
    def __init__(self, ak: str, channel: str = DEFAULT_CHANNEL, http: httpx.Client | None = None):
        self.key_id, self.secret = split_ak(ak)
        self.channel = channel or DEFAULT_CHANNEL
        self.http = http or httpx.Client(timeout=30)

    def _post(self, api: str, body: dict) -> dict:
        path = f"{api}/{self.channel}"
        body_str = json.dumps(body, ensure_ascii=False)
        headers = sign_headers("POST", path, body_str, self.key_id, self.secret)
        headers.update({"x-skill-code": SKILL_CODE, "x-skill-version": SKILL_VERSION,
                        "x-request-id": uuid.uuid4().hex})
        try:
            r = self.http.post(BASE_URL + path, headers=headers, content=body_str.encode("utf-8"))
        except httpx.HTTPError as e:
            raise Ali1688Error(f"无法连接 1688：{e}") from None
        if r.status_code != 200:
            raise Ali1688Error(f"1688 网关异常（HTTP {r.status_code}）")
        data = r.json()
        if data.get("success") is False:
            code = str(data.get("code") or "")
            msg_code = str(data.get("msgCode") or "")
            raise Ali1688Error(ERROR_TEXT.get(code) or data.get("msgInfo") or msg_code or code or "1688 返回错误")
        model = data.get("model") if isinstance(data.get("model"), dict) else data.get("data")
        if not isinstance(model, dict):
            raise Ali1688Error("1688 返回结构异常")
        return model

    def image_search(self, *, image_url: str = "", image_base64: str = "", page_size: int = 20,
                     purchase_amount: int = 1, sort_type: str | None = None) -> list[dict]:
        body = {"imgBase64": image_base64, "imageUrl": image_url or None, "pageSize": page_size,
                "purchaseAmount": purchase_amount, "scoreLevel": "high", "tags": "4306497"}
        if sort_type:
            body["sortType"] = sort_type  # price_asc / price_desc / sold_desc / yx_desc
        model = self._post(FIND_PRODUCT_API, body)
        items = model.get("data")
        if not isinstance(items, list):
            raise Ali1688Error("1688 返回结构异常")
        return [parse_offer(i) for i in items]
