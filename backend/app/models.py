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
    # 积分: spent when publishing a 共享云库 product another account uploaded (admin tops up)
    points: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    daily_upload_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = 系统设置 default

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
    # last progress of a running sync: a "running" sync without progress for a while was interrupted
    # (the API process restarted or the job died) and may be started again
    sync_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_sync_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 订单管理: order sync state; orders_synced_to = update time the last successful sync reached
    order_sync_status: Mapped[str] = mapped_column(String(16), default="", server_default="")
    order_sync_message: Mapped[str] = mapped_column(String(255), default="", server_default="")
    orders_synced_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    # 共享云库: the account that uploaded the product first. Equal to tenant_id for an account's own
    # uploads (the shared original, free to publish); another account for a product added from the
    # shared library (每次上货扣积分). None only before the migration backfills it.
    origin_tenant_id: Mapped[int | None] = mapped_column(BigId, nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("product_group.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), default="wechat")  # wechat|taobao|1688|pdd|kuaishou
    source_product_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    ai_title: Mapped[str] = mapped_column(String(255), default="", server_default="")  # generated / edited title
    # 无货源: kept in the library but never published again (cleared by 设为货源 or 取消标记)
    no_source: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    no_source_note: Mapped[str] = mapped_column(String(255), default="", server_default="")
    cover: Mapped[str] = mapped_column(String(1024), default="")
    has_video: Mapped[bool] = mapped_column(Boolean, default=False)
    price_min: Mapped[int] = mapped_column(Integer, default=0)  # fen
    price_max: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(BigInteger, default=0)
    sales: Mapped[int] = mapped_column(BigInteger, default=0)
    sku_count: Mapped[int] = mapped_column(Integer, default=0)
    normalized: Mapped[dict] = mapped_column(JSON)  # parser output used for publishing
    # 店铺改价 written back: {source sku id: sale price in fen}; used instead of the template formula
    sale_prices: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sale_prices_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    # pending|running|success|partial|failed|cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending")
    total: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    cancelled: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # items 撤销 before running
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
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|success|failed|skipped|cancelled
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
    crop: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 框选主体 [x, y, w, h] as fractions; None = whole image
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
    original_price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1688 price before 修改货源价
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
    title: Mapped[str] = mapped_column(String(255), default="", server_default="")  # title used in this store
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SystemSetting(Base):
    """Platform-wide settings edited in 系统设置 (e.g. the DeepSeek API key). Secrets are encrypted."""
    __tablename__ = "system_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CategoryInfo(Base):
    """WeChat's category library (获取所有类目) with each leaf's 保证金, the same for every store."""
    __tablename__ = "category_info"
    cat_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    deposit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # fen; None = not reported
    deposit_source: Mapped[str] = mapped_column(String(16), default="", server_default="")  # rule|detail
    needs_qualification: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    qua_tips: Mapped[str] = mapped_column(String(255), default="", server_default="")
    path: Mapped[str] = mapped_column(String(255), default="", server_default="")  # ids root->leaf, "/"-joined
    path_names: Mapped[str] = mapped_column(String(512), default="", server_default="")
    leaf: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CatalogSync(Base):
    """Progress of the category library sync (one row, id=1)."""
    __tablename__ = "catalog_sync"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="")  # running|done|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CategoryApplication(Base):
    """一键申请类目: one application submitted for a store."""
    __tablename__ = "category_application"
    __table_args__ = (Index("ix_catapp_store_cat", "store_id", "cat_id"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    store_id: Mapped[int] = mapped_column(BigId)
    cat_id: Mapped[str] = mapped_column(String(32))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    audit_id: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PointLog(Base):
    """积分 changes of an account: admin top-ups (+) and publish charges (-)."""
    __tablename__ = "point_log"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    change: Mapped[int] = mapped_column(BigInteger)
    balance: Mapped[int] = mapped_column(BigInteger)  # after this change
    kind: Mapped[str] = mapped_column(String(16))  # topup | publish
    note: Mapped[str] = mapped_column(String(255), default="")
    task_item_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    operator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StoreOrder(Base):
    """订单管理: an order of a WeChat store, synced from the store's API (获取订单列表 / 获取订单详情)."""
    __tablename__ = "store_order"
    __table_args__ = (
        UniqueConstraint("store_id", "order_id"),
        Index("ix_order_tenant_time", "tenant_id", "create_time"),
    )
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    store_id: Mapped[int] = mapped_column(BigId)
    order_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[int] = mapped_column(Integer, default=0)  # 10 待付款 20 待发货 21 部分发货 30 待收货 100 完成 ...
    create_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    product_price: Mapped[int] = mapped_column(Integer, default=0)  # fen, goods before discounts
    freight: Mapped[int] = mapped_column(Integer, default=0)
    order_price: Mapped[int] = mapped_column(Integer, default=0)  # fen, what the buyer paid
    receiver_name: Mapped[str] = mapped_column(String(64), default="")
    receiver_phone: Mapped[str] = mapped_column(String(64), default="")
    receiver_address: Mapped[str] = mapped_column(String(512), default="")
    buyer_note: Mapped[str] = mapped_column(String(512), default="")
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # full buyer info decoded
    # 虚拟号: WeChat replaces the buyer's phone by a virtual number; the real one only after 申请真实号码
    virtual_phone: Mapped[str] = mapped_column(String(64), default="", server_default="")
    virtual_phone_expire: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decoded: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 解码敏感数据 response, to check fields
    merchant_note: Mapped[str] = mapped_column(String(512), default="")
    # [{product_id, sku_id, title, thumb, attrs, qty, sale_price, real_price}] (prices in fen)
    items: Mapped[list] = mapped_column(JSON, default=list)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)  # 获取订单详情 response, to check field names
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class StoreAccess(Base):
    """用户管理 → 分配店铺: another account may use this store (products, orders, 发货, 上货) as its own.
    The owner (store.tenant_id) keeps it; only the owner changes its settings or deletes it."""
    __tablename__ = "store_access"
    __table_args__ = (UniqueConstraint("store_id", "tenant_id"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(BigId, index=True)
    tenant_id: Mapped[int] = mapped_column(BigId, index=True)
    granted_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
