"""导出 Excel 与下载媒体文件。"""
from __future__ import annotations
import os
from typing import Iterable

from .models import Product


def export_excel(products: Iterable[Product], path: str) -> int:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "选品"
    headers = ["商品ID", "标题", "店铺", "价格(元)", "佣金(%)", "销量",
               "SKU数", "主图", "视频"]
    ws.append(headers)
    n = 0
    for p in products:
        ws.append([p.product_id, p.title, p.shop_name, p.price_text,
                   p.commission_rate, p.sold_count, len(p.skus),
                   p.head_img, p.video_url])
        n += 1
    for col, w in zip("ABCDEFGHI", (14, 40, 18, 14, 10, 8, 8, 40, 40)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return n


def download_file(url: str, path: str, timeout: int = 30) -> str:
    import requests
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
    return path
