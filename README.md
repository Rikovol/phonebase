# PhoneBase — Система учёта БУ телефонов

Внутренняя CRM для сети магазинов б/у телефонов: учёт товаров, импорт из 1С, аналитика цен, интеграция с Авито, выгрузка на сайт.

## Содержание

- [Быстрый старт (локально)](#быстрый-старт-локально)
- [Установка на сервер](#установка-на-сервер)
- [Обновление](#обновление)
- [Управление](#управление)
- [Структура проекта](#структура-проекта)
- [Безопасность (152-ФЗ)](#безопасность-152-фз)

---

## Быстрый старт (локально)

```bash
cp .env.example .env
# Заполнить .env (пароли, SECRET_KEY, PD_ENCRYPTION_KEY)
./deploy.sh setup
```

Сайт откроется на `https://localhost` (или домен из `.env`).

---

## Установка на сервер

### Требования

- Ubuntu 22.04 / 24.04 LTS
- Публичный IP-адрес
- Домен с A-записью, указывающей на сервер
- Root-доступ по SSH

### Один командой

```bash
curl -fsSL https://raw.githubusercontent.com/Rikovol/phonebase/main/server-setup.sh | sudo bash
```

Скрипт задаст вопросы (домен, email для SSL) и выполнит все шаги автоматически.

### Что делает скрипт

| Шаг | Действие |
|-----|---------|
| Системные пакеты | curl, git, certbot, nodejs/npm |
| Docker | Устанавливает Docker + Compose |
| Git clone | Клонирует репозиторий в `/opt/phonebase` |
| `.env` | Генерирует файл с паролями (автоматически) |
| Фронтенд | `npm ci && npm run build` |
| Docker Compose | Запускает все контейнеры |
| SSL | Let's Encrypt сертификат через Certbot |
| Автообновление SSL | Cron каждый понедельник в 04:00 |
| UFW | Открывает порты 22, 80, 443 |
| Systemd | Автозапуск после перезагрузки |

### Идемпотентность

Каждый шаг отмечается в `/var/lib/phonebase/.setup_state`. При повторном запуске выполненные шаги пропускаются — безопасно запускать снова после ошибки:

```bash
sudo bash server-setup.sh
```

### Сохранённые пароли

Все сгенерированные пароли хранятся в `/var/lib/phonebase/.config` (chmod 600):

```bash
sudo cat /var/lib/phonebase/.config
```

---

## Обновление

```bash
cd /opt/phonebase
./deploy.sh update
```

Команда: `git pull` → пересборка фронтенда → перезапуск Docker-контейнеров.

---

## Управление

```bash
./deploy.sh status     # Статус контейнеров + API healthcheck
./deploy.sh logs       # Все логи (Ctrl+C для выхода)
./deploy.sh logs backend   # Логи конкретного сервиса
./deploy.sh restart    # Перезапуск контейнеров
./deploy.sh backup     # Бэкап базы данных → backups/
```

---

## Структура проекта

```
phonebase/
├── docker-compose.yml          # Все сервисы (backend, postgres, redis, nginx, celery)
├── .env.example                # Шаблон переменных окружения
├── deploy.sh                   # Скрипт управления (setup/update/status/logs/backup)
├── server-setup.sh             # Идемпотентная установка на чистый сервер
│
├── backend/
│   ├── requirements.txt
│   ├── Dockerfile
│   └── app/
│       ├── main.py             # FastAPI приложение
│       ├── celery_app.py       # Celery + Redis
│       ├── core/
│       │   ├── config.py       # Настройки из .env
│       │   ├── database.py     # Подключение к PostgreSQL (asyncpg)
│       │   └── security.py     # JWT, роли, зависимости
│       ├── models/
│       │   └── business.py     # Товары, магазины, пользователи, конкуренты
│       ├── api/
│       │   ├── auth.py         # /api/auth/
│       │   ├── products.py     # /api/products/
│       │   ├── stores.py       # /api/stores/
│       │   ├── imports.py      # /api/imports/ — загрузка HTML из 1С
│       │   ├── photos.py       # /api/photos/ — фото товаров
│       │   ├── analytics.py    # /api/analytics/ — аналитика цен
│       │   ├── avito.py        # /api/avito/ — Авито API
│       │   └── competitor_prices.py  # /api/competitor-prices/ — цены конкурентов
│       └── services/
│           ├── import_1c.py    # Парсер HTML-выгрузки 1С
│           ├── pd_encryption.py # Шифрование ПД (152-ФЗ)
│           ├── avito_api.py    # Авито REST API клиент
│           ├── avito_import.py # Импорт объявлений с Авито
│           ├── avito_sync.py   # Синхронизация цен на Авито
│           ├── parse_goodcom.py # Парсинг цен GoodCom
│           ├── website_feed.py # JSON-фид б/у товаров для сайта
│           └── auto_import.py  # Расписание автоимпорта
│
├── frontend/
│   └── src/
│       └── App.jsx             # React SPA (навигация, все страницы)
│
├── docker/
│   ├── postgres-init.sql       # Инициализация БД
│   └── nginx.conf              # TLS, rate limiting, security headers
│
├── fixtures/
│   └── goodcom_prices.csv      # Справочник цен GoodCom (1454 устройства)
│
└── docs/
    └── technical-requirements.md  # Технические требования к проекту
```

---

## Безопасность (152-ФЗ)

| Мера | Реализация |
|------|-----------|
| Шифрование ПД | Fernet (AES-128) на уровне приложения |
| Шифрование секретов Авито | Fernet, ключ в `PD_ENCRYPTION_KEY` |
| Журнал доступа | `personal_data.access_log` |
| Разграничение доступа | JWT роли: admin / manager / info |
| TLS | Nginx + Let's Encrypt, HSTS |
| Пароли в БД | bcrypt |
| Секреты | Только через `.env`, не в репозитории |
