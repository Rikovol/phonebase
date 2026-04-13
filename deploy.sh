#!/usr/bin/env bash
set -euo pipefail

# ─── PhoneBase Deploy Script ─────────────────────────────────────────────────
# Использование:
#   Первый деплой:  ./deploy.sh setup
#   Обновление:     ./deploy.sh update
#   Статус:         ./deploy.sh status
#   Логи:           ./deploy.sh logs [service]
#   Перезапуск:     ./deploy.sh restart

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ── Цвета ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
err()  { echo -e "${RED}[deploy]${NC} $*" >&2; }

# ── Проверки ──────────────────────────────────────────────────────────────────
check_deps() {
    for cmd in docker git node npm; do
        if ! command -v "$cmd" &>/dev/null; then
            err "$cmd не установлен"
            exit 1
        fi
    done
    if ! docker compose version &>/dev/null; then
        err "docker compose не доступен"
        exit 1
    fi
    log "Все зависимости на месте"
}

check_env() {
    if [ ! -f ".env" ]; then
        err "Файл .env не найден. Скопируйте .env.example и заполните."
        exit 1
    fi
    # Проверяем обязательные переменные
    local missing=0
    for var in POSTGRES_PASSWORD REDIS_PASSWORD SECRET_KEY PD_ENCRYPTION_KEY; do
        if ! grep -q "^${var}=" .env; then
            err "Не задана переменная $var в .env"
            missing=1
        fi
    done
    if [ "$missing" -eq 1 ]; then exit 1; fi
    log ".env проверен"
}

# ── Команды ───────────────────────────────────────────────────────────────────
cmd_setup() {
    log "=== Первоначальная установка ==="
    check_deps
    check_env

    # Сборка фронтенда
    log "Сборка фронтенда..."
    cd frontend
    npm ci --silent
    npm run build
    cd ..
    log "Фронтенд собран → frontend/dist/"

    # Запуск Docker
    log "Запуск Docker-контейнеров..."
    docker compose up -d --build

    log "Ожидание готовности БД..."
    sleep 10

    # Проверка здоровья
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        log "=== Backend OK ==="
    else
        warn "Backend ещё запускается, подождите 10-20 секунд"
    fi

    log "=== Установка завершена ==="
    echo ""
    log "Сайт:    https://$(grep '^ALLOWED_HOSTS=' .env | cut -d= -f2 | cut -d, -f1)"
    log "API:     http://localhost:8000/api/docs"
    log "Статус:  ./deploy.sh status"
}

cmd_update() {
    log "=== Обновление проекта ==="
    check_env

    # Текущая версия (до pull)
    local ver_before
    ver_before=$(node -p "require('./frontend/package.json').version" 2>/dev/null || echo "?")

    # Получаем изменения
    if git remote | grep -q origin; then
        log "Получение изменений из git..."
        git pull --ff-only
    else
        warn "Git remote не настроен, пропускаем git pull"
    fi

    # Новая версия (после pull)
    local ver_after
    ver_after=$(node -p "require('./frontend/package.json').version" 2>/dev/null || echo "?")

    if [ "$ver_before" = "$ver_after" ]; then
        log "Версия: v${ver_before} (без изменений)"
    else
        log "Обновление версии: v${ver_before} → v${ver_after}"
    fi

    # Пересборка фронтенда
    log "Пересборка фронтенда..."
    cd frontend
    npm ci --silent
    npm run build
    cd ..

    # Пересборка и перезапуск контейнеров
    log "Пересборка Docker-контейнеров..."
    if [ "${2:-}" = "--no-cache" ]; then
        log "Полная пересборка (--no-cache)..."
        docker compose build --no-cache
    else
        docker compose build
    fi
    docker compose up -d --force-recreate --remove-orphans

    log "Ожидание готовности..."
    sleep 10

    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        log "=== Обновление завершено, backend OK ==="
    else
        warn "Backend ещё перезапускается..."
    fi
}

cmd_status() {
    echo ""
    docker compose ps
    echo ""
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        log "API: OK"
    else
        err "API: недоступен"
    fi
}

cmd_logs() {
    local service="${1:-}"
    if [ -n "$service" ]; then
        docker compose logs -f --tail=100 "$service"
    else
        docker compose logs -f --tail=50
    fi
}

cmd_restart() {
    log "Перезапуск контейнеров..."
    docker compose down
    docker compose up -d
    log "Готово"
}

cmd_backup() {
    local backup_dir="backups/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    log "Бэкап БД → $backup_dir/db.sql.gz"
    docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-phonebase}" "${POSTGRES_DB:-phonebase}" | gzip > "$backup_dir/db.sql.gz"
    log "Бэкап завершён: $(du -sh "$backup_dir" | cut -f1)"
}

# ── Точка входа ───────────────────────────────────────────────────────────────
case "${1:-help}" in
    setup)   cmd_setup ;;
    update)  cmd_update ;;
    status)  cmd_status ;;
    logs)    cmd_logs "${2:-}" ;;
    restart) cmd_restart ;;
    backup)  cmd_backup ;;
    *)
        echo "PhoneBase Deploy"
        echo ""
        echo "Использование: ./deploy.sh <команда>"
        echo ""
        echo "Команды:"
        echo "  setup     Первоначальная установка (Docker + фронтенд)"
        echo "  update    Обновить (git pull + пересборка)"
        echo "  status    Статус контейнеров"
        echo "  logs      Логи (опционально: logs backend)"
        echo "  restart   Перезапустить контейнеры"
        echo "  backup    Бэкап базы данных"
        ;;
esac
