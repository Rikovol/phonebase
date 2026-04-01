"""Первичное заполнение SQLite: магазины и пользователи (пароль «temp», смена при входе)."""

from passlib.context import CryptContext
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.business import Store, User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

STORES = [
    ("11111111-0001-0001-0001-000000000001", "iPrice.Store"),
    ("11111111-0002-0002-0002-000000000002", "МОБИЛАКС"),
    ("11111111-0003-0003-0003-000000000003", "REM-GSM"),
    ("11111111-0004-0004-0004-000000000004", "ДИСКИ"),
    ("11111111-0005-0005-0005-000000000005", "ТЕХНО"),
]

# id, store_id, username, full_name, role (admin | staff | info) — совпадает с docker/seed-users.sql
USERS = [
    ("22222222-0001-0001-0001-000000000001", "11111111-0001-0001-0001-000000000001", "roman", "Роман", "staff"),
    ("22222222-0002-0002-0002-000000000002", "11111111-0001-0001-0001-000000000001", "alisa", "Алиса", "staff"),
    ("22222222-0003-0003-0003-000000000003", "11111111-0001-0001-0001-000000000001", "egor", "Егор", "staff"),
    ("22222222-0004-0004-0004-000000000004", None, "anton", "Антон", "admin"),
    ("22222222-0005-0005-0005-000000000005", "11111111-0002-0002-0002-000000000002", "dmitry", "Дмитрий", "staff"),
    ("22222222-0006-0006-0006-000000000006", "11111111-0003-0003-0003-000000000003", "artem", "Артём", "staff"),
    ("22222222-0007-0007-0007-000000000007", "11111111-0003-0003-0003-000000000003", "ivan", "Иван", "staff"),
    ("22222222-0008-0008-0008-000000000008", "11111111-0003-0003-0003-000000000003", "alexey", "Алексей", "staff"),
    ("22222222-0009-0009-0009-000000000009", "11111111-0003-0003-0003-000000000003", "lena", "Лена", "staff"),
    ("22222222-0010-0010-0010-000000000010", None, "pavel", "Павел", "admin"),
    ("22222222-0011-0011-0011-000000000011", None, "vitaliy", "Виталий", "admin"),
    ("22222222-0012-0012-0012-000000000012", None, "ilya", "Илья", "admin"),
    ("22222222-0013-0013-0013-000000000013", "11111111-0004-0004-0004-000000000004", "alexander", "Александр", "staff"),
    ("22222222-0014-0014-0014-000000000014", "11111111-0005-0005-0005-000000000005", "balashkov", "Балашков", "staff"),
    ("22222222-0015-0015-0015-000000000015", None, "site", "Сайт", "info"),
]


async def seed_if_empty() -> None:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Store).limit(1))
        if r.scalar_one_or_none() is not None:
            return

        for sid, name in STORES:
            db.add(Store(id=sid, name=name, city=None, address=None, is_active=True))

        temp_hash = pwd_ctx.hash("temp")
        for uid, store_id, username, full_name, role in USERS:
            db.add(
                User(
                    id=uid,
                    store_id=store_id,
                    username=username,
                    full_name=full_name,
                    role=role,
                    password_hash=temp_hash,
                    must_change_password=True,
                    is_active=True,
                )
            )
        await db.commit()
