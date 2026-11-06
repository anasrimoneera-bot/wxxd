"""样例数据：无接口凭证时用于预览界面与导出，字段结构与官方接口一致。"""
from __future__ import annotations

SAMPLE_RAW = [
    {
        "product_id": "10000000001", "title": "便携无线蓝牙耳机 降噪长续航",
        "head_img": "https://via.placeholder.com/300?text=earphone",
        "commission_rate": 20.0, "sold_num": 3521, "shop_name": "蓝鲸优选旗舰店",
        "sku_list": [
            {"sku_id": "s1", "sku_attr": "白色", "sale_price": 9900, "stock_num": 230},
            {"sku_id": "s2", "sku_attr": "黑色", "sale_price": 10900, "stock_num": 118},
        ],
        "desc_info": {"imgs": ["https://via.placeholder.com/600?text=detail1"]},
    },
    {
        "product_id": "10000000002", "title": "家用破壁机 多功能料理",
        "head_img": "https://via.placeholder.com/300?text=blender",
        "commission_rate": 15.0, "sold_num": 892, "shop_name": "厨房好物馆",
        "sku_list": [
            {"sku_id": "s1", "sku_attr": "标准版", "sale_price": 29900, "stock_num": 60},
        ],
        "video_url": "https://example.com/video/2.mp4",
    },
    {
        "product_id": "10000000003", "title": "纯棉四件套 亲肤床品",
        "head_img": "https://via.placeholder.com/300?text=bedding",
        "commission_rate": 25.0, "sold_num": 1544, "shop_name": "暖居家纺",
        "sku_list": [
            {"sku_id": "s1", "sku_attr": "1.5米 灰", "sale_price": 15900, "stock_num": 40},
            {"sku_id": "s2", "sku_attr": "1.8米 蓝", "sale_price": 18900, "stock_num": 22},
        ],
    },
]


class SampleClient:
    """与 LeagueClient 接口兼容的离线样例客户端。"""
    def iter_products(self, page_size=100, max_items=0, extra=None):
        for i, p in enumerate(SAMPLE_RAW):
            if max_items and i >= max_items:
                return
            yield p

    def get_product_detail(self, product_id, promote_type=1, info_id=""):
        for p in SAMPLE_RAW:
            if p["product_id"] == product_id:
                return p
        return {}
