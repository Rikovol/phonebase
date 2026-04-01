import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.business import User
from app.services.import_configured import run_configured_import
from app.services.import_sync import sync_import
from app.services.import_sync_new import sync_import_new

router = APIRouter()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ


@router.post("/upload")
async def upload_import(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Загрузить HTML-выгрузку из 1С.
    Парсит файл, создаёт/обновляет товары.
    Товары, исчезнувшие из файла, помечаются как проданные.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Импорт недоступен для роли «Инфо»")

    if not file.filename or not file.filename.lower().endswith((".html", ".htm")):
        raise HTTPException(status_code=400, detail="Ожидается HTML-файл (.html/.htm)")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Файл превышает 50 МБ")

    log, stores_created = await sync_import(
        db=db,
        html_bytes=data,
        filename=file.filename,
        user_id=current_user.id,
    )

    return {
        "status": log.status,
        "filename": log.filename,
        "items_total": log.items_total,
        "items_created": log.items_created,
        "items_updated": log.items_updated,
        "items_sold": log.items_sold,
        "stores_created": stores_created,
    }


@router.post("/from-configured-url")
async def import_from_configured_url(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Импорт HTML из 1С: сначала локальный файл IMPORT_1C_HTML_PATH, иначе IMPORT_1C_HTML_URL.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Импорт недоступен для роли «Инфо»")
    try:
        result = await run_configured_import(db, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось скачать файл по ссылке: {e!s}",
        ) from e
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Не задано: укажите IMPORT_1C_HTML_PATH или IMPORT_1C_HTML_URL в .env",
        )
    log, stores_created = result
    return {
        "status": log.status,
        "filename": log.filename,
        "items_total": log.items_total,
        "items_created": log.items_created,
        "items_updated": log.items_updated,
        "items_sold": log.items_sold,
        "stores_created": stores_created,
    }


@router.post("/upload-new")
async def upload_import_new(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Загрузить HTML-выгрузку НОВЫХ товаров из 1С.
    Парсит файл, создаёт/обновляет товары с is_new=True.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Импорт недоступен для роли «Инфо»")

    if not file.filename or not file.filename.lower().endswith((".html", ".htm")):
        raise HTTPException(status_code=400, detail="Ожидается HTML-файл (.html/.htm)")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Файл превышает 50 МБ")

    log, stores_created = await sync_import_new(
        db=db,
        html_bytes=data,
        filename=file.filename,
        user_id=current_user.id,
    )

    return {
        "status": log.status,
        "filename": log.filename,
        "items_total": log.items_total,
        "items_created": log.items_created,
        "items_updated": log.items_updated,
        "items_sold": log.items_sold,
        "stores_created": stores_created,
    }


@router.post("/from-configured-url-new")
async def import_from_configured_url_new(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Импорт HTML новых товаров из 1С: IMPORT_1C_NEW_HTML_PATH или IMPORT_1C_NEW_HTML_URL.
    """
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Импорт недоступен для роли «Инфо»")

    from app.core.config import settings
    from app.services.import_remote import fetch_import_html, load_import_html_from_path

    path_cfg = (settings.IMPORT_1C_NEW_HTML_PATH or "").strip()
    url = (settings.IMPORT_1C_NEW_HTML_URL or "").strip()
    if path_cfg:
        try:
            data, filename = load_import_html_from_path(path_cfg)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif url:
        try:
            data, filename = await fetch_import_html(url)
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось скачать файл по ссылке: {e!s}",
            ) from e
    else:
        raise HTTPException(
            status_code=503,
            detail="Не задано: укажите IMPORT_1C_NEW_HTML_PATH или IMPORT_1C_NEW_HTML_URL в .env",
        )

    log, stores_created = await sync_import_new(
        db=db,
        html_bytes=data,
        filename=filename,
        user_id=current_user.id,
    )
    return {
        "status": log.status,
        "filename": log.filename,
        "items_total": log.items_total,
        "items_created": log.items_created,
        "items_updated": log.items_updated,
        "items_sold": log.items_sold,
        "stores_created": stores_created,
    }
