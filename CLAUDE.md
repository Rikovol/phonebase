# PhoneBase

CRM для сети магазинов б/у телефонов. Django-подобный backend (FastAPI), React SPA frontend, PostgreSQL, Docker.

## Stack
- Backend: Python 3.12, FastAPI, SQLAlchemy async, asyncpg, Celery + Redis
- Frontend: React 18 + Vite (single App.jsx SPA)
- DB: PostgreSQL 16
- Deploy: Docker Compose, nginx, Let's Encrypt

## Model Routing

Экономия токенов через распределение задач по моделям:

| Задача | Модель | Когда |
|--------|--------|-------|
| Архитектура, план, ревью, сложный дебаг | **Opus** | Основной контекст |
| Написание кода, рефакторинг, тесты | **Sonnet** | Субагенты с `model: "sonnet"` |
| Поиск по кодовой базе, разведка, grep | **Haiku** | Субагенты с `model: "haiku"` |

При запуске субагентов:
- Для кодогенерации: `Agent({ model: "sonnet", ... })`
- Для поиска/исследования: `Agent({ model: "haiku", subagent_type: "Explore", ... })`
- Для ревью: оставлять на Opus (основной контекст) или `Agent({ subagent_type: "code-reviewer", ... })`

## Auto-Review после изменений

После завершения блока кодовых изменений (фича, фикс, рефакторинг) — **перед коммитом** — запускать два ревью параллельно:

1. **Codex review** — в фоне:
   ```
   Bash({ command: 'node ".claude/plugins/cache/openai-codex/codex/1.0.2/scripts/codex-companion.mjs" review ""', run_in_background: true })
   ```

2. **Qwen review** — в фоне:
   ```
   Bash({ command: 'export DASHSCOPE_API_KEY=$(grep DASHSCOPE_API_KEY ~/.env | cut -d= -f2) && node .claude/scripts/qwen-companion.mjs task --context "$(git diff --cached || git diff)" "Ревью изменений: найди баги, проблемы безопасности, нарушения паттернов проекта. Кратко."', run_in_background: true })
   ```

После получения результатов:
- Показать оба ревью пользователю
- Если найдены критичные проблемы — исправить перед коммитом
- Если замечания косметические — на усмотрение пользователя

## Conventions
- Язык общения: русский
- Версионирование: bump patch в `frontend/package.json` + `backend/app/main.py`
- Коммит: спрашивать перед коммитом, формат `<type>: <description> (v1.x.x)`
- БД: только PostgreSQL, никогда SQLite
- Секреты: .env (в .gitignore), .env.example для документации
