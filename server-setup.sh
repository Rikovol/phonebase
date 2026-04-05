#!/usr/bin/env bash
# ─── PhoneBase Server Setup ───────────────────────────────────────────────────
# Идемпотентный скрипт разворачивания на чистом Ubuntu 22.04 / 24.04
#
# Использование:
#   git clone https://github.com/Rikovol/phonebase.git /opt/phonebase
#   sudo bash /opt/phonebase/server-setup.sh
#
# При повторном запуске пропускает выполненные шаги (проверяет /var/lib/phonebase/.setup_state)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Директория самого скрипта — используем как INSTALL_DIR если запущен из проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Цвета ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[setup]${NC} $*"; }
err()  { echo -e "${RED}[setup]${NC} $*" >&2; }
step() { echo -e "\n${CYAN}══ $* ══${NC}"; }

# ── Состояние шагов ───────────────────────────────────────────────────────────
STATE_DIR="/var/lib/phonebase"
STATE_FILE="$STATE_DIR/.setup_state"

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

done_step() { grep -q "^$1$" "$STATE_FILE" 2>/dev/null; }
mark_done() { echo "$1" >> "$STATE_FILE"; log "✓ Шаг '$1' завершён"; }
skip_if_done() {
    if done_step "$1"; then
        warn "↷ Пропускаем '$1' (уже выполнен)"
        return 0
    fi
    return 1
}

# ── Проверка root ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "Запустите скрипт от root: sudo bash server-setup.sh"
    exit 1
fi

# ── Конфигурация ──────────────────────────────────────────────────────────────
CONFIG_FILE="$STATE_DIR/.config"

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        source "$CONFIG_FILE"
    fi
}

save_config() {
    cat > "$CONFIG_FILE" <<EOF
DOMAIN="${DOMAIN}"
EMAIL="${EMAIL}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD}"
REDIS_PASSWORD="${REDIS_PASSWORD}"
SECRET_KEY="${SECRET_KEY}"
PD_ENCRYPTION_KEY="${PD_ENCRYPTION_KEY}"
GITHUB_REPO="${GITHUB_REPO}"
INSTALL_DIR="${INSTALL_DIR}"
EOF
    chmod 600 "$CONFIG_FILE"
}

ask_questions() {
    step "Настройка параметров установки"
    load_config

    # Домен
    if [ -z "${DOMAIN:-}" ]; then
        read -rp "Домен сайта (например: phonebase.ru): " DOMAIN
        DOMAIN="${DOMAIN// /}"
    else
        log "Домен: $DOMAIN"
    fi

    # Email для Let's Encrypt
    if [ -z "${EMAIL:-}" ]; then
        read -rp "Email для SSL-сертификата (Let's Encrypt): " EMAIL
    else
        log "Email: $EMAIL"
    fi

    # Пароль PostgreSQL
    if [ -z "${POSTGRES_PASSWORD:-}" ]; then
        POSTGRES_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | head -c 40)"
        log "PostgreSQL пароль сгенерирован автоматически"
    fi

    # Пароль Redis
    if [ -z "${REDIS_PASSWORD:-}" ]; then
        REDIS_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | head -c 40)"
        log "Redis пароль сгенерирован автоматически"
    fi

    # SECRET_KEY
    if [ -z "${SECRET_KEY:-}" ]; then
        SECRET_KEY="$(openssl rand -base64 48 | tr -d '/+=')"
        log "SECRET_KEY сгенерирован автоматически"
    fi

    # PD_ENCRYPTION_KEY — валидный Fernet-ключ: 32 байта в URL-safe base64 (44 символа)
    if [ -z "${PD_ENCRYPTION_KEY:-}" ]; then
        PD_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
        log "PD_ENCRYPTION_KEY сгенерирован автоматически"
    fi

    # GitHub репозиторий
    if [ -z "${GITHUB_REPO:-}" ]; then
        GITHUB_REPO="https://github.com/Rikovol/phonebase.git"
        log "GitHub репозиторий: $GITHUB_REPO"
    fi

    # Директория установки — если скрипт запущен из клонированного репо, используем его
    if [ -z "${INSTALL_DIR:-}" ]; then
        if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
            INSTALL_DIR="$SCRIPT_DIR"
            log "Используем директорию скрипта: $INSTALL_DIR"
        else
            INSTALL_DIR="/opt/phonebase"
        fi
    fi

    save_config
    log "Конфигурация сохранена в $CONFIG_FILE"
}

# ── Шаг 1: Системные пакеты ───────────────────────────────────────────────────
step_system_packages() {
    skip_if_done "system_packages" && return
    step "Установка системных пакетов"

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -yq \
        curl wget git vim \
        apt-transport-https ca-certificates gnupg lsb-release \
        openssl net-tools ufw \
        certbot python3-certbot-nginx \
        nodejs npm

    mark_done "system_packages"
}

# ── Шаг 2: Docker ─────────────────────────────────────────────────────────────
step_docker() {
    skip_if_done "docker" && return
    step "Установка Docker"

    if command -v docker &>/dev/null; then
        log "Docker уже установлен: $(docker --version)"
    else
        curl -fsSL https://get.docker.com | sh
    fi

    # Docker Compose plugin
    if ! docker compose version &>/dev/null 2>&1; then
        apt-get install -yq docker-compose-plugin
    fi

    systemctl enable docker
    systemctl start docker

    mark_done "docker"
}

# ── Шаг 2б: Освобождение портов 80/443 ───────────────────────────────────────
step_free_ports() {
    skip_if_done "free_ports" && return
    step "Освобождение портов 80 и 443"

    for svc in nginx apache2 lighttpd caddy; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            log "Останавливаем $svc (занимает порт 80/443)..."
            systemctl stop "$svc"
            systemctl disable "$svc"
        fi
    done

    # Проверяем что порты свободны
    if ss -tlnp 2>/dev/null | grep -q ':80 '; then
        warn "Порт 80 всё ещё занят: $(ss -tlnp | grep ':80 ')"
    else
        log "Порт 80 свободен"
    fi

    mark_done "free_ports"
}

# ── Шаг 3: Клонирование репозитория ──────────────────────────────────────────
step_clone() {
    skip_if_done "clone" && return
    load_config

    # Если INSTALL_DIR уже содержит проект — клонирование не нужно
    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        log "Репозиторий уже на месте: $INSTALL_DIR"
        mark_done "clone"
        return
    fi

    step "Клонирование репозитория"
    if [ -d "$INSTALL_DIR/.git" ]; then
        log "Репозиторий уже существует, обновляем..."
        cd "$INSTALL_DIR"
        git pull --ff-only
    else
        git clone "$GITHUB_REPO" "$INSTALL_DIR"
    fi

    mark_done "clone"
}

# ── Шаг 4: Генерация .env ─────────────────────────────────────────────────────
step_env() {
    skip_if_done "env" && return
    step "Генерация .env файла"
    load_config

    cat > "$INSTALL_DIR/.env" <<EOF
# ─── Сгенерировано server-setup.sh $(date '+%Y-%m-%d %H:%M:%S') ───────────────

# База данных
POSTGRES_USER=phonebase
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=phonebase
DATABASE_URL=postgresql+asyncpg://phonebase:${POSTGRES_PASSWORD}@postgres:5432/phonebase

# Redis
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0

# Безопасность
SECRET_KEY=${SECRET_KEY}
PD_ENCRYPTION_KEY=${PD_ENCRYPTION_KEY}

# Домен и URL
DOMAIN=${DOMAIN}
ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN}
PUBLIC_URL=https://${DOMAIN}
CORS_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}

# Настройки
DEBUG=false
ENVIRONMENT=production
EOF

    chmod 600 "$INSTALL_DIR/.env"
    log ".env создан: $INSTALL_DIR/.env"
    mark_done "env"
}

# ── Шаг 5: Сборка фронтенда ───────────────────────────────────────────────────
step_frontend() {
    skip_if_done "frontend" && return
    step "Сборка фронтенда"
    load_config

    cd "$INSTALL_DIR/frontend"
    npm ci --silent
    npm run build
    log "Фронтенд собран → frontend/dist/"

    mark_done "frontend"
}

# ── Шаг 6: Запуск Docker-контейнеров ─────────────────────────────────────────
step_docker_up() {
    skip_if_done "docker_up" && return
    step "Запуск Docker-контейнеров"
    load_config

    cd "$INSTALL_DIR"
    docker compose up -d --build

    log "Ожидание готовности БД (15 секунд)..."
    sleep 15

    mark_done "docker_up"
}

# ── Шаг 7: Получение SSL-сертификата ──────────────────────────────────────────
step_ssl() {
    skip_if_done "ssl" && return
    step "Получение SSL-сертификата (Let's Encrypt)"
    load_config

    # Проверяем, что домен указывает на этот сервер
    SERVER_IP="$(curl -4 -sf https://ifconfig.me || curl -4 -sf https://api.ipify.org || echo 'unknown')"
    DOMAIN_IP="$(dig +short "$DOMAIN" A 2>/dev/null | tail -1 || nslookup "$DOMAIN" 2>/dev/null | awk '/^Address: / { print $2 }' | tail -1 || echo 'unknown')"

    if [ "$SERVER_IP" != "$DOMAIN_IP" ] && [ "$DOMAIN_IP" != "unknown" ]; then
        warn "IP сервера ($SERVER_IP) не совпадает с DNS домена ($DOMAIN → $DOMAIN_IP)"
        warn "Убедитесь, что A-запись домена указывает на этот сервер"
        read -rp "Продолжить получение сертификата? [y/N]: " CONFIRM
        if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
            warn "SSL пропущен. Запустите скрипт снова после настройки DNS"
            return
        fi
    fi

    # Временно останавливаем nginx для получения сертификата
    docker compose -f "$INSTALL_DIR/docker-compose.yml" stop nginx 2>/dev/null || true

    certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        -d "$DOMAIN" \
        -d "www.$DOMAIN" \
        || certbot certonly \
            --standalone \
            --non-interactive \
            --agree-tos \
            --email "$EMAIL" \
            -d "$DOMAIN"

    # Копируем сертификаты в директорию проекта (имена должны совпадать с nginx.conf)
    mkdir -p "$INSTALL_DIR/ssl"
    cp /etc/letsencrypt/live/"$DOMAIN"/fullchain.pem "$INSTALL_DIR/ssl/fullchain.pem"
    cp /etc/letsencrypt/live/"$DOMAIN"/privkey.pem "$INSTALL_DIR/ssl/privkey.pem"
    chmod 600 "$INSTALL_DIR/ssl/privkey.pem"

    # Перезапускаем nginx
    cd "$INSTALL_DIR"
    docker compose start nginx 2>/dev/null || docker compose up -d nginx

    log "SSL-сертификат получен для $DOMAIN"
    mark_done "ssl"
}

# ── Шаг 8: Автообновление SSL ─────────────────────────────────────────────────
step_ssl_renew() {
    skip_if_done "ssl_renew" && return
    step "Настройка автообновления SSL"
    load_config

    # Cron для обновления сертификата
    RENEW_SCRIPT="/usr/local/bin/phonebase-ssl-renew.sh"
    cat > "$RENEW_SCRIPT" <<RENEW_EOF
#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR}"
DOMAIN="${DOMAIN}"

docker compose -f "\$INSTALL_DIR/docker-compose.yml" stop nginx 2>/dev/null || true

certbot renew --quiet --non-interactive

if [ -f "/etc/letsencrypt/live/\$DOMAIN/fullchain.pem" ]; then
    cp /etc/letsencrypt/live/"\$DOMAIN"/fullchain.pem "\$INSTALL_DIR/ssl/fullchain.pem"
    cp /etc/letsencrypt/live/"\$DOMAIN"/privkey.pem "\$INSTALL_DIR/ssl/privkey.pem"
    chmod 600 "\$INSTALL_DIR/ssl/privkey.pem"
fi

cd "\$INSTALL_DIR"
docker compose up -d nginx
RENEW_EOF
    chmod +x "$RENEW_SCRIPT"

    # Добавляем в cron (каждый понедельник в 04:00)
    existing_cron="$(crontab -l 2>/dev/null || true)"
    filtered_cron="$(echo "$existing_cron" | grep -v "phonebase-ssl-renew" || true)"
    printf '%s\n%s\n' "$filtered_cron" "0 4 * * 1 $RENEW_SCRIPT >> /var/log/phonebase-ssl-renew.log 2>&1" | crontab -

    log "Автообновление SSL настроено (каждый понедельник 04:00)"
    mark_done "ssl_renew"
}

# ── Шаг 9: Настройка UFW (файрвол) ───────────────────────────────────────────
step_firewall() {
    skip_if_done "firewall" && return
    step "Настройка файрвола UFW"

    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp    # SSH
    ufw allow 80/tcp    # HTTP
    ufw allow 443/tcp   # HTTPS
    ufw --force enable

    log "Файрвол настроен: открыты порты 22, 80, 443"
    mark_done "firewall"
}

# ── Шаг 10: Глобальная команда basestock ─────────────────────────────────────
step_cli() {
    skip_if_done "cli" && return
    step "Установка команды basestock"
    load_config

    cat > /usr/local/bin/basestock <<CLI_EOF
#!/usr/bin/env bash
exec "${INSTALL_DIR}/deploy.sh" "\$@"
CLI_EOF
    chmod +x /usr/local/bin/basestock

    log "Команда 'basestock' доступна глобально"
    log "  basestock status | logs | restart | update | backup"
    mark_done "cli"
}

# ── Шаг 11: Systemd-сервис для автозапуска ───────────────────────────────────
step_systemd() {
    skip_if_done "systemd" && return
    step "Настройка автозапуска (systemd)"
    load_config

    cat > /etc/systemd/system/phonebase.service <<SERVICE_EOF
[Unit]
Description=PhoneBase Application
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    systemctl daemon-reload
    systemctl enable phonebase

    log "Systemd-сервис phonebase создан и включён"
    mark_done "systemd"
}

# ── Шаг 11: Проверка работоспособности ───────────────────────────────────────
step_healthcheck() {
    step "Проверка работоспособности"
    load_config

    log "Ожидание 10 секунд..."
    sleep 10

    local ok=true

    # Docker
    cd "$INSTALL_DIR"
    RUNNING=$(docker compose ps --filter "status=running" --format "{{.Name}}" 2>/dev/null | wc -l)
    log "Запущено контейнеров: $RUNNING"

    # API
    if curl -sf "http://localhost:8000/api/health" > /dev/null 2>&1; then
        log "✓ Backend API: OK"
    else
        warn "✗ Backend API недоступен на :8000 (возможно ещё запускается)"
        ok=false
    fi

    # HTTPS
    if curl -sf "https://$DOMAIN/api/health" > /dev/null 2>&1; then
        log "✓ HTTPS: OK — https://$DOMAIN"
    else
        warn "✗ HTTPS недоступен (проверьте SSL и DNS)"
        ok=false
    fi

    if $ok; then
        log "✅ Все проверки пройдены"
    else
        warn "⚠ Некоторые проверки не прошли. Логи: cd $INSTALL_DIR && docker compose logs -f"
    fi
}

# ── Вывод итоговой информации ──────────────────────────────────────────────────
show_summary() {
    load_config
    echo ""
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo -e "${GREEN}  PhoneBase успешно установлен!${NC}"
    echo -e "${GREEN}════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Сайт:     ${CYAN}https://${DOMAIN}${NC}"
    echo -e "  API Docs: ${CYAN}https://${DOMAIN}/api/docs${NC}"
    echo ""
    echo -e "  Директория: ${INSTALL_DIR}"
    echo -e "  Логи:       cd ${INSTALL_DIR} && docker compose logs -f"
    echo -e "  Обновление: cd ${INSTALL_DIR} && ./deploy.sh update"
    echo -e "  Бэкап БД:   cd ${INSTALL_DIR} && ./deploy.sh backup"
    echo ""
    echo -e "  Пароли сохранены в: ${STATE_DIR}/.config (chmod 600)"
    echo ""
}

# ── Главный поток ──────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║     PhoneBase Server Setup v1.0       ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════╝${NC}"
    echo ""

    # Загружаем сохранённую конфигурацию или спрашиваем
    load_config
    if [ -z "${DOMAIN:-}" ] || [ -z "${EMAIL:-}" ]; then
        ask_questions
    else
        log "Используется сохранённая конфигурация:"
        log "  Домен: $DOMAIN"
        log "  Email: $EMAIL"
        log "  Директория: $INSTALL_DIR"
        echo ""
        read -rp "Продолжить с этими настройками? [Y/n]: " CONFIRM
        if [[ "$CONFIRM" =~ ^[Nn]$ ]]; then
            # Сбрасываем сохранённые значения чтобы перезадать
            DOMAIN="" EMAIL="" POSTGRES_PASSWORD="" REDIS_PASSWORD="" SECRET_KEY="" PD_ENCRYPTION_KEY=""
            ask_questions
        fi
    fi

    step_system_packages
    step_docker
    step_free_ports
    step_clone
    step_env
    step_frontend
    step_docker_up
    step_ssl
    step_ssl_renew
    step_firewall
    step_cli
    step_systemd
    step_healthcheck
    show_summary
}

main "$@"
