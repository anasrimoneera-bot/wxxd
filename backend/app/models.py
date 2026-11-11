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
    """A customer (company / team). Membership is controlled by active + expires_at."""
    __tablename__ = "tenant"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = no expiry
    note: Mapped[str] = mapped_column(String(255), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def block_reason(self) -> str | None:
        if not self.active:
            return "账号已停用，请联系管理员"
        if self.expires_at and self.expires_at < datetime.now():
            return f"会员已于 {self.expires_at:%Y-%m-%d} 到期，请联系管理员续费"
        return None


class User(Base):
    """Owner account or sub-account (子账号) of a tenant."""
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | operator
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # platform administrator (you)
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
    sync_status: Mapped[str] = mapped_column(String(16), default="", server_default="")  # running|done|failed
    sync_message: Mapped[str] = mapped_column(String(255), default="", server_default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    ai_title: Mapped[str] = mapped_column(String(255), default="", server_default="")  # generated / edited title
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


class StoreProduct(Base):
    """商品管理: a product as it exists in a WeChat store, synced from the store's API."""
    __tablename__ = "store_product"
    __table_args__ = (
        UniqueConstraint("store_id", "wx_product_id"),
        Index("ix_sp_tenant_store", "tenant_id", "store_id"),
    )
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    store_id: Mapped[int] = mapped_column(BigId)
    wx_product_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), default="")
    cover: Mapped[str] = mapped_column(String(1024), default="")
    spu_code: Mapped[str] = mapped_column(String(128), default="", server_default="")  # merchant product code
    status: Mapped[int] = mapped_column(Integer, default=0)  # WeChat status (5 = on sale, 11 = delisted, ...)
    edit_status: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # draft/review state
    price_min: Mapped[int] = mapped_column(Integer, default=0)  # fen
    price_max: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(BigInteger, default=0)
    skus: Mapped[list] = mapped_column(JSON, default=list)  # [{sku_id, sku_code, price, stock, attrs}]
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SourcingJob(Base):
    """找货源: one batch of up to 10 images searched on 1688."""
    __tablename__ = "sourcing_job"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    user_id: Mapped[int] = mapped_column(BigId)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    sort_type: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SourcingQuery(Base):
    """One source image in a job, with the 1688 offers found for it (JSON)."""
    __tablename__ = "sourcing_query"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigId, index=True)
    tenant_id: Mapped[int] = mapped_column(BigId)
    image_url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(255), default="")
    library_product_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    store_product_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed
    error: Mapped[str] = mapped_column(String(512), default="")
    offers: Mapped[list] = mapped_column(JSON, default=list)
    hidden_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # offers hidden: 0 sales
    best_offer_id: Mapped[str] = mapped_column(String(64), default="")


class SourcingCache(Base):
    __tablename__ = "sourcing_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[list] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ProductSourceLink(Base):
    """The 1688 offer chosen as a product's supply source (设为货源). Price excludes shipping."""
    __tablename__ = "product_source_link"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    library_product_id: Mapped[int | None] = mapped_column(BigId, nullable=True, index=True)
    store_product_id: Mapped[int | None] = mapped_column(BigId, nullable=True, index=True)
    offer_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255), default="")
    image_url: Mapped[str] = mapped_column(String(1024), default="")
    detail_url: Mapped[str] = mapped_column(String(1024), default="")
    supplier: Mapped[str] = mapped_column(String(255), default="")
    price: Mapped[int] = mapped_column(Integer, default=0)  # fen, goods only
    moq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="")
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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


class SystemSetting(Base):
    """Platform-wide settings edited in 系统设置 (e.g. the DeepSeek API key). Secrets are encrypted."""
    __tablename__ = "system_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
