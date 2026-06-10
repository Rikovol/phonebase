# Аудит структуры каталога phonebase ↔ mobileax (2026-06-10)

20 агентов (4 исследователя + адверсариальная верификация). 35 находок → 7 подтверждено, 7 опровергнуто, 19 minor.

## Подтверждено
### [MAJOR] FACET COUNT MISMATCH: /categories vs /menu (backend-models)

**Evidence:** Sites.py:1122 (/categories) uses `select(Product.category, count(Product.id))` on raw string. Sites.py:1290-1307 (/menu) uses `count(DISTINCT concat_ws(...brand, model, storage, color))` for new products. Live data: /categories shows 'Смартфоны 24' but /menu?condition=new shows '73' (Codex r7 comment line 1298). For used: /menu shows counts per category, /categories shows different totals.

**Рекомендация:** Make /categories and /brands always normalize through CatalogModel like /menu does. Remove double-aggregation complexity. For new products, deduplicate by product_key. For used products, deduplicate by category FK not string.

### [MAJOR] /header RETURNS CATEGORIES WITHOUT PRODUCT-EXISTENCE FILTER (backend-models)

**Evidence:** Sites.py:1840-1849 fetches all CatalogCategory.is_visible=true with NO WHERE clause filtering by Product count. Returns empty categories (like 'Для волос' / hair with 0 products). /menu (line 1281+) correctly filters: `WHERE ... Product.model_id IS NOT NULL ... quantity > 0 ...`.

**Рекомендация:** Apply same product-existence filters to /header categories+brands as /menu uses. Either pre-compute in admin or join to Product/CatalogModel with COUNT filter. For real-world data (mobileax: 1 store, ~100 products), unnecessary to list 7 categories when only 6 have products.

### [MAJOR] LEGACY PRODUCT.CATEGORY SYNCHRONIZATION IS MANUAL AND FRAGILE (backend-models)

**Evidence:** Catalog.py:224-230 (UPDATE products SET category = :new_name WHERE model_id IN ...) syncs on CatalogCategory.display_name PATCH. Catalog.py:345-351 similarly syncs Product.brand on CatalogBrand rename. If import or manual Product edit happens without updating CatalogModel, category/brand strings go out-of-sync. Sites.py:480, 662, 695, 951 continue to read stale Product.category.

**Рекомендация:** EITHER: (1) Remove Product.category/brand fields entirely, always compute from CatalogModel FK. OR (2) Make them database triggers or view-backed to guarantee sync. OR (3) Audit all product-create/edit code to ensure CatalogModel link is set + denorm fields are synced.

### [MAJOR] MAJOR: Filter UI parameters never sent to backend API (frontend-usage)

**Evidence:** src/components/catalog/CatalogFilters.tsx sets URL params for storage, color, battery_min, completeness via setParam (lines 78, 88, 98, 108). But fetchCatalog call in [category]/page.tsx:82-91 never reads these URL params — they're never passed to backend. Backend /catalog endpoint has no such query parameters anyway.

**Рекомендация:** Either (a) remove filter UI from CatalogFilters — storage/color are derived client-side in [category]/page.tsx already; (b) implement these as server-side /catalog params if multi-page filtering is needed; or (c) keep as client-side filters for /new, /used only (already working via CatalogClientView) and remove from category pages.

### [MAJOR] MAJOR: Facet endpoints /brands and /categories are declared but never used (frontend-usage)

**Evidence:** src/lib/phonebase-client.ts:70-76 define fetchBrands() and fetchCategories(). Called only once: src/app/page.tsx:16 calls fetchBrands() for cache warming but discards result. /categories endpoint declared but never imported anywhere. Live data indicates /categories returns different counts (legacy string category) than /menu (normalized tree).

**Рекомендация:** Delete unused fetchCategories export unless planned feature exists. Consider whether fetchBrands warming is necessary (result is thrown away; if keeping for ISR, add comment explaining ISR benefit). Clarify which facet is source-of-truth: /menu or /categories.

### [MAJOR] Products can be imported WITHOUT model_id (import-1c)

**Evidence:** backend/app/services/import_sync_new.py:76-90 and import_sync.py:118-132 — if brand/category/model fields incomplete (any None/empty), model_id is set to NULL. resolve_catalog_refs returns None if any param is falsy (catalog_refs.py:93-94).

**Рекомендация:** Products with NULL model_id are invisible in /menu (filters model_id.isnot(None), line 1281) but visible in /categories facet (counts legacy Product.category string, line 1122). This is the source of count divergence. To reduce: (1) validate 1С data has all 3 fields before import, (2) block product creation if classification incomplete, or (3) deprecate /categories facet in favor of /menu.

### [CRITICAL] Битая кнопка «Для волос» в header — категория в CMS без товаров (admin-cms)

**Evidence:** backend/app/api/sites.py:1839-1849 (get_site_header возвращает ВСЕ видимые CatalogCategory независимо от наличия товаров), mobileax-next Header.tsx использует это для построения меню. /menu endpoint (sites.py:1267-1350) ФИЛЬТРУЕТ категории, у которых есть видимые товары (GROUP BY с WHERE). Результат: /header возвращает категорию «Для волос» с count=0 (нет товаров), /menu её не возвращает (нет товаров). Клиенту приходит битая кнопка.

**Рекомендация:** get_site_header должен фильтровать категории так же как /menu: JOIN с Product, WHERE видимые товары в наличии. ИЛИ добавить flag на CatalogCategory.show_in_header и явно управлять, что показывать в header независимо от товаров

## Опровергнуто верификацией

- **THREE INDEPENDENT SOURCES OF TRUTH FOR CATEGORIES** (backend-models): Реальное зерно — два узких low/medium-дефекта, а не «три источника правды»: (а) catalog_refs.py:213-221 — при импорте существующей модели в другой категории FK сохраняет старую категорию, а Product.category получает сырую строку 1С → string≠FK у затронутых товаров; т.к. страница каталога фильтрует строкой Product.category (sites.py:479-480) при display_name из /menu, товар может считаться в меню, но выпасть из листинга. Фикс: при конфликте синхронизировать Product.category с display_name категории найденной модели (или наоборот — алертить). (б) catalog_refs._find_or_create_category создаёт категории из 1С сразу is_visible=true (default) → пустая авто-категория может появиться в /header до одобрения моделей; достаточно создавать авто-категории скрытыми (по аналогии с auto_created-моделями), а не фильтровать /header по наличию товаров. Удалять Product.category нельзя — его читают фиды Avito и фильтры каталога для товаров с model_id=NULL.

- **CRITICAL: Header breaks silently when CMS category has no inventory match** (frontend-usage): Не critical-баг, а намеренная graceful degradation. Реальный остаток находки — UX/merchandising-замечание уровня low/info: категория без товаров в наличии (например 'hair') остаётся в навигации как обычная ссылка и ведёт на пустую страницу /catalog/hair. Это управляется флагом is_visible в админ-разделе «Каталог» (скрыть категорию без стока — продуктовое решение продавца). Опционально: server-side log (не console.warn для админов — это RSC) при расхождении /header vs /menu для observability, но не «синхронизация» эндпоинтов — они намеренно фильтруют по-разному одну и ту же таблицу CatalogCategory.

- **MAJOR: Duplicate storage/color filtering logic across components** (frontend-usage): Корректная формулировка: MINOR «Storage-опции derive дублируются в ДВУХ местах с разной логикой»: src/app/catalog/[category]/page.tsx:98-102 (unsorted) и src/components/catalog/CatalogClientView.tsx:42-53 (numeric sort + Tb-эвристика); color derive только в одном месте (page.tsx:103-107). CatalogFilters — frontend-консьюмер props, не третья точка извлечения. filters_applied в CatalogOut — эхо применённых фильтров, не available-опции, так что «уже сделано в backend» — нет. Настоящая MAJOR-проблема рядом (отдельная находка): селекты «Объём»/«Цвет» на /catalog/[category] — мёртвые контролы: CatalogFilters пишет ?storage=/?color= в URL, но page.tsx эти параметры не читает, тип CatalogFilters (src/types/api.ts:42-56) их не содержит, и backend GET /{store_id}/catalog (phonebase/backend/app/api/sites.py:375-389) их не принимает — выбор фильтра меняет URL, но не выдачу; к тому же опции derive из одной страницы (per_page=60), а не из всего результата.

- **MAJOR: Header dual-fetch (CMS + /menu) creates reconciliation overhead** (frontend-usage): Корректная формулировка (severity=minor/info): Header делает два параллельных кэшированных запроса (/header для promo+полного списка категорий+брендов, /menu для дерева панелей) — оба производны от одной таблицы CatalogCategory, slug-расхождение невозможно. Категория, присутствующая в /header но отсутствующая в /menu (нет товаров в наличии), детерминированно рендерится plain-ссылкой без mega-панели и ведёт на потенциально пустую страницу /catalog/{slug}. Опционально: добавить debug-лог при таком mismatch и/или скрывать категории с products_count=0; консолидация эндпоинтов — архитектурное предпочтение, не фикс бага.

- **Auto-created catalog entries risk misdated taxonomy pollution** (import-1c): Находка описывает реальное поведение auto-create, но её ключевые утверждения опровергнуты кодом: (1) «No normalization beyond slugify()» — неверно: есть strip(), case-insensitive+TRIM матчинг и CATEGORY_REMAP_1C (catalog_refs.py:34-71) — ~30 имён 1С уже маппятся в 7 product-type категорий, т.е. рекомендация «pre-seed 7 categories» для категорий уже реализована (Stage 3, 2026-05-31); (2) рекомендуемый admin UI ревью уже существует: GET /catalog/models?needs_review=true (api/catalog.py:406-432) + баннер «Требуют проверки» в App.jsx:5980/5997; (3) логирование уже есть (logger.info/warning на каждое создание); (4) severity=major не обоснована: «pollution» не достигает витрины — sites.py:1324-1326 фильтрует is_visible=true на всех трёх уровнях, скрытые записи = карантин by design до одобрения продавцом; (5) неточность evidence: auto_created=true ставится только на CatalogModel, у брендов/категорий этого поля нет (business.py:650). Корректная остаточная находка — minor: опечатки из 1С накапливают скрытые дубли-бренды/категории вне модельной needs_review-очереди (флаг auto_created есть только у моделей), а CatalogRefs.created задокументирован «для ImportLog», но вызывающими не персистится. Реальные улучшения: расширить auto_created+needs_review на бренды/категории и писать created в ImportLog. Вариант «REJECT unknown» спорен — он оставит товары без классификации (model_id=NULL), текущий карантин-паттерн безопаснее.

- **/menu shows only used products for specific store, but used import has no category remap** (import-1c): Ремап категорий ПРИМЕНЯЕТСЯ к used-импорту: import_sync.py → resolve_catalog_refs → _find_or_create_category, где CATEGORY_REMAP_1C мапит старые 1С-заголовки («iPhone», «AirPods», «Sony PS»…) в product-type slugs (smartphones/audio/consoles…). Парсер import_1c.py и не должен ремапить — ремап намеренно централизован в catalog_refs.py (Stage 3, spec 2026-05-31), и import_1c_new.py тоже его в парсере не делает. Реальный остаточный риск минорный и другой: 1С-заголовок вне карты CATEGORY_REMAP_1C создаст скрытую legacy-категорию (документированный fallback с модерацией в админке «Требуют проверки»), одинаково для used и new пайплайнов. Severity major не обоснована.

- **Дублирование управления категориями каталога и header'а** (admin-cms): Находку в формулировке «дублирование управления категориями» закрыть как false positive: /header — read-only consumer, дублирования CRUD нет, порядок/видимость категорий header'а уже управляются из Каталог→Категории. Реальное зерно — отдельная minor-находка про promo: HomeSection key='header_promo' читается в get_site_header (sites.py:1812-1837), но не создаваем из админки — ключ отсутствует в ALLOWED_SECTION_KEYS (home_blocks_admin.py:28), endpoint создания секций отсутствует вовсе, seed migrate_seed_home_blocks (db_migrations.py) его не заводит, в SECTION_LABELS (App.jsx:5558) метки нет. Без ручной вставки строки в БД promo-карточка header'а всегда null и витрина показывает hardcoded-дефолт (Header.tsx:125-132). Фикс: добавить header_promo в seed + ALLOWED_SECTION_KEYS + SECTION_LABELS (severity=minor, feature gap).

## Minor

- (backend-models) COMPETITORPRICE MODEL UNUSED: Remove CompetitorPrice model and migration if it was test-populated only. If future admin dashboards will use it, document explicitly. Current 1 store + ~100 products reality doesn't need competitor p
- (backend-models) CART/ORDER MODELS CREATED BUT NOT FULLY INTEGRATED WITH VITRINE: For current scope (1 store, ~100 products, simple shop), decide: (A) Remove Cart/Order models, keep localStorage client-side only. OR (B) Fully integrate server-side cart with vitrine frontend (requir
- (backend-models) HIDDENCATALOGPHOTO COMPLEXITY FOR MINIMAL BENEFIT: For simplification: if mobileax-next only serves 1 store, can ignore HiddenCatalogPhoto entirely (simplify /product detail and /catalog new logic). If 3-store network activates, keep as-is.
- (backend-models) CATALOGPHOTO.PRODUCT_KEY DUPLICATION ON BRAND RENAME: Document that product_key normalization (trim + lower) must match make_product_key() in catalog_photos.py line 23. Consider database-level GENERATED column or app-level materialized view instead of ma
- (backend-models) SITE_PUBLISHED FLAG NOT EXPOSED IN /HEADER, CAUSING INCONSISTENCY: Ensure /header and /menu use identical visibility filters. Either: (A) /header filters by Product.site_published (slower, requires JOIN), or (B) trust admin to manage is_visible correctly (assumes adm
- (frontend-usage) MINOR: METADATA_TITLE_FALLBACK hardcoded category names create drift risk: Fetch menu at generateMetadata time (expensive but prevents stale fallback). OR accept that fallback is best-effort and add comment noting it may be stale. OR use slug as title fallback instead of har
- (frontend-usage) MINOR: /new and /used pages fetch 3 pages client-side, may miss new items or double-count: Either (a) accept 180-item limit is 'enough' for mobileax's 100 new + 15 used, add comment; OR (b) fetch count from first page and calculate total_pages, fetch in series with cache headers; OR (c) imp
- (frontend-usage) MINOR: model_id and advanced filter params (promo_only, in_stock, price_from/to) unused: Remove from types.ts or add TODO comment with expected implementation plan. If these are future-proofing, document that decision. Don't leave in limbo.
- (frontend-usage) MINOR: Category sidebar not present in [category]/page.tsx, breadcrumb-only nav: Not necessarily a bug — depends on product requirements. But if discoverability is important, consider adding a collapsible sidebar or filter drawer that shows brands (from menu tree) and price range 
- (frontend-usage) MINOR: CatalogFilters component never reads or validates passed URL params: Add a whitelist or schema validation to avoid future bugs when adding new params. Not critical since URL tampering is user-controlled, but good defensive programming.
- (import-1c) Category 'hair' (Для волос) is intentional product-type, not CMS mistake: Hair category is pre-seeded for Dyson/hair appliances. Not a bug. For mobileax reality (no hair items): either (a) remove from Stage 3 seed if not needed, or (b) keep as reserved for future Dyson impo
- (import-1c) Import creates stores auto-magically from 1С names: For 1-store reality (only mobileax): (1) freeze store list in production, (2) add validation to reject imports with unknown store names (error instead of auto-create), (3) document expected store name
- (import-1c) resolve_catalog_refs creates 3 levels (brand/category/model) without transaction rollback: Race-safe within single Savepoint. Low risk because IntegrityError → retry SELECT finds the other thread's insertion. For safety: (1) wrap all 3 in single transaction, (2) add audit logging of every a
- (import-1c) is_new column defaults False, used products are default state: For 1-store reality: consolidate to single import source. Current dual pipeline (new + used) adds complexity. If only 1С exports new items, delete legacy sync_import entirely and rename sync_import_ne
- (admin-cms) Недостаточно CMS-сущностей для управления header'ом: Для мобилакс добавить в Магазин→Header явные поля: 1) включить/отключить каждую категорию в header (независимо от видимости в каталоге), 2) переупорядочить, 3) редактировать promo-карточку прямо там. 
- (admin-cms) HomeBlocks (CMS главной) имеет предустановленные секции вместо гибкого конструктора: Для простоты: оставить как есть (достаточно для одного магазина). Если нужна гибкость — перейти на генерирование HomeSection'ов из конфига или UI конструктора
- (admin-cms) SitePromotions и Bonuses как отдельные сущности вместо встроенных в товары: Для мобилакса: эти две сущности ДУБЛИРУЮТ功能. SitePromotion (скидка на товар) vs встроенная скидка в Product.price_override. Выбрать один механизм и удалить второй. Bonuses (программа лояльности) — ред
- (admin-cms) CatalogModelsTab с merge'ом — сложность для одного магазина: Скрыть/удалить UI merge'а для non-admin пользователей или staff'а. Оставить только для администратора
- (admin-cms) Redis кэш на /menu может привести к просроченным данным: Явная инвалидация кэша при PATCH/DELETE модели. Или снизить TTL до 30s для мобилакса

## Решения (Антон, 2026-06-10)

- «Для волос»: привязать товары (ремап 1С уже есть: для волос/фены → hair; товаров в выгрузке пока не было)
- Фильтры storage/color/battery_min/completeness: реализованы на backend (v1.7.5) + frontend (v0.9.2)
- Фасеты /categories /brands: deprecated v1.7.5, удаление в v1.8
- 15 мёртвых невидимых категорий в catalog_categories (iphone, ipad, macbook...) — кандидаты на удаление, ждёт подтверждения
- CompetitorPrice НЕ удалять — нужен для трейд-ин аналитики