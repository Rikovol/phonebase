-- Однократно для уже развёрнутого PostgreSQL: убрать роль manager из CHECK и добавить info.
-- Сначала приложение при старте переведёт role='manager' → 'staff' (migrate_legacy_role_manager_to_staff).
-- Затем выполните этот скрипт под суперпользователем БД (при необходимости замените business на вашу схему).

ALTER TABLE business.users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE business.users ADD CONSTRAINT users_role_check CHECK (role IN ('admin','staff','info'));
