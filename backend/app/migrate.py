"""Minimal automatic schema upgrade: creates missing tables and adds missing columns.

Runs at API start-up, so a deploy that adds a column needs no manual SQL. It never
drops or changes existing columns. New NOT NULL columns must have a server_default.
"""
import logging

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
    return added
