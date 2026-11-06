"""命令行入口：拉取联盟商品、导出 Excel。

示例：
    python -m lanjing.cli --demo --export out.xlsx
    python -m lanjing.cli --token <access_token> --max 200 --export out.xlsx
    python -m lanjing.cli --appid <id> --secret <secret> --export out.xlsx
"""
from __future__ import annotations
import argparse
import sys

from .models import product_from_api
from .exporter import export_excel


def build_client(args):
    if args.demo:
        from .sample_data import SampleClient
        return SampleClient()
    from .api_client import LeagueClient
    return LeagueClient(access_token=args.token or "",
                        appid=args.appid or "", secret=args.secret or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lanjing", description="蓝鲸科技 · 微信小店联盟选品工具")
    ap.add_argument("--demo", action="store_true", help="样例数据模式，无需凭证")
    ap.add_argument("--token", help="联盟 access_token")
    ap.add_argument("--appid")
    ap.add_argument("--secret")
    ap.add_argument("--max", type=int, default=0, help="最多拉取数量，0=全部")
    ap.add_argument("--min-commission", type=float, default=0.0, help="最低佣金(%)筛选")
    ap.add_argument("--export", help="导出 Excel 路径")
    args = ap.parse_args(argv)

    if not (args.demo or args.token or (args.appid and args.secret)):
        ap.error("需要 --demo，或 --token，或 --appid 与 --secret")

    client = build_client(args)
    products = []
    for raw in client.iter_products(max_items=args.max):
        p = product_from_api(raw)
        if p.commission_rate < args.min_commission:
            continue
        products.append(p)
        print(f"[{len(products):>4}] {p.product_id}  佣金{p.commission_rate:>5.1f}%  "
              f"￥{p.price_text:<14} {p.title}")

    print(f"\n共 {len(products)} 件商品")
    if args.export:
        n = export_excel(products, args.export)
        print(f"已导出 {n} 件到 {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
