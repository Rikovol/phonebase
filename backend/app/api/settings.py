"""
Системные настройки: ключи конфигурации хранятся в БД.
Доступ — только admin.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core.database import get_db
from app.models.business import SystemSetting, User

router = APIRouter()

ALLOWED_KEYS = {
    "import_1c_url",
    "import_1c_new_url",
}


async def get_setting(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SystemSetting, key)
    return row.value if row else None


async def set_setting(db: AsyncSession, key: str, value: str | None) -> None:
    row = await db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    await db.commit()


class SettingsOut(BaseModel):
    import_1c_url: str | None = None
    import_1c_new_url: str | None = None


class SettingsIn(BaseModel):
    import_1c_url: str | None = None
    import_1c_new_url: str | None = None


@router.get("/", response_model=SettingsOut)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    rows = (await db.execute(select(SystemSetting))).scalars().all()
    data = {r.key: r.value for r in rows}
    return SettingsOut(
        import_1c_url=data.get("import_1c_url"),
        import_1c_new_url=data.get("import_1c_new_url"),
    )


@router.patch("/", response_model=SettingsOut)
async def update_settings(
    body: SettingsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    for key in ALLOWED_KEYS:
        val = getattr(body, key, None)
        if val is not None:
            val = val.strip() or None
        await set_setting(db, key, val)

    rows = (await db.execute(select(SystemSetting))).scalars().all()
    data = {r.key: r.value for r in rows}
    return SettingsOut(
        import_1c_url=data.get("import_1c_url"),
        import_1c_new_url=data.get("import_1c_new_url"),
    )
