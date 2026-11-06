from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Sku:
    sku_id: str = ""
    spec: str = ""          # 规格，如 "红色 / XL"
    price: float = 0.0      # 售价（元）
    stock: int = 0


@dataclass
class Product:
    product_id: str = ""
    title: str = ""
    head_img: str = ""              # 主图 URL
    images: list[str] = field(default_factory=list)   # 轮播/详情图
    min_price: float = 0.0
    max_price: float = 0.0
    commission_rate: float = 0.0    # 佣金比例（%）
    sold_count: int = 0             # 销量
    shop_name: str = ""
    video_url: str = ""
    skus: list[Sku] = field(default_factory=list)

    @property
    def price_text(self) -> str:
        if self.max_price and self.max_price != self.min_price:
            return f"{self.min_price:.2f} ~ {self.max_price:.2f}"
        return f"{self.min_price:.2f}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def product_from_api(raw: dict[str, Any]) -> Product:
    """把官方接口返回的单品结构映射成 Product（字段名按官方文档，缺失则留空）。"""
    skus = []
    for s in raw.get("sku_list", raw.get("skus", []) or []):
        skus.append(Sku(
            sku_id=str(s.get("sku_id", s.get("skuId", ""))),
            spec=s.get("sku_attr", s.get("spec", "")),
            price=_yuan(s.get("sale_price", s.get("price", 0))),
            stock=int(s.get("stock_num", s.get("stock", 0)) or 0),
        ))
    prices = [s.price for s in skus if s.price] or [_yuan(raw.get("min_price", 0))]
    return Product(
        product_id=str(raw.get("product_id", raw.get("productId", ""))),
        title=raw.get("title", raw.get("product_name", "")),
        head_img=raw.get("head_img", (raw.get("head_imgs") or [""])[0] if raw.get("head_imgs") else raw.get("headImg", "")),
        images=list(raw.get("desc_info", {}).get("imgs", []) or raw.get("images", []) or []),
        min_price=min(prices),
        max_price=max(prices),
        commission_rate=float(raw.get("commission_rate", raw.get("commissionRate", 0)) or 0),
        sold_count=int(raw.get("sold_num", raw.get("sales", 0)) or 0),
        shop_name=raw.get("shop_name", raw.get("shopName", "")),
        video_url=raw.get("video_url", raw.get("videoUrl", "")),
        skus=skus,
    )


def _yuan(v: Any) -> float:
    """官方价格多为分，转成元。已是元的小数则原样返回。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(n / 100, 2) if n >= 1000 and n == int(n) else round(n, 2)
