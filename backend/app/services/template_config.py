"""上货模板 settings. Defaults follow the reference screenshots plus the owner's decisions
(videos uploaded by default, titles truncated at 30 characters, deposit cap 5,000 元)."""
from pydantic import BaseModel, Field


class PriceFormula(BaseModel):
    threshold: float = 0  # applies when source price (yuan) >= threshold
    multiplier: float = 1.0
    add: float = 13.5
    subtract: float = 0.0
    rounding: str = "fen"  # fen (保留分) | jiao (保留角) | yuan (保留元)


class Range(BaseModel):
    enabled: bool = False
    min: float = 0
    max: float = 0


class DeleteRange(BaseModel):
    enabled: bool = False
    front: int = 0  # delete first N
    back: int = 0  # delete last N


class Watermark(BaseModel):
    enabled: bool = False
    style: str = "center_light"  # simple_text|half_label|outline_badge|tiled|center_light|vertical_corner
    opacity: int = 20  # percent
    text: str = ""  # empty = store name


class KeywordReplace(BaseModel):
    source: str
    target: str = ""  # empty = delete


class CustomAttr(BaseModel):
    name: str
    value: str
    unit: str = ""


DEFAULT_ATTRS = [
    ("数量", "1"), ("商品产地", "中国大陆"), ("毛重", "见详情"), ("质量", "见详情"), ("材质", "见详情"),
    ("主体材质", "见详情"), ("适用性别", "通用"), ("规格", "见详情"), ("容量", "见详情"), ("非金属", "见详情"),
    ("净含量", "见详情"), ("产地", "中国大陆"), ("产品净重", "见详情"), ("面料材质成分含量", "见详情"),
    ("尺寸", "见详情"), ("生产方式", "见详情"), ("适用年龄", "见详情"), ("面料材质", "见详情"), ("功能", "见详情"),
    ("是否可调色", "见详情"), ("重量", "见详情"), ("最大功率", "见详情"), ("特色功能", "见详情"),
    ("操控方式", "见详情"), ("金属材质", "见详情"), ("填充物", "见详情"), ("适用场景", "见详情"),
    ("主材质", "见详情"), ("含绒量/绒子含量（%）", "见详情"), ("是否提供上门安装", "否，不支持"),
    ("是否支持上门测量", "否，不支持"), ("CCC证书编号", "见详情"), ("型号", "见详情"), ("版型详情", "见详情"),
    ("防水强度", "见详情"), ("单件净含量", "见详情"), ("保质期", "见详情"), ("填充材料", "见详情"),
    ("衣长", "见详情"), ("蚕丝长度", "见详情"), ("尺寸大小", "见详情"), ("版型", "见详情"),
]


class TemplateConfig(BaseModel):
    deliver_type: str = "spot"  # spot (现货, 48h) | presale (全款预售)
    deliver_hours: int = 48
    price: PriceFormula = Field(default_factory=PriceFormula)
    listing: str = "on_sale"  # on_sale (出售中; quota full -> draft) | draft
    title_prefix: str = ""
    title_suffix: str = ""
    keyword_replacements: list[KeywordReplace] = Field(default_factory=list)
    title_banned_words: list[str] = Field(default_factory=list)
    sku_banned_words: list[str] = Field(default_factory=list)
    filter_low_price_sku: bool = False
    low_price_ratio: float = 0.4  # SKU below 40% of the median price counts as a lure SKU
    freight_insurance: bool = False
    seven_day_return: bool = True
    filter_duplicates: bool = True
    price_filter: Range = Field(default_factory=Range)  # skip products whose source price is outside
    fixed_tail: bool = False
    fixed_tail_value: float = 0.9  # e.g. ¥23.47 -> ¥23.90
    truncate_title: bool = True
    title_max_len: int = 30
    ai_rewrite_title: bool = True  # DeepSeek (needs DEEPSEEK_API_KEY)
    dedupe_title_keywords: bool = True
    auto_short_title: bool = True
    upload_video: bool = True
    watermark: Watermark = Field(default_factory=Watermark)
    damage_return: bool = False  # 坏损包退
    fake_one_pay_three: bool = False  # 假一赔三
    exchange: bool = False  # 支持换货
    detail_img_delete: DeleteRange = Field(default_factory=DeleteRange)
    main_img_delete: DeleteRange = Field(default_factory=DeleteRange)
    stock_keep_original: bool = True
    fixed_stock: int = 999
    auto_fill_required_attrs: bool = False
    custom_attrs: list[CustomAttr] = Field(
        default_factory=lambda: [CustomAttr(name=n, value=v) for n, v in DEFAULT_ATTRS]
    )
    auto_apply_category: bool = True
    max_deposit: int = 5000  # 元, cap for automatic category applications
