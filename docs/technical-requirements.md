# Технические требования PhoneBase

## Минимальные требования для сервера

| Ресурс | Минимум | Рекомендуемо |
|--------|---------|-------------|
| **RAM** | 2 ГБ | 4 ГБ |
| **CPU** | 1 ядро | 2 ядра |
| **Диск** | 20 ГБ SSD | 50 ГБ SSD |
| **ОС** | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 |

## Стек технологий

| Компонент | Версия | Потребление RAM |
|-----------|--------|----------------|
| PostgreSQL | 16 | ~500 МБ |
| Redis | 7 | ~100 МБ |
| FastAPI + Uvicorn | 0.115 | ~200 МБ |
| Celery (3 процесса) | 5.4 | ~400 МБ |
| Nginx | Alpine | ~50 МБ |
| Python | 3.12 | — |
| Node.js (только сборка) | 22 | — |

## Внешние сервисы

- **1С** — HTML-выгрузка (файл или URL), импорт каждые 30 мин
- **Avito REST API** — OAuth2, статистика, мессенджер, фиды
- **GoodCom** — парсинг цен конкурентов (пятница 03:00)

## Безопасность

- SSL/TLS 1.2+ (nginx)
- Fernet-шифрование персональных данных (отдельный ключ)
- JWT-токены (8 часов + refresh 30 дней)
- Rate limiting: API 30 req/min, авторизация 5 req/min
- LUKS-шифрование тома для персональных данных (рекомендуется)

## База данных

- **12 таблиц**, ~13 индексов
- 2 схемы: `business` (основная) + `personal_data` (изолированная)
- Расширения: `pgcrypto`, `uuid-ossp`

## Кодовая база

- Backend: ~12 900 строк Python (20 зависимостей)
- Frontend: ~4 100 строк React/JSX (SPA, Vite)
- 40+ API эндпоинтов

## Порты

| Порт | Сервис | Доступ |
|------|--------|--------|
| 80/443 | Nginx | Внешний |
| 8000 | FastAPI | Только localhost |
| 5432 | PostgreSQL | Только localhost |
| 6379 | Redis | Только localhost |

## Переменные окружения (25)

### Аутентификация и безопасность
- `SECRET_KEY` — ключ подписи JWT (мин. 32 символа)
- `PD_ENCRYPTION_KEY` — ключ Fernet для шифрования ПД
- `ACCESS_TOKEN_EXPIRE_MINUTES` (по умолчанию: 480)
- `REFRESH_TOKEN_EXPIRE_DAYS` (по умолчанию: 30)

### База данных и кэш
- `DATABASE_URL` — PostgreSQL (asyncpg)
- `REDIS_URL` — Redis брокер

### Хранение файлов
- `MEDIA_ROOT` — фото товаров (по умолчанию: ./media)
- `PD_DOCS_ROOT` — документы ПД (по умолчанию: ./pd_docs)
- `PURCHASE_DOCS_ROOT` — документы закупки (по умолчанию: ./purchase_docs)
- `MAX_PHOTO_SIZE_MB` (по умолчанию: 10)
- `MAX_DOC_SIZE_MB` (по умолчанию: 20)

### Сеть и CORS
- `ALLOWED_HOSTS` — CSV-список хостов
- `CORS_ORIGINS` — CSV-список origins
- `PUBLIC_URL` — базовый URL API

### Интеграция 1С
- `IMPORT_1C_HTML_URL` — URL выгрузки б/у
- `IMPORT_1C_HTML_PATH` — локальный путь (приоритет над URL)
- `IMPORT_1C_NEW_HTML_URL` — URL выгрузки новых
- `IMPORT_1C_NEW_HTML_PATH` — локальный путь новых
- `IMPORT_INTERVAL_MINUTES` (по умолчанию: 30, 0 = отключено)

### Avito REST API
- `AVITO_STATS_INTERVAL_MINUTES` (по умолчанию: 60)
- `AVITO_MESSENGER_INTERVAL_MINUTES` (по умолчанию: 5)
- `AVITO_FEED_CHECK_INTERVAL_MINUTES` (по умолчанию: 120)

### Окружение
- `ENVIRONMENT` — development / production

## Docker-сервисы

| Сервис | Образ | Зависимости |
|--------|-------|------------|
| postgres | postgres:16-alpine | — |
| redis | redis:7-alpine | — |
| backend | python:3.12-slim | postgres, redis |
| celery | python:3.12-slim | postgres, redis |
| celery-beat | python:3.12-slim | redis |
| nginx | nginx:alpine | backend |

## Тома Docker

| Том | Назначение |
|-----|-----------|
| `postgres_data` | Данные PostgreSQL |
| `redis_data` | Персистентность Redis |
| `media_files` | Фото товаров |
| `pd_documents` | Персональные данные (LUKS) |
| `purchase_docs` | Документы закупки по IMEI |
