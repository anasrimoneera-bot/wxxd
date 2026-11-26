"""Minimal automatic schema upgrade: creates missing tables and adds missing columns.

Runs at API start-up, so a deploy that adds a column needs no manual SQL. It never
drops or changes existing columns. New NOT NULL columns must have a server_default — except TEXT / JSON ones (MySQL allows no DEFAULT on
them): make those nullable.
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
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = str(CreateColumn(col).compile(dialect=engine.dialect))
            if engine.dialect.name in ("mysql", "mariadb") and col.type.__class__.__name__ in ("Text", "JSON", "LargeBinary"):
                # MySQL 8 refuses a plain DEFAULT on TEXT / JSON / BLOB columns (error 1101): add it nullable
                ddl = f"{col.name} {col.type.compile(dialect=engine.dialect)} NULL"
            try:  # each on its own (MySQL commits every ALTER at once)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                added.append(f"{table.name}.{col.name}")
            except Exception as e:  # one column must never keep the whole site from starting
                log.error("schema upgrade: could not add %s.%s: %s", table.name, col.name, e)
    for a in added:
        log.info("schema upgrade: added column %s", a)
    add_missing_indexes(engine)
    backfill_origins(engine)
    fix_order_times(engine)
    freight_into_source_prices(engine)
    fill_order_net_income(engine)
    fill_order_buyer_openid(engine)
    fill_library_source_key(engine)
    return added


def add_missing_indexes(engine) -> list[str]:
    """Indexes declared on tables that already existed (create_all only makes them for new tables)."""
    made = []
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        have = {i["name"] for i in insp.get_indexes(table.name)}
        for idx in table.indexes:
            if idx.name in have:
                continue
            try:
                idx.create(engine)
                made.append(idx.name)
            except Exception as e:  # e.g. created meanwhile by another worker
                log.warning("index %s not created: %s", idx.name, e)
    for m in made:
        log.info("schema upgrade: added index %s", m)
    return made


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


def fill_order_net_income(engine) -> int:
    """到手 (net_income) of the orders synced before it was kept, from their saved 订单详情. Once (app_flag)."""
    import json

    from .services.order_sync import money
    with engine.begin() as conn:
        if conn.execute(text("SELECT name FROM app_flag WHERE name = 'order_net_income'")).first():
            return 0
        n, last = 0, 0
        while True:
            rows = conn.execute(text("SELECT id, raw, order_price FROM store_order WHERE id > :l ORDER BY id LIMIT 500"),
                                {"l": last}).all()
            if not rows:
                break
            for rid, raw, price in rows:
                last = rid
                try:
                    m = money(json.loads(raw) if isinstance(raw, str) else (raw or {}), price or 0)
                except Exception:
                    continue
                conn.execute(text("UPDATE store_order SET net_income = :v WHERE id = :i"), {"v": m["net_income"], "i": rid})
                n += 1
        conn.execute(text("INSERT INTO app_flag (name, done_at) VALUES ('order_net_income', :d)"),
                     {"d": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return n


def fill_order_buyer_openid(engine) -> int:
    """买家消息 → orders: the buyer's openid of the orders synced before it was kept (from the saved 订单详情). Once."""
    import json
    with engine.begin() as conn:
        if conn.execute(text("SELECT name FROM app_flag WHERE name = 'order_buyer_openid'")).first():
            return 0
        n, last = 0, 0
        while True:
            rows = conn.execute(text("SELECT id, raw FROM store_order WHERE id > :l ORDER BY id LIMIT 500"), {"l": last}).all()
            if not rows:
                break
            for rid, raw in rows:
                last = rid
                try:
                    d = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    oid = str(((d.get("order") or d).get("openid")) or "")[:128]
                except Exception:
                    continue
                if oid:
                    conn.execute(text("UPDATE store_order SET buyer_openid = :v WHERE id = :i"), {"v": oid, "i": rid})
                    n += 1
        conn.execute(text("INSERT INTO app_flag (name, done_at) VALUES ('order_buyer_openid', :d)"),
                     {"d": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    return n


def fill_library_source_key(engine) -> None:
    """商品云库 source_key (platform:id) for rows saved before it was kept: joins with published_record use it."""
    concat = "platform || ':' || source_product_id" if engine.dialect.name == "sqlite" else "CONCAT(platform, ':', source_product_id)"
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE library_product SET source_key = {concat} WHERE source_key = '' OR source_key IS NULL"))
