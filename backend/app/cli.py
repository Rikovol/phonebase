"""
python -m app.cli init_users

Перегенерирует временные пароли для пользователей с маркером CHANGE_ON_FIRST_RUN
(режим PostgreSQL + seed-users.sql). При локальном SQLite с готовым bcrypt см. seed.py.
"""
import asyncio
import secrets
import sys
from passlib.context import CryptContext
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.business import Store, User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = [
    ("roman", "Роман", "staff", "iPrice.Store"),
    ("alisa", "Алиса", "staff", "iPrice.Store"),
    ("egor", "Егор", "staff", "iPrice.Store"),
    ("anton", "Антон", "admin", None),
    ("dmitry", "Дмитрий", "staff", "МОБИЛАКС"),
    ("artem", "Артём", "staff", "REM-GSM"),
    ("ivan", "Иван", "staff", "REM-GSM"),
    ("alexey", "Алексей", "staff", "REM-GSM"),
    ("lena", "Лена", "staff", "REM-GSM"),
    ("alexander", "Александр", "staff", "ДИСКИ"),
    ("balashkov", "Балашков", "staff", "ТЕХНО"),
    ("pavel", "Павел", "admin", None),
    ("vitaliy", "Виталий", "admin", None),
    ("ilya", "Илья", "admin", None),
    ("site", "Сайт", "info", None),
]


async def init_users():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Store))
        store_map = {row.name: row.id for row in res.scalars().all()}

        credentials = []
        for username, full_name, role, store_name in USERS:
            r = await db.execute(select(User).where(User.username == username))
            existing = r.scalar_one_or_none()
            if existing and existing.password_hash != "CHANGE_ON_FIRST_RUN":
                print(f"  ⏭  {username} — пропускаем (пароль уже установлен)")
                continue

            temp_pwd = secrets.token_urlsafe(10)
            pwd_hash = pwd_ctx.hash(temp_pwd)
            store_id = None if role in ("admin", "info") else (store_map.get(store_name) if store_name else None)

            if existing:
                existing.password_hash = pwd_hash
                existing.must_change_password = True
                existing.store_id = store_id
                existing.role = role
                existing.full_name = full_name
            else:
                db.add(
                    User(
                        store_id=store_id,
                        username=username,
                        full_name=full_name,
                        role=role,
                        password_hash=pwd_hash,
                        must_change_password=True,
                        is_active=True,
                    )
                )

            credentials.append((username, full_name, role, store_name or "все магазины", temp_pwd))

        await db.commit()

    if not credentials:
        print("\nВсе пользователи уже инициализированы.")
        return

    print("\n" + "=" * 72)
    print("  ВРЕМЕННЫЕ ПАРОЛИ — распечатать и раздать лично, затем УНИЧТОЖИТЬ")
    print("=" * 72)
    print(f"  {'Логин':<12} {'Имя':<12} {'Роль':<8} {'Магазин':<18} {'Пароль'}")
    print("-" * 72)
    for username, full_name, role, store, pwd in credentials:
        print(f"  {username:<12} {full_name:<12} {role:<8} {store:<18} {pwd}")
    print("=" * 72)
    print("  Сотрудник должен сменить пароль при первом входе.")
    print("  Этот вывод нигде не сохраняется автоматически.")
    print("=" * 72 + "\n")


async def run_cleanup():
    from app.services.cleanup import cleanup_sold_products

    result = await cleanup_sold_products()
    print(f"\nОчистка проданных товаров:")
    print(f"  Товаров удалено:    {result['products_deleted']}")
    print(f"  Фото удалено:       {result['photos_deleted']}")
    print(f"  Документов удалено: {result['docs_deleted']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init_users":
        asyncio.run(init_users())
    elif cmd == "cleanup":
        asyncio.run(run_cleanup())
    else:
        print("Использование:")
        print("  python -m app.cli init_users   — сгенерировать временные пароли")
        print("  python -m app.cli cleanup      — удалить данные проданных товаров (старше 1 года)")
