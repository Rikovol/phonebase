"""Агрегированные логи для администратора. Хранение — 1 год."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.business import ImportLog, StaffActionLog, Store, User, DocAccessLog, Product
from app.models.personal_data import PDAccessLog

LOG_RETENTION_DAYS = 365

router = APIRouter()


async def cleanup_old_logs(db: AsyncSession):
    """Удаляет логи старше LOG_RETENTION_DAYS (1 год)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
    await db.execute(delete(ImportLog).where(ImportLog.started_at < cutoff))
    await db.execute(delete(DocAccessLog).where(DocAccessLog.created_at < cutoff))
    await db.execute(delete(StaffActionLog).where(StaffActionLog.created_at < cutoff))
    await db.commit()


@router.get("/activity")
async def get_activity_logs(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    log_type: str = Query("all"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")

    await cleanup_old_logs(db)

    results = []

    # Import logs
    if log_type in ("all", "import"):
        q = (
            select(
                ImportLog.id,
                ImportLog.filename,
                ImportLog.status,
                ImportLog.items_total,
                ImportLog.items_created,
                ImportLog.items_updated,
                ImportLog.items_sold,
                ImportLog.error_message,
                ImportLog.started_at,
                ImportLog.finished_at,
                User.username.label("user"),
                User.full_name.label("user_name"),
                Store.name.label("store_name"),
            )
            .outerjoin(User, ImportLog.imported_by == User.id)
            .outerjoin(Store, ImportLog.store_id == Store.id)
            .order_by(desc(ImportLog.started_at))
            .limit(limit)
        )
        rows = (await db.execute(q)).all()
        for r in rows:
            results.append({
                "type": "import",
                "id": r.id,
                "timestamp": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "user": r.user or "система",
                "user_name": r.user_name,
                "store": None,
                "status": r.status,
                "details": f"{r.filename}: всего {r.items_total}, создано {r.items_created}, обновлено {r.items_updated}, продано {r.items_sold}",
                "error": r.error_message,
            })

    # Document access logs
    if log_type in ("all", "docs"):
        q = (
            select(
                DocAccessLog.id,
                DocAccessLog.action,
                DocAccessLog.created_at,
                User.username.label("user"),
                User.full_name.label("user_name"),
            )
            .outerjoin(User, DocAccessLog.user_id == User.id)
            .order_by(desc(DocAccessLog.created_at))
            .limit(limit)
        )
        rows = (await db.execute(q)).all()
        for r in rows:
            results.append({
                "type": "doc_access",
                "id": r.id,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
                "user": r.user or "—",
                "user_name": r.user_name,
                "store": None,
                "status": "ok",
                "details": f"Документ: {r.action}",
                "error": None,
            })

    # Staff action logs
    if log_type in ("all", "staff"):
        q = (
            select(
                StaffActionLog.id,
                StaffActionLog.action,
                StaffActionLog.target_id,
                StaffActionLog.details,
                StaffActionLog.store_name,
                StaffActionLog.created_at,
                User.username.label("user"),
                User.full_name.label("user_name"),
            )
            .outerjoin(User, StaffActionLog.user_id == User.id)
            .order_by(desc(StaffActionLog.created_at))
            .limit(limit)
        )
        rows = (await db.execute(q)).all()
        action_labels = {
            "login": "Вход", "product_edit": "Редактирование",
            "photo_upload": "Загрузка фото", "photo_delete": "Удаление фото",
            "avito_toggle": "Авито",
        }
        for r in rows:
            results.append({
                "type": "staff",
                "id": r.id,
                "timestamp": r.created_at.isoformat() if r.created_at else None,
                "user": r.user or "—",
                "user_name": r.user_name,
                "store": r.store_name,
                "status": action_labels.get(r.action, r.action),
                "details": r.details or "",
                "error": None,
            })

    # Avito sync — check recent product updates with avito fields
    if log_type in ("all", "avito"):
        q = (
            select(
                Product.id,
                Product.model,
                Product.store_id,
                Product.avito_published,
                Product.avito_item_id,
                Product.updated_at,
                Store.name.label("store_name"),
            )
            .outerjoin(Store, Product.store_id == Store.id)
            .where(Product.avito_item_id.isnot(None))
            .order_by(desc(Product.updated_at))
            .limit(limit)
        )
        try:
            rows = (await db.execute(q)).all()
            for r in rows:
                results.append({
                    "type": "avito",
                    "id": r.id,
                    "timestamp": r.updated_at.isoformat() if r.updated_at else None,
                    "user": "avito_sync",
                    "user_name": "Авито",
                    "store": r.store_name,
                    "status": "published" if r.avito_published else "unpublished",
                    "details": f"{r.model} — item_id: {r.avito_item_id}",
                    "error": None,
                })
        except Exception:
            pass

    # Sort all by timestamp descending
    results.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return {"items": results[:limit], "total": len(results)}
