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
