#!/usr/bin/env bash
# ============================================================
# PhoneBase — полная установка сервера
# Разработка: Моблесс Студия, 2026
# Запуск: sudo bash setup.sh
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; exit 1; }
ask()  { echo -en "${CYAN}[?]${NC} $1: "; read -r "$2"; }

# ── Проверки ──────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Запустите от root: sudo bash setup.sh"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ══════════════════════════════════════════════════════════════
# РЕЖИМ УДАЛЕНИЯ: sudo bash setup.sh --uninstall
# ══════════════════════════════════════════════════════════════
if [[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" || "${1:-}" == "uninstall" ]]; then
    echo ""
    echo -e "${RED}${BOLD}=== Удаление PhoneBase ===${NC}"
    echo ""
    echo -e "  Будет удалено:"
    echo "    - Docker-контейнеры и volumes (БД, медиа, документы)"
    echo "    - Файл .env (секреты и ключи)"
    echo "    - SSL-сертификаты из $PROJECT_DIR/ssl/"
    echo "    - Let's Encrypt сертификаты для домена"
    echo "    - Cron-задачи (SSL-обновление, бэкапы)"
    echo "    - Скрипты /usr/local/bin/phonebase-*.sh"
    echo "    - Правила фаервола UFW"
    echo "    - Fail2Ban jail для PhoneBase"
    echo "    - Собранный фронтенд (frontend/dist)"
    echo ""
    echo -e "  ${YELLOW}НЕ удаляется:${NC}"
    echo "    - Исходный код проекта ($PROJECT_DIR)"
    echo "    - Docker, Node.js, Certbot (системные пакеты)"
    echo "    - Бэкапы (/root/backups/)"
    echo ""
    echo -en "${RED}${BOLD}  Вы уверены? Все данные БД будут потеряны! (yes/no): ${NC}"
    read -r CONFIRM_DEL
    [[ "$CONFIRM_DEL" != "yes" ]] && { echo "Отменено."; exit 0; }

    echo ""

    # 1. Остановить и удалить контейнеры + volumes
    log "Остановка и удаление контейнеров..."
    if docker compose -f "$PROJECT_DIR/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
        docker compose -f "$PROJECT_DIR/docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
    else
        warn "Контейнеры не запущены"
    fi

    # 2. Удалить Docker-образы проекта
    log "Удаление Docker-образов..."
    docker images --filter "reference=phonebase-*" -q 2>/dev/null | xargs -r docker rmi -f 2>/dev/null || true

    # 3. Удалить .env
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        log "Удаление .env..."
        rm -f "$PROJECT_DIR/.env"
    fi

    # 4. Удалить SSL-сертификаты
    log "Удаление SSL-сертификатов..."
    rm -f "$PROJECT_DIR/ssl/fullchain.pem" "$PROJECT_DIR/ssl/privkey.pem"

    # Определить домен из nginx.conf для удаления Let's Encrypt
    LE_DOMAIN=$(grep -oP 'server_name\s+\K[^;]+' "$PROJECT_DIR/docker/nginx.conf" 2>/dev/null | head -1 | awk '{print $1}')
    if [[ -n "$LE_DOMAIN" && -d "/etc/letsencrypt/live/$LE_DOMAIN" ]]; then
        log "Удаление Let's Encrypt сертификата для $LE_DOMAIN..."
        certbot delete --cert-name "$LE_DOMAIN" --non-interactive 2>/dev/null || true
    fi

    # 5. Удалить cron-задачи
    log "Удаление cron-задач..."
    (crontab -l 2>/dev/null | grep -v phonebase-renew-ssl | grep -v phonebase-backup) | crontab - 2>/dev/null || true

    # 6. Удалить скрипты
    log "Удаление скриптов..."
    rm -f /usr/local/bin/phonebase-renew-ssl.sh
    rm -f /usr/local/bin/phonebase-backup.sh

    # 7. Удалить собранный фронтенд
    if [[ -d "$PROJECT_DIR/frontend/dist" ]]; then
        log "Удаление сборки фронтенда..."
        rm -rf "$PROJECT_DIR/frontend/dist"
    fi

    # 8. Сбросить UFW
    log "Сброс правил фаервола..."
    ufw --force reset >/dev/null 2>&1 || true
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    ufw allow ssh >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true

    # 9. Удалить Fail2Ban jail
    if [[ -f /etc/fail2ban/jail.local ]]; then
        log "Удаление конфигурации Fail2Ban..."
        rm -f /etc/fail2ban/jail.local
        systemctl restart fail2ban 2>/dev/null || true
    fi

    # 10. Удалить логи
    rm -f /var/log/phonebase-ssl-renew.log
    rm -f /var/log/phonebase-backup.log

    echo ""
    echo -e "${GREEN}${BOLD}============================================${NC}"
    echo -e "${GREEN}${BOLD}   PhoneBase полностью удалён${NC}"
    echo -e "${GREEN}${BOLD}============================================${NC}"
    echo ""
    echo -e "  ${BOLD}Сохранено:${NC}"
    echo "    - Исходный код: $PROJECT_DIR"
    echo "    - Бэкапы БД:    /root/backups/"
    echo ""
    echo "  Для полного удаления исходников:"
    echo "    rm -rf $PROJECT_DIR"
    echo ""
    echo "  Для удаления бэкапов:"
    echo "    rm -rf /root/backups/"
    echo ""
    echo -e "  ${CYAN}${BOLD}Моблесс Студия, 2026${NC}"
    echo ""
    exit 0
fi

log "Каталог проекта: ${BOLD}$PROJECT_DIR${NC}"

# ── 1. Сбор параметров ───────────────────────────────────────
echo ""
echo -e "${BOLD}=== Настройка PhoneBase ===${NC}"
echo ""

ask "Домен сайта (например basestock.ru)" DOMAIN
[[ -z "$DOMAIN" ]] && err "Домен обязателен"

ask "Email для Let's Encrypt SSL-сертификата" SSL_EMAIL
[[ -z "$SSL_EMAIL" ]] && err "Email обязателен"

echo ""
echo -e "${BOLD}--- Администратор ---${NC}"
ask "Логин администратора" ADMIN_USER
[[ -z "$ADMIN_USER" ]] && ADMIN_USER="admin"

ask "Полное имя администратора" ADMIN_NAME
[[ -z "$ADMIN_NAME" ]] && ADMIN_NAME="Администратор"

while true; do
    echo -en "${CYAN}[?]${NC} Пароль администратора (мин. 8 символов): "
    read -rs ADMIN_PASS
    echo ""
    [[ ${#ADMIN_PASS} -ge 8 ]] && break
    warn "Пароль слишком короткий, минимум 8 символов"
done

echo ""
echo -e "${BOLD}--- Импорт 1С (опционально) ---${NC}"
ask "Путь к файлу Б/У товаров (Enter чтобы пропустить)" IMPORT_1C_PATH
ask "Путь к файлу НОВЫХ товаров (Enter чтобы пропустить)" IMPORT_1C_NEW_PATH
ask "Интервал автоимпорта в минутах (30 по умолч., 0 — выкл.)" IMPORT_INTERVAL
[[ -z "$IMPORT_INTERVAL" ]] && IMPORT_INTERVAL=30

echo ""
echo -e "${BOLD}Параметры:${NC}"
echo "  Домен:        $DOMAIN"
echo "  SSL Email:    $SSL_EMAIL"
echo "  Админ:        $ADMIN_USER ($ADMIN_NAME)"
echo "  1С Б/У файл:  ${IMPORT_1C_PATH:-не задан}"
echo "  1С Новые:     ${IMPORT_1C_NEW_PATH:-не задан}"
echo "  Автоимпорт:   ${IMPORT_INTERVAL} мин."
echo ""
echo -en "${CYAN}[?]${NC} Продолжить? (y/n): "
read -r CONFIRM
[[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]] && err "Отменено"

# ── 2. Установка системных пакетов ───────────────────────────
log "Обновление системы..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

log "Установка базовых пакетов..."
apt-get install -y -qq curl wget gnupg lsb-release ca-certificates \
    ufw fail2ban unattended-upgrades apt-listchanges

# ── 3. Docker ─────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Установка Docker..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker && systemctl start docker
else
    log "Docker уже установлен: $(docker --version)"
fi

if ! docker compose version &>/dev/null; then
    err "Docker Compose не найден. Обновите Docker."
fi

# ── 4. Node.js ────────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ $(node -v | sed 's/v//' | cut -d. -f1) -lt 18 ]]; then
    log "Установка Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
else
    log "Node.js уже установлен: $(node -v)"
fi

# ── 5. Certbot ────────────────────────────────────────────────
if ! command -v certbot &>/dev/null; then
    log "Установка Certbot..."
    apt-get install -y -qq certbot
else
    log "Certbot уже установлен"
fi

# ── 6. Фаервол (UFW) ─────────────────────────────────────────
log "Настройка фаервола..."
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow ssh >/dev/null
ufw allow 443/tcp >/dev/null
# Порт 80 НЕ открываем постоянно — только для certbot
ufw --force enable >/dev/null
log "UFW: SSH(22) + HTTPS(443). Порт 80 закрыт (открывается только для certbot)"

# ── 7. Fail2Ban ───────────────────────────────────────────────
log "Настройка Fail2Ban..."
cat > /etc/fail2ban/jail.local << 'F2B'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = ssh
F2B
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban

# ── 8. Автообновления безопасности ────────────────────────────
log "Включение автообновлений безопасности..."
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'AU'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
AU

# ── 9. Генерация ключей ──────────────────────────────────────
log "Генерация криптографических ключей..."
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)

# Fernet-ключ генерируем через Python (временный контейнер)
PD_ENCRYPTION_KEY=$(docker run --rm python:3.12-slim python3 -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

log "Ключи сгенерированы"

# ── 10. Создание .env ────────────────────────────────────────
log "Создание .env..."
cat > "$PROJECT_DIR/.env" << ENVFILE
# PhoneBase — сгенерировано setup.sh $(date +%Y-%m-%d)
POSTGRES_DB=phonebase
POSTGRES_USER=phonebase
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
SECRET_KEY=${SECRET_KEY}
PD_ENCRYPTION_KEY=${PD_ENCRYPTION_KEY}
ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN}
PUBLIC_URL=https://${DOMAIN}
ENVIRONMENT=production
IMPORT_1C_HTML_PATH=${IMPORT_1C_PATH:-}
IMPORT_1C_NEW_HTML_PATH=${IMPORT_1C_NEW_PATH:-}
IMPORT_INTERVAL_MINUTES=${IMPORT_INTERVAL}
ENVFILE

chmod 600 "$PROJECT_DIR/.env"
log ".env создан (chmod 600)"

# ── 11. Домен в nginx.conf ───────────────────────────────────
log "Настройка nginx для домена ${DOMAIN}..."
sed -i "s/basestock\.ru/${DOMAIN}/g" "$PROJECT_DIR/docker/nginx.conf"

# ── 12. SSL-сертификат ───────────────────────────────────────
log "Получение SSL-сертификата Let's Encrypt..."
mkdir -p "$PROJECT_DIR/ssl"

# Временно открыть порт 80
ufw allow 80/tcp >/dev/null
log "Порт 80 открыт для certbot"

certbot certonly --standalone --non-interactive --agree-tos \
    --email "$SSL_EMAIL" \
    -d "$DOMAIN" -d "www.$DOMAIN" \
    || { warn "Certbot не смог получить сертификат. Создаём самоподписанный."; \
         openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
           -keyout "$PROJECT_DIR/ssl/privkey.pem" \
           -out "$PROJECT_DIR/ssl/fullchain.pem" \
           -subj "/CN=$DOMAIN"; }

# Копировать сертификаты если certbot успешен
if [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
    cp "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$PROJECT_DIR/ssl/fullchain.pem"
    cp "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$PROJECT_DIR/ssl/privkey.pem"
    log "Let's Encrypt сертификат установлен"
fi

chmod 600 "$PROJECT_DIR/ssl/privkey.pem"

# Закрыть порт 80
ufw delete allow 80/tcp >/dev/null
log "Порт 80 закрыт"

# ── 13. Скрипт обновления сертификата ─────────────────────────
log "Создание скрипта автообновления SSL..."
cat > /usr/local/bin/phonebase-renew-ssl.sh << RENEW
#!/bin/bash
# PhoneBase — обновление SSL с временным открытием порта 80
set -e
LOG="/var/log/phonebase-ssl-renew.log"
echo "[\$(date)] Начало обновления SSL" >> "\$LOG"

# Открыть порт 80
ufw allow 80/tcp >> "\$LOG" 2>&1

# Обновить сертификат
certbot renew --quiet >> "\$LOG" 2>&1
RESULT=\$?

# Закрыть порт 80
ufw delete allow 80/tcp >> "\$LOG" 2>&1

if [[ \$RESULT -eq 0 ]] && [[ -f /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ]]; then
    cp /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ${PROJECT_DIR}/ssl/fullchain.pem
    cp /etc/letsencrypt/live/${DOMAIN}/privkey.pem ${PROJECT_DIR}/ssl/privkey.pem
    chmod 600 ${PROJECT_DIR}/ssl/privkey.pem
    docker compose -f ${PROJECT_DIR}/docker-compose.yml restart nginx >> "\$LOG" 2>&1
    echo "[\$(date)] SSL обновлён, nginx перезапущен" >> "\$LOG"
else
    echo "[\$(date)] Обновление не требуется или ошибка (код \$RESULT)" >> "\$LOG"
fi
RENEW
chmod +x /usr/local/bin/phonebase-renew-ssl.sh

# Cron: каждый понедельник в 3:00
(crontab -l 2>/dev/null | grep -v phonebase-renew-ssl; \
 echo "0 3 * * 1 /usr/local/bin/phonebase-renew-ssl.sh") | crontab -
log "Cron-задача обновления SSL добавлена (пн 03:00)"

# ── 14. Скрипт бэкапов ───────────────────────────────────────
log "Создание скрипта бэкапов..."
mkdir -p /root/backups

cat > /usr/local/bin/phonebase-backup.sh << 'BACKUP'
#!/bin/bash
set -e
BACKUP_DIR="/root/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
COMPOSE_FILE="/root/phonebase/docker-compose.yml"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U phonebase phonebase | gzip > "$BACKUP_DIR/phonebase_$TIMESTAMP.sql.gz"

find "$BACKUP_DIR" -name "phonebase_*.sql.gz" -mtime +30 -delete
echo "[$(date)] Бэкап создан: phonebase_$TIMESTAMP.sql.gz"
BACKUP
sed -i "s|/root/phonebase|${PROJECT_DIR}|g" /usr/local/bin/phonebase-backup.sh
chmod +x /usr/local/bin/phonebase-backup.sh

(crontab -l 2>/dev/null | grep -v phonebase-backup; \
 echo "0 4 * * * /usr/local/bin/phonebase-backup.sh >> /var/log/phonebase-backup.log 2>&1") | crontab -
log "Cron-задача бэкапов добавлена (ежедневно 04:00)"

# ── 15. Сборка фронтенда ─────────────────────────────────────
log "Сборка фронтенда..."
cd "$PROJECT_DIR/frontend"
npm ci --silent 2>&1 | tail -1
npm run build 2>&1 | tail -3
cd "$PROJECT_DIR"

# ── 16. Запуск Docker Compose ────────────────────────────────
log "Сборка и запуск контейнеров..."
docker compose up -d --build 2>&1 | tail -10

log "Ожидание готовности PostgreSQL..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U phonebase >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker compose exec -T postgres pg_isready -U phonebase >/dev/null 2>&1 \
    || err "PostgreSQL не запустился за 30 секунд"

log "Ожидание готовности Backend..."
for i in $(seq 1 30); do
    if docker compose exec -T backend curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# ── 17. Создание администратора ──────────────────────────────
log "Создание администратора: ${ADMIN_USER}..."

# Хешируем пароль и вставляем пользователя через Python внутри контейнера
docker compose exec -T backend python3 -c "
import asyncio
from passlib.context import CryptContext
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.business import User

pwd_ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')

async def create_admin():
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(User).where(User.username == '${ADMIN_USER}')
        )).scalar_one_or_none()
        if existing:
            existing.password_hash = pwd_ctx.hash('''${ADMIN_PASS}''')
            existing.role = 'admin'
            existing.store_id = None
            existing.full_name = '${ADMIN_NAME}'
            existing.must_change_password = False
            existing.is_active = True
        else:
            import uuid
            db.add(User(
                id=str(uuid.uuid4()),
                store_id=None,
                username='${ADMIN_USER}',
                full_name='${ADMIN_NAME}',
                role='admin',
                password_hash=pwd_ctx.hash('''${ADMIN_PASS}'''),
                must_change_password=False,
                is_active=True,
            ))
        await db.commit()
        print('OK')

asyncio.run(create_admin())
" 2>&1 | tail -1

# ── 18. Проверка ─────────────────────────────────────────────
echo ""
log "Проверка сервисов..."
echo ""

ALL_OK=true

check_service() {
    local name=$1 cmd=$2
    if eval "$cmd" >/dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}  $name"
    else
        echo -e "  ${RED}FAIL${NC}  $name"
        ALL_OK=false
    fi
}

check_service "PostgreSQL" "docker compose exec -T postgres pg_isready -U phonebase"
check_service "Redis" "docker compose exec -T redis redis-cli -a '$REDIS_PASSWORD' ping"
check_service "Backend" "docker compose exec -T backend curl -sf http://localhost:8000/api/health"
check_service "Nginx HTTPS" "curl -sk https://localhost -H 'Host: $DOMAIN' -o /dev/null -w '%{http_code}' | grep -q 200"
check_service "SSL-сертификат" "test -f $PROJECT_DIR/ssl/fullchain.pem"
check_service "Фаервол UFW" "ufw status | grep -q active"
check_service "Fail2Ban" "systemctl is-active fail2ban"

echo ""

if $ALL_OK; then
    echo -e "${GREEN}${BOLD}============================================${NC}"
    echo -e "${GREEN}${BOLD}   PhoneBase успешно установлен!${NC}"
    echo -e "${GREEN}${BOLD}============================================${NC}"
else
    echo -e "${YELLOW}${BOLD}   Установка завершена с предупреждениями${NC}"
fi

echo ""
echo -e "  ${BOLD}URL:${NC}      https://$DOMAIN"
echo -e "  ${BOLD}Логин:${NC}    $ADMIN_USER"
echo -e "  ${BOLD}Пароль:${NC}   (указан при установке)"
echo ""
echo -e "  ${BOLD}Полезные команды:${NC}"
echo "    docker compose -f $PROJECT_DIR/docker-compose.yml logs -f"
echo "    docker compose -f $PROJECT_DIR/docker-compose.yml ps"
echo "    /usr/local/bin/phonebase-backup.sh"
echo ""
echo -e "  ${BOLD}Файлы:${NC}"
echo "    .env          — секреты (chmod 600)"
echo "    ssl/           — сертификаты"
echo "    INSTALL.md     — подробная документация"
echo ""
echo -e "  ${YELLOW}Не забудьте:${NC}"
echo "    1. Настроить магазины для Авито (Настройки -> каждый магазин)"
echo "    2. Указать URL фида в личном кабинете Авито"
echo "    3. Проверить работу: https://$DOMAIN"
echo ""
echo -e "  ${CYAN}${BOLD}Моблесс Студия, 2026${NC}"
echo ""
