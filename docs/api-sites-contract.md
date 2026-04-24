# API-контракт публичного роутера `/api/sites/{store_id}/*`

> **Статус**: проектирование 2026-04-24. Готово к реализации.
> **Источник данных** для 3 сайтов-витрин: мобилакс / айпрас / ремгсм.

## 1. Обзор

Публичный API-контракт `/api/sites/{store_id}/*` — источник данных для трёх сайтов-витрин phonebase. Роутер живёт в `backend/app/api/sites.py`, подключается через `app.include_router(sites.router, prefix="/api/sites", tags=["sites"])`.

Контракт решает четыре задачи:
1. **Каталог** — публичные GET для б/у (per-store) и новых (агрегация по `product_key` для всех 3 магазинов).
2. **Промо/бонусы** — активные акции и бонусные правила магазина, включая глобальные (`store_id IS NULL`).
3. **OAuth-авторизация посетителя** через VK ID SDK и Telegram Login Widget, анонимный fallback с rate-limit и honeypot. Сессия хранится в **HttpOnly JWT-cookie**.
4. **Заявки** — `tradein | contact | feedback | order` с привязкой к `SiteVisitor`, валидация `visitor.store_id == message.store_id`.

Trade-in прайс-фид (`/api/feeds/tradein-prices.json?token=...`) остаётся в `feeds.py` — не дублируем.

## 2. Auth-стратегия посетителя сайта

### 2.1 Принципы

- **HttpOnly cookie** `site_session` (SameSite=Lax, Secure в production, Path=`/api/sites`).
- JWT подписан `settings.SECRET_KEY` (HS256), имеет `aud="site_visitor"` — чтобы его нельзя было использовать в CRM-API.
- CORS: `allow_origins=_csv(settings.SITE_ORIGINS)`, `allow_credentials=True`.
- Анонимов не авторизуем — POST /messages принимается с honeypot и rate-limit. JWT выдаётся только после OAuth.

### 2.2 JWT payload

```python
{
    "sub": visitor_id,              # UUID SiteVisitor
    "aud": "site_visitor",
    "store_id": "uuid",
    "provider": "vk" | "telegram",
    "provider_user_id": "12345",
    "exp": <unix-ts>,               # 30 дней
    "iat": <unix-ts>,
    "jti": <uuid>
}
```

Cookie = 30 дней, без refresh (публичный сайт, не SPA-админка). Если истёк — редирект на `/auth/vk/start`.

### 2.3 Endpoints

- `GET /api/sites/{store_id}/auth/vk/start` → 302 redirect на `https://id.vk.com/authorize?client_id=...&redirect_uri=.../auth/vk/callback&response_type=code&scope=email+phone&state=<csrf>&code_challenge=<PKCE>`. PKCE S256 **обязателен** (VK ID, 2024+).
- `GET /api/sites/{store_id}/auth/vk/callback?code=...&state=...` — проверка state, обмен code→token через `POST https://id.vk.com/oauth2/auth`, получение user_info, upsert `SiteVisitor`, выдача cookie, 302 на `SITE_RETURN_URL`.
- `POST /api/sites/{store_id}/auth/telegram/callback` — принимает Telegram Login payload (id, first_name, auth_date, hash). Проверка `HMAC(sha256(bot_token), data_check_string) == hash`, `auth_date < 24h`. Upsert visitor, cookie, 200.
- `POST /api/sites/{store_id}/auth/logout` — удаляет cookie, 204.
- `GET /api/sites/{store_id}/auth/me` — текущий visitor из cookie.

### 2.4 Dependencies

```python
async def get_active_store(store_id: str, db: AsyncSession = Depends(get_db)) -> Store:
    store = await db.get(Store, store_id)
    if not store or not store.is_active:
        raise HTTPException(404, detail="Магазин не найден")
    return store

async def get_site_visitor(
    request: Request,
    store: Store = Depends(get_active_store),
    db: AsyncSession = Depends(get_db),
) -> SiteVisitor | None:
    """Читает cookie, проверяет JWT, проверяет store_id в payload == URL."""
    token = request.cookies.get("site_session")
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], audience="site_visitor")
    except JWTError:
        return None
    if payload.get("store_id") != store.id:
        raise HTTPException(403, detail="Сессия для другого магазина")
    visitor = await db.get(SiteVisitor, payload["sub"])
    if not visitor or visitor.is_blocked:
        return None
    return visitor

async def require_site_visitor(visitor: SiteVisitor | None = Depends(get_site_visitor)) -> SiteVisitor:
    if not visitor:
        raise HTTPException(401, detail="Требуется авторизация")
    return visitor
```

## 3. Каталог (публичный, без auth)

### 3.1 `GET /api/sites/{store_id}/catalog`

**Query params:**

| Параметр | Тип | По умолчанию |
|---|---|---|
| `condition` | `new \| used` | required |
| `brand` | `str` | — |
| `category` | `str` | — |
| `search` | `str` | — |
| `in_stock` | `bool` | `true` |
| `promo_only` | `bool` | `false` |
| `price_from` / `price_to` | `int` | — |
| `sort` | `price_asc \| price_desc \| newest` | `newest` |
| `page`, `per_page` | `int` | `1`, `24` (max 60) |

**Логика:**
- `condition=used` → Product с `store_id=URL.store_id`, `is_new=False`, `site_published=True`, не продан.
- `condition=new` → Product по всем магазинам, `is_new=True`, `site_published=True`, `quantity>0`. **Агрегация по `product_key = lower(brand|model|storage|color)`** — одна карточка на ключ. `min_price = MIN(effective_price)`, `total_quantity = SUM(quantity)`.
- `effective_price = COALESCE(PriceOverride.override_price WHERE store_id=URL.store_id AND is_active, Product.price_retail)`.

**Response:**

```python
class CatalogPromoBadge(BaseModel):
    promotion_id: str
    title: str
    code: str | None

class CatalogItemOut(BaseModel):
    slug: str                   # product_id (used) или product_key base64-safe (new)
    condition: Literal["new", "used"]
    brand: str | None
    model: str
    storage: str | None
    color: str | None
    battery_pct: str | None     # только used
    completeness: str | None
    sim_count: int | None
    sim_type: str | None
    price_retail: int | None    # «перечёркнутая» (исходная)
    price_effective: int        # финальная (после скидки)
    discount_percent: int | None
    promo: CatalogPromoBadge | None
    photo_main: str | None
    photos_count: int
    total_quantity: int

class CatalogOut(BaseModel):
    items: list[CatalogItemOut]
    total: int
    page: int
    per_page: int
    filters_applied: dict
```

### 3.2 `GET /api/sites/{store_id}/product/{slug}`

`slug` = UUID (used) или base64url `product_key` (new).

Детали включают все фото: `ProductPhoto` + `CatalogPhoto` **минус** `HiddenCatalogPhoto(store_id=URL.store_id)`. Для new — агрегация по product_key с `per_store_availability`.

```python
class ProductPhotoItem(BaseModel):
    url: str
    is_main: bool
    source: Literal["product", "catalog"]

class ProductPromoOut(BaseModel):
    promotion_id: str
    title: str
    body: str | None
    code: str | None
    ends_at: datetime | None

class ProductDetailOut(BaseModel):
    slug: str
    condition: Literal["new", "used"]
    brand: str | None
    model: str
    storage: str | None
    color: str | None
    category: str | None
    battery_pct: str | None
    completeness: str | None
    sim_count: int | None
    sim_type: str | None
    price_retail: int | None
    price_effective: int
    discount_percent: int | None
    promo: ProductPromoOut | None
    photos: list[ProductPhotoItem]
    total_quantity: int
    per_store_availability: dict[str, int] | None  # только для new
```

### 3.3 `GET /api/sites/{store_id}/categories` и `/brands`

Фасеты с counts, учитывают agent видимость.

## 4. Акции и бонусы (публичные)

### 4.1 `GET /api/sites/{store_id}/promotions`

Активные: `is_active=True AND (store_id=URL.store_id OR store_id IS NULL) AND now BETWEEN starts_at AND ends_at`. Сортировка `priority DESC, created_at DESC`.

```python
class PromotionOut(BaseModel):
    id: str
    scope: Literal["store", "global"]
    title: str
    body: str | None
    code: str | None
    discount_type: Literal["percent", "fixed", "info_only"]
    discount_value: float | None
    banner_image: str | None
    landing_url: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    priority: int
    applies_to_brand: str | None
    applies_to_category: str | None
```

### 4.2 `GET /api/sites/{store_id}/promotions/{id}` и `/bonuses`

`/promotions/{id}` — детали (404 при scope mismatch).
`/bonuses` — активные `SiteBonus(store_id, is_active=True)` без чувствительных полей.

```python
class BonusOut(BaseModel):
    id: str
    name: str
    description: str | None
    rule_type: Literal["cashback", "accrual", "signup", "referral"]
    accrual_percent: float | None
    accrual_fixed: float | None
    redemption_rate: float | None
    expires_days: int | None
    max_percent_of_order: float | None
```

## 5. Сообщения / заявки

### 5.1 `POST /api/sites/{store_id}/messages`

**Auth:** с cookie — visitor из `get_site_visitor` (проверка `visitor.store_id == URL.store_id` → 403). Без cookie — создаётся новый анон `SiteVisitor`, применяется rate-limit 10/час/IP.

```python
class TradeinFields(BaseModel):
    brand: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=255)
    storage: str | None = Field(None, max_length=30)
    color: str | None = Field(None, max_length=100)
    condition: str | None                # JSON массив проблем
    battery_pct: int | None = Field(None, ge=0, le=100)
    completeness: str | None = Field(None, max_length=255)
    estimated_price: Decimal | None = Field(None, ge=0)

class MessageCreateIn(BaseModel):
    message_type: Literal["tradein", "contact", "feedback", "order"]
    contact_name: str | None = Field(None, max_length=200)
    contact_phone: str | None = Field(None, max_length=30)
    contact_email: EmailStr | None = None
    preferred_channel: Literal["telegram", "max", "vk", "phone", "email", "whatsapp"] | None = None
    subject: str | None = Field(None, max_length=255)
    body: str | None = Field(None, max_length=5000)
    tradein: TradeinFields | None = None

    # Anti-spam
    website: str = Field("", max_length=200)    # honeypot
    time_to_submit_ms: int = Field(..., ge=0)

    @root_validator
    def check_anti_spam(cls, v):
        if v.get("website"):
            raise ValueError("spam")
        if v.get("time_to_submit_ms", 0) < 3000:
            raise ValueError("spam")
        return v

    @root_validator
    def check_tradein_fields(cls, v):
        if v.get("message_type") == "tradein" and not v.get("tradein"):
            raise ValueError("tradein fields required")
        return v
```

**Handler-логика:**

```python
@router.post("/{store_id}/messages", status_code=201)
@limiter.limit("10/hour", key_func=lambda req: req.client.host)
async def create_message(
    store_id: str,
    body: MessageCreateIn,
    request: Request,
    store: Store = Depends(get_active_store),
    visitor: SiteVisitor | None = Depends(get_site_visitor),
    db: AsyncSession = Depends(get_db),
):
    # 1. cross-store leak
    if visitor and visitor.store_id != store.id:
        raise HTTPException(403, detail="Cross-store forbidden")
    # 2. анон → контакт обязателен
    if not visitor and not (body.contact_phone or body.contact_email):
        raise HTTPException(400, detail="Требуется телефон или email")
    # 3. анон → создать visitor
    if not visitor:
        visitor = SiteVisitor(
            store_id=store.id, auth_provider=None,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            contact_email=body.contact_email,
            preferred_channel=body.preferred_channel,
        )
        db.add(visitor); await db.flush()
    # 4. создать SiteMessage с денормализацией
    msg = SiteMessage(
        store_id=store.id, visitor_id=visitor.id,
        message_type=body.message_type,
        is_verified=bool(visitor.auth_provider),
        auth_provider=visitor.auth_provider,
        contact_name=body.contact_name or visitor.display_name,
        contact_phone=body.contact_phone or visitor.contact_phone,
        contact_email=body.contact_email or visitor.contact_email,
        preferred_channel=body.preferred_channel or visitor.preferred_channel,
        subject=body.subject, body=body.body,
        tradein_brand=body.tradein.brand if body.tradein else None,
        # ...остальные tradein_* поля
        ip_address=request.client.host,
        user_agent=request.headers.get("user-agent"),
        referer=request.headers.get("referer"),
    )
    db.add(msg)
    visitor.total_messages_count += 1
    visitor.last_seen_at = _now()
    await db.commit()
    return {"id": msg.id, "status": msg.status}
```

### 5.2 `GET /api/sites/{store_id}/messages/my`

Требует `require_site_visitor`. Возвращает заявки visitor'а со статусами (id, тип, status, body_preview, last_reply_text, answered_at, created_at).

## 6. Trade-in прайс

Остаётся `GET /api/feeds/tradein-prices.json?token=...` в `feeds.py`. Не дублируется в `/api/sites/*`.

## 7. Валидации и защиты

| Защита | Механизм | Где |
|---|---|---|
| Store активен | `get_active_store` | все endpoints |
| Cross-store visitor/message leak | `visitor.store_id == URL.store_id` | POST /messages, get_site_visitor |
| Rate-limit анон POST | slowapi 10/hour per IP | POST /messages без cookie |
| Rate-limit verified POST | slowapi 100/hour per visitor_id | POST /messages с cookie |
| Rate-limit OAuth callback | slowapi 20/hour per IP | /auth/*/callback |
| Honeypot | `website` поле = "" | MessageCreateIn |
| Time-to-submit | ≥ 3000 ms | MessageCreateIn |
| Blocked visitor | `visitor.is_blocked` → 403 | POST /messages |
| CSRF для OAuth | `state` cookie check | /auth/vk/callback |
| Telegram hash verify | HMAC-SHA256(bot_token) | /auth/telegram/callback |
| CORS с credentials | `SITE_ORIGINS`, `allow_credentials=True` | роутер |
| Cookie flags | HttpOnly, SameSite=Lax, Secure (prod) | auth endpoints |
| `aud="site_visitor"` в JWT | Разграничение от CRM-JWT | decode |

## 8. HTTP ошибки

| Код | Когда |
|---|---|
| 400 | Невалидный payload, missing contact у анона, honeypot fail |
| 401 | Cookie missing/expired/invalid |
| 403 | Cross-store leak, blocked visitor, state mismatch |
| 404 | Store/product/promotion не найдены |
| 409 | Race в OAuth upsert (маловероятно) |
| 422 | Pydantic validation (FastAPI default) |
| 429 | slowapi rate-limit exceeded |
| 503 | OAuth конфигурация в env отсутствует |

## 9. Env-переменные (добавить в `backend/app/core/config.py`)

```python
SITE_ORIGINS: str = Field(default="http://localhost:5174")
SITE_RETURN_URL: str = Field(default="http://localhost:5174")
VK_APP_ID: str | None = None
VK_APP_SECRET: str | None = None
VK_REDIRECT_URI: str | None = None
TELEGRAM_BOT_TOKEN: str | None = None
SITE_SESSION_COOKIE_DOMAIN: str | None = None
SITE_SESSION_DAYS: int = 30
```

## 10. Пошаговая реализация

1. `backend/app/api/sites.py` — router + Pydantic schemas (§2–5).
2. `backend/app/main.py` — `app.include_router(sites.router, prefix="/api/sites", tags=["sites"])`.
3. `CORSMiddleware` — добавить `SITE_ORIGINS` в allow_origins.
4. `backend/app/core/config.py` — env §9.
5. `backend/requirements.txt` — `slowapi`, инициализация в main.py.
6. Dependencies `get_active_store`, `get_site_visitor`, `require_site_visitor` в sites.py.
7. Вынести агрегацию new по `product_key` из `services/website_feed.py` в общий helper для переиспользования.
8. Тесты `backend/tests/test_sites.py` (pytest+httpx): каталог, cross-store 403, honeypot, OAuth hash verify.

## 11. Сводка: 13 endpoints, 4 группы

**Auth** (5): `GET /auth/vk/start`, `GET /auth/vk/callback`, `POST /auth/telegram/callback`, `POST /auth/logout`, `GET /auth/me`
**Catalog** (4): `GET /catalog`, `GET /product/{slug}`, `GET /categories`, `GET /brands`
**Promotions/Bonuses** (3): `GET /promotions`, `GET /promotions/{id}`, `GET /bonuses`
**Messages** (2): `POST /messages`, `GET /messages/my`

**Ключевые решения:** HttpOnly JWT cookie с `aud="site_visitor"` (нельзя использовать в CRM), PKCE S256 для VK ID, HMAC-SHA256 для Telegram, partial unique на PriceOverride для одной активной акции на (product_id, store_id), slowapi + honeypot + time-to-submit для защиты POST /messages, cross-store leak защита через `visitor.store_id == URL.store_id`.
