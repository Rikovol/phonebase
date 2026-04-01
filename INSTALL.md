# PhoneBase — Инструкция по установке на новый сервер

## Требования к серверу

- **ОС:** Ubuntu 22.04 / 24.04 LTS (или Debian 12)
- **CPU:** 2+ vCPU
- **RAM:** 4+ ГБ
- **Диск:** 50+ ГБ SSD
- **Порты:** 80, 443 открыты наружу
- **Домен:** привязан A-записью к IP сервера (например `basestock.ru`)

---

## 1. Подготовка сервера

```bash
# Обновить систему
apt update && apt upgrade -y

# Установить Docker и Docker Compose
curl -fsSL https://get.docker.com | bash
systemctl enable docker && systemctl start docker

# Установить Node.js 20+ (для сборки фронтенда)
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

# Установить certbot для SSL (опционально, если нет своих сертификатов)
apt install -y certbot
```

---

## 2. Загрузка проекта

```bash
# Распаковать архив в /root/phonebase (или любую другую директорию)
cd /root
tar xzf phonebase-release.tar.gz
cd phonebase
```

---

## 3. Настройка окружения

```bash
# Скопировать шаблон и заполнить значения
cp .env.example .env
nano .env
```

### Обязательные переменные в `.env`:

| Переменная | Описание | Пример |
|---|---|---|
| `POSTGRES_PASSWORD` | Пароль PostgreSQL | Сгенерировать: `openssl rand -hex 24` |
| `REDIS_PASSWORD` | Пароль Redis | Сгенерировать: `openssl rand -hex 24` |
| `SECRET_KEY` | Ключ JWT-токенов | Сгенерировать: `openssl rand -hex 32` |
| `PD_ENCRYPTION_KEY` | Ключ шифрования ПД (Fernet) | Сгенерировать: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ALLOWED_HOSTS` | Домен сайта | `basestock.ru` |
| `PUBLIC_URL` | Публичный URL | `https://basestock.ru` |
| `ENVIRONMENT` | Режим | `production` |

> Для генерации `PD_ENCRYPTION_KEY` без установки Python:
> ```bash
> docker run --rm python:3.12-slim python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

---

## 4. SSL-сертификаты

### Вариант A: Let's Encrypt (бесплатно)

```bash
# Остановить nginx если запущен (порт 80 должен быть свободен)
certbot certonly --standalone -d ваш-домен.ru -d www.ваш-домен.ru

# Скопировать сертификаты
cp /etc/letsencrypt/live/ваш-домен.ru/fullchain.pem ssl/fullchain.pem
cp /etc/letsencrypt/live/ваш-домен.ru/privkey.pem ssl/privkey.pem
chmod 600 ssl/privkey.pem
```

### Вариант B: Свои сертификаты

Положить файлы в папку `ssl/`:
- `ssl/fullchain.pem` — цепочка сертификатов
- `ssl/privkey.pem` — приватный ключ

### Вариант C: Самоподписанный (для тестов)

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout ssl/privkey.pem -out ssl/fullchain.pem \
  -subj "/CN=localhost"
chmod 600 ssl/privkey.pem
```

---

## 5. Настройка домена в nginx

Если домен отличается от `basestock.ru`, отредактировать `docker/nginx.conf`:

```bash
nano docker/nginx.conf
```

Заменить `basestock.ru` на ваш домен в строках `server_name`.

---

## 6. Сборка фронтенда

```bash
cd frontend
npm ci
npm run build
cd ..
```

После сборки в `frontend/dist/` появятся статические файлы.

---

## 7. Запуск

```bash
# Собрать образы и запустить все контейнеры
docker compose up -d --build

# Проверить что всё запустилось
docker compose ps
```

Ожидаемый результат — 6 контейнеров в статусе `Up`:
- `phonebase-postgres-1` (healthy)
- `phonebase-redis-1`
- `phonebase-backend-1`
- `phonebase-celery-1` (worker)
- `phonebase-celery-beat-1` (планировщик автоимпорта)
- `phonebase-nginx-1`

---

## 8. Инициализация базы данных

При первом запуске таблицы создаются автоматически (SQLAlchemy).
Для загрузки начальных данных (магазины + пользователи):

```bash
docker compose exec postgres psql -U phonebase -f /docker-entrypoint-initdb.d/init.sql
```

Если нужно загрузить тестовых пользователей:

```bash
docker compose exec postgres psql -U phonebase < docker/seed-users.sql
```

---

## 9. Первый вход

1. Открыть `https://ваш-домен.ru`
2. Войти любым пользователем из `docker/seed-users.sql`
   - Логин: `pavel`, `vitaliy`, `ilya`, `anton` (админы) или `roman`, `alisa` и др. (сотрудники)
   - Пароль: `temp`
3. Система потребует сменить пароль при первом входе

---

## 10. Настройка магазинов для Авито

1. Войти как администратор
2. Перейти в **Настройки** (боковое меню)
3. Для каждого магазина заполнить:
   - Телефон для Авито
   - Адрес магазина
   - Имя менеджера
4. В личном кабинете Авито -> Настройки -> Автозагрузка указать URL фида:
   ```
   https://ваш-домен.ru/api/avito/feed/{store_id}.xml
   ```
   `store_id` можно узнать из URL в настройках магазина.

---

## 11. Автопродление SSL (Let's Encrypt)

```bash
# Добавить в crontab
crontab -e
```

Вставить строку:
```
0 3 * * 1 certbot renew --quiet && cp /etc/letsencrypt/live/ваш-домен.ru/fullchain.pem /root/phonebase/ssl/fullchain.pem && cp /etc/letsencrypt/live/ваш-домен.ru/privkey.pem /root/phonebase/ssl/privkey.pem && docker compose -f /root/phonebase/docker-compose.yml restart nginx
```

---

## 12. Бэкапы

### Автоматический бэкап БД (ежедневно)

```bash
mkdir -p /root/backups

cat > /root/backup-phonebase.sh << 'SCRIPT'
#!/bin/bash
BACKUP_DIR="/root/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
docker compose -f /root/phonebase/docker-compose.yml exec -T postgres \
  pg_dump -U phonebase phonebase | gzip > "$BACKUP_DIR/phonebase_$TIMESTAMP.sql.gz"
# Удалить бэкапы старше 30 дней
find "$BACKUP_DIR" -name "phonebase_*.sql.gz" -mtime +30 -delete
SCRIPT

chmod +x /root/backup-phonebase.sh

# Добавить в crontab (каждый день в 4:00)
(crontab -l 2>/dev/null; echo "0 4 * * * /root/backup-phonebase.sh") | crontab -
```

### Восстановление из бэкапа

```bash
gunzip -c /root/backups/phonebase_ДАТА.sql.gz | \
  docker compose exec -T postgres psql -U phonebase phonebase
```

---

## 13. Полезные команды

```bash
# Логи всех контейнеров
docker compose logs -f

# Логи конкретного сервиса
docker compose logs -f backend
docker compose logs -f nginx

# Перезапуск после изменений в коде
docker compose up -d --build backend    # бэкенд
docker compose restart nginx            # nginx (после изменения конфига или фронта)

# Пересборка фронтенда
cd frontend && npm run build && cd .. && docker compose restart nginx

# Статус контейнеров
docker compose ps

# Консоль БД
docker compose exec postgres psql -U phonebase

# Остановить всё
docker compose down

# Остановить и удалить данные (ОСТОРОЖНО!)
docker compose down -v
```

---

## Структура проекта

```
phonebase/
├── .env.example          # Шаблон переменных окружения
├── docker-compose.yml    # Оркестрация контейнеров
├── INSTALL.md            # Эта инструкция
├── backend/              # FastAPI бэкенд (Python)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/              # Код приложения
│   └── fixtures/         # Тестовые данные для импорта 1С
├── frontend/             # React фронтенд (Vite)
│   ├── src/App.jsx       # Основной компонент
│   ├── package.json
│   └── dist/             # Собранные статические файлы
├── docker/               # Конфигурации Docker
│   ├── nginx.conf        # Nginx (reverse proxy + SSL)
│   ├── postgres-init.sql # Инициализация БД
│   └── seed-users.sql    # Начальные пользователи
├── ssl/                  # SSL-сертификаты (fullchain.pem + privkey.pem)
├── scripts/              # Скрипты для локальной разработки
└── docs/                 # Документация
```
