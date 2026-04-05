import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    avito_phone: Mapped[str | None] = mapped_column(String(30))
    avito_address: Mapped[str | None] = mapped_column(String(256))
    avito_manager_name: Mapped[str | None] = mapped_column(String(40))
    website_url: Mapped[str | None] = mapped_column(String(256))
    website_feed_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Avito REST API credentials (client_secret шифруется Fernet)
    avito_client_id: Mapped[str | None] = mapped_column(String(100))
    avito_client_secret: Mapped[str | None] = mapped_column(Text)  # encrypted

    users: Mapped[list["User"]] = relationship(back_populates="store")
    products: Mapped[list["Product"]] = relationship(back_populates="store")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("stores.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    store: Mapped["Store | None"] = relationship(back_populates="users")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False)
    sku_1c: Mapped[str] = mapped_column(String(100), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    storage: Mapped[str | None] = mapped_column(String(30))
    color: Mapped[str | None] = mapped_column(String(100))
    condition: Mapped[str | None] = mapped_column(String(50))
    battery_pct: Mapped[str | None] = mapped_column(String(10))
    in_repair: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    sim_count: Mapped[int | None] = mapped_column(Integer)  # 1, 2, 3 — кол-во SIM-карт
    sim_type: Mapped[str | None] = mapped_column(String(50))  # "SIM+eSIM", "eSIM+eSIM" и т.д.
    completeness: Mapped[str | None] = mapped_column(String(100))  # комплектация: "Телефон", "Полная" и т.д.
    price_retail: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_sold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_new: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    site_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avito_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avito_title: Mapped[str | None] = mapped_column(String(50))
    avito_description: Mapped[str | None] = mapped_column(Text)
    avito_item_id: Mapped[str | None] = mapped_column(String(50), index=True)
    avito_url: Mapped[str | None] = mapped_column(String(512))

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    store: Mapped["Store"] = relationship(back_populates="products")
    photos: Mapped[list["ProductPhoto"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    docs: Mapped[list["PurchaseDoc"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductPhoto(Base):
    __tablename__ = "product_photos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="photos")


class PurchaseDoc(Base):
    __tablename__ = "purchase_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    has_personal_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pd_record_id: Mapped[str | None] = mapped_column(String(36))
    file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    product: Mapped["Product"] = relationship(back_populates="docs")


class DocAccessLog(Base):
    __tablename__ = "doc_access_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase_docs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # view | print | download
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False)
    imported_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    items_created: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    items_sold: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AvitoStats(Base):
    __tablename__ = "avito_stats"
    __table_args__ = (
        UniqueConstraint("product_id", "date", name="uq_avito_stats_product_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False)
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0)
    contacts: Mapped[int] = mapped_column(Integer, default=0)
    favorites: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CompetitorPrice(Base):
    __tablename__ = "competitor_prices"
    __table_args__ = (
        UniqueConstraint("source", "brand", "model", "memory", name="uq_competitor_price"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # "goodcom", ...
    brand: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    memory: Mapped[str | None] = mapped_column(String(30))
    full_name: Mapped[str | None] = mapped_column(String(500))
    price_excellent: Mapped[int | None] = mapped_column(Integer)  # grade B
    price_good: Mapped[int | None] = mapped_column(Integer)       # grade C
    price_poor: Mapped[int | None] = mapped_column(Integer)       # grade D
    price_repair: Mapped[int | None] = mapped_column(Integer)     # grade G
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class AvitoMessage(Base):
    __tablename__ = "avito_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    avito_message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # incoming | outgoing
    author_id: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
