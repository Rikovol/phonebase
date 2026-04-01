# PhoneBase — Система учёта БУ телефонов

## Структура проекта

```
phonebase/
├── docker-compose.yml          # Все сервисы
├── .env.example                # Шаблон переменных окружения
│
├── backend/
│   ├── requirements.txt
│   ├── Dockerfile
│   └── app/
│       ├── main.py             # FastAPI приложение
│       ├── celery_app.py       # Celery + Redis
│       ├── core/
│       │   ├── config.py       # Настройки из .env
│       │   ├── database.py     # Подключение к PostgreSQL
│       │   └── security.py     # JWT, роли, зависимости
│       ├── models/
│       │   ├── business.py     # Товары, магазины, пользователи
│       │   └── personal_data.py # ПД клиентов (schema: personal_data)
│       ├── schemas/            # Pydantic схемы
│       ├── api/
│       │   ├── auth.py         # /api/auth/
│       │   ├── products.py     # /api/products/
│       │   ├── stores.py       # /api/stores/
│       │   ├── imports.py      # /api/imports/ — загрузка HTML из 1С
│       │   ├── photos.py       # /api/photos/ — фото товаров
│       │   └── personal_data.py # /api/pd/ — ПД клиентов (только admin)
│       └── services/
│           ├── import_1c.py    # Парсер HTML-выгрузки 1С
│           ├── pd_encryption.py # Шифрование ПД (152-ФЗ)
│           ├── photo_service.py # Обработка фото товаров
│           └── avito_parser.py # Парсинг цен с Авито
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Products.tsx    # Каталог товаров
│       │   ├── ProductCard.tsx # Карточка товара с фото
│       │   ├── Import.tsx      # Загрузка из 1С
│       │   ├── Analytics.tsx   # Аналитика цен
│       │   └── PersonalData.tsx # ПД (только admin)
│       ├── components/
│       ├── store/              # Zustand / Redux
│       └── api/                # API клиент
│
├── docker/
│   ├── postgres-init.sql       # Инициализация БД + схемы
│   └── nginx.conf              # TLS, rate limiting, security headers
│
└── docs/
    └── DEPLOYMENT.md           # Инструкция по развёртыванию (152-ФЗ)
```

## Быстрый старт

```bash
cp .env.example .env
# Заполнить .env
docker compose up -d
```

## Ключевые принципы безопасности (152-ФЗ)

| Мера | Реализация |
|------|-----------|
| Шифрование ПД | Fernet (AES-128) на уровне приложения |
| Шифрование файлов | Fernet + LUKS на уровне диска |
| Журнал доступа | personal_data.access_log (без права DELETE) |
| Разграничение доступа | Две схемы БД + JWT роли |
| TLS | Nginx TLS 1.2/1.3 + HSTS |
| Согласие клиента | Хранится вместе с записью ПД |
| Уничтожение ПД | Перезапись нулями + акт в журнале |
