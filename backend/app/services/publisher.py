"""Publish one library product to one store using a publish template (上货模板)."""
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

from .images import add_watermark, apply_delete, normalize_for_upload
from .pricing import apply_price, drop_lure_skus
from .template_config import TemplateConfig
from .titles import build_title, clean_sku_value, short_title
from .wechat import WeChatClient, WeChatError, download, explain

log = logging.getLogger(__name__)

MAX_HEAD_IMGS = 9
MAX_DETAIL_IMGS = 20


class PublishFailed(Exception):
    """Business failure shown to the user as the item's failure reason."""


class Skipped(Exception):
    pass


@dataclass
class Result:
    wx_product_id: str = ""
    wx_category: str = ""
    message: str = ""
    title: str = ""


@dataclass
class StoreContext:
    """Per-store caches kept by a worker between items."""
    client: WeChatClient
    store_name: str
    freight_template_id: str
    address_id: str
    categories: set[str] | None = None
    categories_at: float = 0
    category_details: dict = field(default_factory=dict)
    image_cache: dict = field(default_factory=dict)
    applied: dict = field(default_factory=dict)  # leaf cat_id -> audit id submitted by this worker
    url_upload_ok: bool = True  # False once WeChat rejects fetching source URLs (10020061)

    def allowed_categories(self) -> set[str] | None:
        if self.categories is None or time.time() - self.categories_at > 600:
            try:
                self.categories = self.client.account_categories()
            except Exception as e:  # endpoint unavailable: let product/add decide
                log.warning("account_categories failed: %s", e)
                self.categories = set()
            self.categories_at = time.time()
        return self.categories or None

    def detail(self, cat_id: str) -> dict:
        if cat_id not in self.category_details:
            try:
                self.category_details[cat_id] = self.client.category_detail(cat_id)
            except Exception as e:
                log.warning("category_detail %s failed: %s", cat_id, e)
                self.category_details[cat_id] = {}
        return self.category_details[cat_id]


def title_hash(title: str) -> str:
    norm = re.sub(r"[\s\W_]+", "", title.lower())
    return hashlib.sha256(norm.encode()).hexdigest()


def check_skus(norm: dict) -> None:
    skus = norm["skus"]
    if not skus:
        raise PublishFailed("数据包未提供 SKU，无法发布")
    if len(skus) > 1 and any(not s["attrs"] for s in skus):
        raise PublishFailed(f"商品存在 {len(skus)} 个 SKU，但数据包未提供销售规格，请补全 SKU 规格后再发布")


def _required_attrs(detail: dict) -> list[dict]:
    """VERIFY: shape of category/detail attribute list."""
    attr = detail.get("attr") or {}
    items = attr.get("product_attr_list") or []
    return [a for a in items if a.get("is_required")]


NO_BRAND_ID = "2100000000"  # WeChat's id for 无品牌; products are published without a brand
BRAND_ATTRS = {"品牌", "品牌名称"}  # must match brand_id (6600149), so never copied from the source
NO_CATEGORY_PERMISSION = "6600145"


def _category_missing(ctx: StoreContext, cfg: TemplateConfig, norm: dict, res: Result, detail: dict):
    """The store has not opened this category: apply for it automatically (if enabled). Always raises."""
    leaf = norm["cats"][-1]
    if leaf in ctx.applied:
        raise PublishFailed(f"类目 {res.wx_category} 申请审核中（申请单：{ctx.applied[leaf]}），"
                            f"审核通过后在任务列表点「重试失败」即可")
    if not cfg.auto_apply_category:
        raise PublishFailed(f"店铺未开通类目 {res.wx_category}，请先申请类目（或在上货模板开启自动申请类目）")
    from .category_info import _find_deposit
    deposit = _find_deposit(detail)  # fen
    if deposit is not None and deposit / 100 > cfg.max_deposit:
        raise PublishFailed(f"店铺未开通类目 {res.wx_category}，保证金超过 {cfg.max_deposit} 元上限，未自动申请")
    try:
        audit = ctx.client.apply_category(norm["cats"])
    except WeChatError as e:
        raise PublishFailed(f"店铺未开通类目 {res.wx_category}，自动申请失败：{explain(e)}"
                            f"（该类目可能需要资质，请在微信小店后台手动申请）")
    ctx.applied[leaf] = audit or "已提交"  # don't re-apply for every product in this category
    raise PublishFailed(f"已自动提交微信类目申请（类目：{res.wx_category}；申请单：{audit or '-'}），"
                        f"审核通过后在任务列表点「重试失败」即可")


def build_attrs(norm: dict, cfg: TemplateConfig, detail: dict) -> list[dict]:
    values = {a["name"]: a["value"] for a in norm["attrs"] if a["name"] not in BRAND_ATTRS}
    custom = {c.name: (f"{c.value}{c.unit}" if c.unit else c.value) for c in cfg.custom_attrs
              if c.name not in BRAND_ATTRS}
    attrs = dict(values)
    missing = []
    for req in _required_attrs(detail):
        name = req.get("name")
        if not name or name in attrs:
            continue
        if name in custom:
            attrs[name] = custom[name]
        elif cfg.auto_fill_required_attrs and req.get("value"):
            attrs[name] = str(req["value"]).split(";")[0]
        else:
            missing.append(name)
    if missing:
        raise PublishFailed(f"缺少类目必填属性：{'、'.join(missing)}（可在上货模板的自定义属性中补充，或开启自动补全必填属性）")
    return [{"attr_key": k, "attr_value": v} for k, v in attrs.items()]


URL_REJECTED = {10020061}  # 图片URL非法: WeChat will not fetch this URL itself


def _upload(ctx: StoreContext, url: str, watermark=None) -> str:
    """Upload one source image to WeChat. Tries WeChat fetching the URL; once that is rejected,
    downloads the image here and uploads the file (JPEG, under the size limit)."""
    key = (url, bool(watermark))
    if key in ctx.image_cache:
        return ctx.image_cache[key]
    wx = None
    if watermark is None and ctx.url_upload_ok:
        try:
            wx = ctx.client.upload_image_url(url)
        except WeChatError as e:
            if e.errcode not in URL_REJECTED:
                raise
            ctx.url_upload_ok = False  # remember for the rest of this store's items
    if wx is None:
        data = download(url)
        if watermark is not None:
            data = add_watermark(data, watermark, ctx.store_name)
        wx = ctx.client.upload_image_bytes(normalize_for_upload(data))
    ctx.image_cache[key] = wx
    return wx


def publish(norm: dict, cfg: TemplateConfig, ctx: StoreContext, is_duplicate) -> Result:
    res = Result()
    try:
        return _publish(norm, cfg, ctx, is_duplicate, res)
    except (PublishFailed, Skipped) as e:
        e.wx_category = res.wx_category  # shown in the task details even when the item fails
        raise


def _publish(norm: dict, cfg: TemplateConfig, ctx: StoreContext, is_duplicate, res: Result) -> Result:
    prices = [s["price"] for s in norm["skus"] if s["price"]]

    # 1. Filters
    if cfg.price_filter.enabled and prices:
        lo, hi = cfg.price_filter.min * 100, cfg.price_filter.max * 100
        if min(prices) < lo or (hi and max(prices) > hi):
            raise Skipped(f"来源价格不在上货价格区间 {cfg.price_filter.min}-{cfg.price_filter.max} 元内，已过滤")
    if cfg.filter_duplicates and is_duplicate(norm):
        raise Skipped("该店铺已上传过相同商品，已过滤（过滤重复商品）")
    check_skus(norm)

    # 2. Category
    if not norm["cats"]:
        raise PublishFailed("数据包未提供类目")
    leaf = norm["cats"][-1]
    detail = ctx.detail(leaf)
    cat_name = ((detail.get("info") or {}).get("name")) or ""
    res.wx_category = f"{cat_name}（{leaf}）" if cat_name else leaf
    allowed = ctx.allowed_categories()
    if allowed is not None and leaf not in allowed:
        _category_missing(ctx, cfg, norm, res, detail)

    attrs = build_attrs(norm, cfg, detail)

    # 3. Title
    title = build_title(norm["title"], cfg, cat_name, preset=norm.get("ai_title", ""))
    res.title = title

    # 4. Images
    head = apply_delete(norm["head_imgs"], cfg.main_img_delete)[:MAX_HEAD_IMGS]
    detail_imgs = apply_delete(norm["detail_imgs"], cfg.detail_img_delete, keep_min=0)[:MAX_DETAIL_IMGS]
    if not head:
        raise PublishFailed("数据包没有主图")
    wm = cfg.watermark if cfg.watermark.enabled else None
    try:
        head_wx = [_upload(ctx, u, wm) for u in head]
        detail_wx = [_upload(ctx, u) for u in detail_imgs]
    except WeChatError as e:
        raise PublishFailed(f"图片上传失败：{explain(e)}")
    except Exception as e:
        raise PublishFailed(f"图片下载失败（来源链接可能已失效）：{e}")

    # 5. SKUs
    skus = norm["skus"]
    if cfg.filter_low_price_sku:
        skus = drop_lure_skus(skus, cfg.low_price_ratio)
    sku_body = []
    for s in skus:
        thumb = s["thumb"] or norm["head_imgs"][0]
        try:
            thumb_wx = _upload(ctx, thumb)
        except Exception:
            thumb_wx = head_wx[0]
        sku_body.append({
            "thumb_img": thumb_wx,
            "sale_price": apply_price(s["price"], cfg),
            "stock_num": s["stock"] if cfg.stock_keep_original else cfg.fixed_stock,
            "sku_code": s["sku_code"],
            "sku_attrs": [{"attr_key": a["name"], "attr_value": clean_sku_value(a["value"], cfg)} for a in s["attrs"]],
        })

    body = {
        "title": title,
        "head_imgs": head_wx,
        "desc_info": {"imgs": detail_wx, "desc": norm.get("desc", "")},
        "cats_v2": [{"cat_id": int(c)} for c in norm["cats"]],
        "attrs": attrs,
        "brand_id": NO_BRAND_ID,
        "express_info": {"template_id": ctx.freight_template_id},
        "after_sale_info": {"after_sale_address_id": int(ctx.address_id)} if ctx.address_id else {},
        "deliver_method": 0,
        "extra_service": {  # VERIFY field names against product/add docs
            "seven_day_return": int(cfg.seven_day_return),
            "freight_insurance": int(cfg.freight_insurance),
            "damage_guarantee": int(cfg.damage_return),
            "fake_one_pay_three": int(cfg.fake_one_pay_three),
            "exchange_support": int(cfg.exchange),
        },
        "skus": sku_body,
    }
    if cfg.auto_short_title:
        body["short_title"] = short_title(title)
    if cfg.deliver_type == "presale":
        body["spu_deliver_info"] = {"sku_deliver_info": {"stock_type": 1}}  # VERIFY presale fields

    notes = []
    if cfg.upload_video and norm.get("video"):
        # VERIFY: the product API field for a main-image video is not confirmed yet.
        notes.append("主图视频待接口确认后上传")

    # 6. Create + list
    try:
        res.wx_product_id = ctx.client.add_product(body)
    except WeChatError as e:
        if NO_CATEGORY_PERMISSION in (e.errmsg or ""):  # 6600145 无该类目权限
            _category_missing(ctx, cfg, norm, res, detail)
        raise PublishFailed(f"创建商品失败：{explain(e)}")
    if cfg.listing == "on_sale":
        try:
            ctx.client.listing(res.wx_product_id)
            notes.insert(0, "已上架")
        except WeChatError as e:
            notes.insert(0, f"已存草稿箱（上架失败：{explain(e)}）")
    else:
        notes.insert(0, "已存草稿箱")
    res.message = "；".join(notes)
    return res
