"""Minimal automatic schema upgrade: creates missing tables and adds missing columns.

Runs at API start-up, so a deploy that adds a column needs no manual SQL. It never
drops or changes existing columns. New NOT NULL columns must have a server_default.
"""
import logging
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn

from . import models  # noqa: F401  (registers every table on Base.metadata)
from .db import Base

log = logging.getLogger(__name__)


def upgrade(engine) -> list[str]:
    Base.metadata.create_all(engine)
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = CreateColumn(col).compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                added.append(f"{table.name}.{col.name}")
    for a in added:
        log.info("schema upgrade: added column %s", a)
    backfill_origins(engine)
    fix_order_times(engine)
    freight_into_source_prices(engine)
    return added


def backfill_origins(engine) -> int:
    """共享云库: give every library product its uploader (origin_tenant_id). The first account that
    uploaded a source product (lowest id) keeps the original; the same product in other accounts
    becomes 'added from the shared library' and costs 积分 per publish. Only rows without an
    origin are touched, so it runs once per row."""
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, tenant_id, platform, source_product_id, origin_tenant_id FROM library_product "
            "ORDER BY id")).all()
        if all(r[4] is not None for r in rows):
            return 0
        uploader = {}  # (platform, source id) -> tenant: an origin already recorded, else the lowest id
        for rid, tid, plat, spid, origin in rows:
            if origin is not None:
                uploader[(plat, spid)] = origin
        for rid, tid, plat, spid, origin in rows:
            uploader.setdefault((plat, spid), tid)
        n = 0
        for rid, tid, plat, spid, origin in rows:
            if origin is None:
                conn.execute(text("UPDATE library_product SET origin_tenant_id = :o WHERE id = :i"),
                             {"o": uploader[(plat, spid)], "i": rid})
                n += 1
    if n:
        log.info("共享云库: set the uploader of %s library products", n)
    return n


def fix_order_times(engine) -> int:
    """Orders synced before the China-time fix kept the server's (UTC) clock: 8 hours early. Their
    times are read again from the saved WeChat response. Runs once (app_flag)."""
    import json

    from .services.order_sync import parse_order
    with engine.begin() as conn:
        if conn.execute(text("SELECT name FROM app_flag WHERE name = 'order_times_cn'")).first():
            return 0
        n, last = 0, 0
        while True:
            rows = conn.execute(text("SELECT id, raw FROM store_order WHERE id > :l ORDER BY id LIMIT 500"), {"l": last}).all()
            if not rows:
                break
            for rid, raw in rows:
                last = rid
                try:
                    p = parse_order(json.loads(raw) if isinstance(raw, str) else (raw or {}))
                except Exception:
                    continue
                conn.execute(text("UPDATE store_order SET create_time = :c, update_time = :u, pay_time = :p WHERE id = :i"),
                             {"c": p["create_time"], "u": p["update_time"], "p": p["pay_time"], "i": rid})
                n += 1
        conn.execute(text("INSERT INTO app_flag (name, done_at) VALUES ('order_times_cn', :d)"), {"d": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    if n:
        log.info("订单时间: %s orders now in China time", n)
    return n


def freight_into_source_prices(engine) -> int:
    """货源价 now include the 1688 运费 (it was added on top before). Existing 货源 get their 运费 added
    to their prices once (app_flag). A price changed from a real 采购价 already includes it: only its
    1688 原价 gets the 运费."""
    import json
    with engine.begin() as conn:
        if conn.execute(text("SELECT name FROM app_flag WHERE name = 'freight_in_price'")).first():
            return 0
        rows = conn.execute(text("SELECT id, price, original_price, sku_prices, freight FROM product_source_link "
                                 "WHERE freight IS NOT NULL AND freight > 0")).all()
        for rid, price, orig, skus, fr in rows:
            if orig is not None:
                conn.execute(text("UPDATE product_source_link SET original_price = :o WHERE id = :i"), {"o": orig + fr, "i": rid})
                continue
            sp = json.loads(skus) if isinstance(skus, str) else skus
            sp = {k: v + fr for k, v in sp.items()} if sp else sp
            conn.execute(text("UPDATE product_source_link SET price = :p, sku_prices = :s WHERE id = :i"),
                         {"p": (price or 0) + fr, "s": json.dumps(sp) if sp is not None else None, "i": rid})
        conn.execute(text("INSERT INTO app_flag (name, done_at) VALUES ('freight_in_price', :d)"),
                     {"d": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    if rows:
        log.info("货源价: 运费 included in %s 货源", len(rows))
    return len(rows)
