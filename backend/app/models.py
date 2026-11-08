from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index, JSON,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

BigId = BigInteger().with_variant(Integer, "sqlite")
LongText = Text().with_variant(LONGTEXT, "mysql")


def now() -> datetime:
    return datetime.now()


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class User(Base):
    """Owner account or sub-account (子账号) of a tenant."""
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | operator
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Store(Base):
    """A WeChat Store connected with the user's own AppID/AppSecret."""
    __tablename__ = "store"
    __table_args__ = (UniqueConstraint("tenant_id", "appid"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    logo: Mapped[str] = mapped_column(String(512), default="")
    appid: Mapped[str] = mapped_column(String(64))
    secret_enc: Mapped[str] = mapped_column(String(512))
    wx_shop_id: Mapped[str] = mapped_column(String(64), default="")  # gh_xxx
    open_status: Mapped[str] = mapped_column(String(32), default="")  # e.g. open_finished
    freight_template_id: Mapped[str] = mapped_column(String(64), default="")
    freight_template_name: Mapped[str] = mapped_column(String(128), default="")
    address_id: Mapped[str] = mapped_column(String(64), default="")
    address_name: Mapped[str] = mapped_column(String(255), default="")
    publish_template_id: Mapped[int | None] = mapped_column(ForeignKey("publish_template.id"), nullable=True)
    last_error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProductGroup(Base):
    __tablename__ = "product_group"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LibraryProduct(Base):
    """Product cloud library (商品云库): one imported product data package."""
    __tablename__ = "library_product"
    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", "source_product_id"),
        Index("ix_lib_tenant_group", "tenant_id", "group_id"),
    )
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("product_group.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), default="wechat")  # wechat|taobao|1688|pdd|kuaishou
    source_product_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    cover: Mapped[str] = mapped_column(String(1024), default="")
    has_video: Mapped[bool] = mapped_column(Boolean, default=False)
    price_min: Mapped[int] = mapped_column(Integer, default=0)  # fen
    price_max: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(BigInteger, default=0)
    sales: Mapped[int] = mapped_column(BigInteger, default=0)
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    normalized: Mapped[dict] = mapped_column(JSON)  # parser output used for publishing
    raw: Mapped[str] = mapped_column(LongText)  # original file content, for traceability
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class PublishTemplate(Base):
    """上货模板: rules applied when publishing library products to a store."""
    __tablename__ = "publish_template"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PublishTask(Base):
    __tablename__ = "publish_task"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    store_id: Mapped[int] = mapped_column(BigId, index=True)  # no FK: tasks outlive deleted stores
    store_name: Mapped[str] = mapped_column(String(64), default="")
    template_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    template_name: Mapped[str] = mapped_column(String(64), default="")
    template_snapshot: Mapped[dict] = mapped_column(JSON)  # rules frozen at task creation
    scope: Mapped[str] = mapped_column(String(128), default="")  # e.g. 微信小店 / 杨继芸
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|success|partial|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublishTaskItem(Base):
    __tablename__ = "publish_task_item"
    __table_args__ = (Index("ix_item_status", "status", "id"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("publish_task.id"), index=True)
    library_product_id: Mapped[int] = mapped_column(BigId)  # no FK: keep history if product is deleted
    platform: Mapped[str] = mapped_column(String(16), default="wechat")
    source_product_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    cover: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|success|failed|skipped
    wx_category: Mapped[str] = mapped_column(String(255), default="")
    wx_product_id: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(String(1024), default="")
    worker: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublishedRecord(Base):
    """Per-store record of what was uploaded, used by the duplicate filter (过滤重复商品)."""
    __tablename__ = "published_record"
    __table_args__ = (
        UniqueConstraint("store_id", "source_key"),
        Index("ix_pub_store_title", "store_id", "title_hash"),
    )
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigId)
    source_key: Mapped[str] = mapped_column(String(96))  # platform:source_product_id
    title_hash: Mapped[str] = mapped_column(String(64))  # hash of normalised source title
    wx_product_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
