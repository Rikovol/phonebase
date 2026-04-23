import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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


class StaffActionLog(Base):
    __tablename__ = "staff_action_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[str | None] = mapped_column(Text)
    store_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


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


class CatalogPhoto(Base):
    """Фото для каталога новых товаров — привязка к наименованию (brand+model+storage), а не к IMEI."""
    __tablename__ = "catalog_photos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False)
    product_key: Mapped[str] = mapped_column(String(500), nullable=False, index=True)  # lower(brand|model|storage)
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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


# ── Site-witnessed объекты (заявки, акции, бонусы, корректировки цен) ──────
# Добавлено 2026-04-24: phonebase становится multi-tenant CRM для 3 магазинов
# (мобилакс/айпрас/ремгсм). Сайты-витрины питаются из этих таблиц через
# публичный API /api/sites/{store_id}/*.


class SiteVisitor(Base):
    """Клиент сайта магазина. Агрегирует все обращения одного человека.

    Идентификация:
    - Авторизованные через OAuth (VK/Telegram/MAX) — UNIQUE по (store_id, auth_provider, auth_provider_user_id).
    - Анонимные (rate-limit + honeypot) — отдельная запись на каждое обращение,
      последующая мерж-логика по phone/email — в админке вручную.

    Продавец видит в карточке заявки: "клиент с января, 5 обращений, VK ID 123".
    """
    __tablename__ = "site_visitors"
    __table_args__ = (
        # PostgreSQL: NULL != NULL в UNIQUE, поэтому анонимные (auth_provider=NULL)
        # НЕ дедуплицируются — каждое анонимное обращение создаёт нового visitor.
        # Это by design: без идентификатора мы не можем сопоставить. Ручное слияние
        # в админке продавца по phone/email.
        UniqueConstraint("store_id", "auth_provider", "auth_provider_user_id",
                         name="uq_site_visitors_auth"),
        Index("idx_site_visitors_store_phone", "store_id", "contact_phone"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False, index=True)

    # OAuth-идентификация (nullable — анонимы без auth)
    auth_provider: Mapped[str | None] = mapped_column(String(20))  # vk | telegram | max | null=anonymous
    auth_provider_user_id: Mapped[str | None] = mapped_column(String(100))  # VK user_id, Telegram chat_id и т.п.
    avatar_url: Mapped[str | None] = mapped_column(String(500))

    # Контакт
    display_name: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30), index=True)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    preferred_channel: Mapped[str | None] = mapped_column(String(20))  # telegram | max | vk | phone | email

    # Заметки продавца об этом клиенте
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)  # JSON массив тэгов для сегментации

    # Счётчики (денормализация для быстрого UI)
    total_messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # бан за спам

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)


class SiteMessage(Base):
    """Заявки с сайта магазина: Trade-in оценка + контакт-формы + обратная связь.

    Бизнес-правила (2026-04-24):
    - Сообщение считается **прочитанным** ТОЛЬКО после того, как продавец дал ответ
      (answered_at IS NOT NULL). Просто открытие карточки в админке прочитанным не делает.
    - Счётчик непрочитанных в UI = COUNT(WHERE answered_at IS NULL AND status NOT IN ('closed','spam')).
    - Жизненный цикл: new → in_progress (взял в работу) → answered (дал ответ) → closed (вопрос решён).
      Статус spam — отдельно, для удаляемых заявок.
    """
    __tablename__ = "site_messages"
    __table_args__ = (
        # Типовой запрос админки: новые заявки магазина за период
        Index("idx_site_messages_store_status_created", "store_id", "status", "created_at"),
        # Быстрый счётчик непрочитанных (без ответа) по магазину
        Index("idx_site_messages_store_unread", "store_id", "created_at", postgresql_where="answered_at IS NULL"),
        # История обращений одного клиента
        Index("idx_site_messages_visitor_created", "visitor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False, index=True)
    visitor_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("site_visitors.id", ondelete="SET NULL"), index=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # tradein | contact | feedback | order
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", index=True)  # new | in_progress | answered | closed | spam

    # OAuth-верификация (денормализация из SiteVisitor для быстрых фильтров в админке)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    auth_provider: Mapped[str | None] = mapped_column(String(20))  # vk | telegram | max | null=anonymous

    # Контакт клиента (либо из OAuth-профиля, либо заполнен вручную)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(30), index=True)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    preferred_channel: Mapped[str | None] = mapped_column(String(20))  # telegram | max | vk | phone | email | whatsapp

    # Trade-in специфичное (когда message_type='tradein')
    tradein_brand: Mapped[str | None] = mapped_column(String(100))
    tradein_model: Mapped[str | None] = mapped_column(String(255))
    tradein_storage: Mapped[str | None] = mapped_column(String(30))
    tradein_color: Mapped[str | None] = mapped_column(String(100))
    tradein_condition: Mapped[str | None] = mapped_column(Text)          # JSON список проблем / оценка состояния
    tradein_battery_pct: Mapped[int | None] = mapped_column(Integer)
    tradein_completeness: Mapped[str | None] = mapped_column(String(255))  # что в комплекте
    tradein_estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # цена, которую показал виджет

    # Контакт-форма / обратная связь (когда message_type='contact' | 'feedback')
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)

    # Технические метаданные
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    referer: Mapped[str | None] = mapped_column(String(500))
    raw_payload: Mapped[str | None] = mapped_column(Text)  # JSON исходного запроса

    # Процесс обработки
    assigned_to: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)  # внутренние заметки продавца

    # Ответ клиенту (прочитанным заявка становится только после ответа — answered_at IS NOT NULL)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    answered_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    last_reply_text: Mapped[str | None] = mapped_column(Text)  # последний ответ продавца (для превью в админке)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SitePromotion(Base):
    """Акции магазина (скидки, спецпредложения). store_id=NULL → глобальная акция всех магазинов."""
    __tablename__ = "site_promotions"
    __table_args__ = (
        # Частичный индекс для быстрого поиска глобальных акций
        Index("idx_promotions_global", "priority", postgresql_where="store_id IS NULL"),
        # Составной индекс: активные акции магазина по приоритету
        Index("idx_promotions_store_active", "store_id", "is_active", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("stores.id"))  # NULL = global
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)  # описание / HTML / markdown
    code: Mapped[str | None] = mapped_column(String(64), index=True)  # промокод, если есть

    discount_type: Mapped[str] = mapped_column(String(20), nullable=False, default="info_only")  # percent | fixed | info_only
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Условия применения (опционально)
    applies_to_brand: Mapped[str | None] = mapped_column(String(100))
    applies_to_category: Mapped[str | None] = mapped_column(String(100))
    applies_to_products: Mapped[str | None] = mapped_column(Text)  # JSON массив product_ids
    min_order_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Период
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # для сортировки

    # UI
    banner_image: Mapped[str | None] = mapped_column(String(500))
    landing_url: Mapped[str | None] = mapped_column(String(500))

    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class SiteBonus(Base):
    """Бонусные программы магазина (кэшбэк, баллы за покупку и т.п.)."""
    __tablename__ = "site_bonuses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)  # cashback | accrual | signup | referral

    accrual_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))   # % от суммы заказа
    accrual_fixed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))    # фиксированные баллы
    redemption_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))  # 1 балл = X рублей

    min_balance_to_use: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_percent_of_order: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # макс % оплаты бонусами

    expires_days: Mapped[int | None] = mapped_column(Integer)  # срок жизни баллов
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class PriceOverride(Base):
    """Скидка на товар от магазина, привязанная к акции (SitePromotion).

    Бизнес-правила (2026-04-24):
    - Истинная цена товара — products.price_retail (CRM).
    - Магазин может применить скидку ТОЛЬКО через существующую акцию (SitePromotion).
    - У одного товара в одном магазине — только ОДНА активная скидка
      (unique на пару (product_id, store_id) WHERE is_active=true).
    - В разных магазинах скидки на тот же товар — независимы: мобилакс может
      выставить свою скидку, айпрас свою. Это отдельные записи PriceOverride.
    - На сайте отображается: перечёркнутая price_retail, override_price, название акции.
    - Неактивные записи остаются в истории (для аналитики промо-кампаний).
    """
    __tablename__ = "price_overrides"
    __table_args__ = (
        # Одна активная скидка на товар в магазине (история через is_active=false сохраняется)
        Index(
            "uq_price_overrides_active",
            "product_id", "store_id",
            unique=True,
            postgresql_where="is_active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False, index=True)
    promotion_id: Mapped[str] = mapped_column(String(36), ForeignKey("site_promotions.id", ondelete="CASCADE"), nullable=False, index=True)

    override_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)  # финальная цена (после скидки)

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class HiddenCatalogPhoto(Base):
    """Общие фото из catalog_photos, которые магазин скрыл у себя (но не удалил)."""
    __tablename__ = "hidden_catalog_photos"
    __table_args__ = (
        UniqueConstraint("store_id", "catalog_photo_id", name="uq_hidden_catalog_photos_store_photo"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id"), nullable=False, index=True)
    catalog_photo_id: Mapped[str] = mapped_column(String(36), ForeignKey("catalog_photos.id", ondelete="CASCADE"), nullable=False, index=True)
    hidden_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
