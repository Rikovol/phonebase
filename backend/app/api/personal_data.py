"""
API для работы с персональными данными клиентов (152-ФЗ).

Каждый доступ к ПД логируется в personal_data.access_log.
Только роль 'admin' имеет доступ к этим эндпоинтам.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional
import uuid

from app.core.database import get_db
from app.core.security import require_admin
from app.services.pd_encryption import pd_crypto
from app.models.personal_data import ClientRecord, PDAccessLog

router = APIRouter()


# ── Схемы запросов/ответов ────────────────────────────────────────────────────

class CreateClientRecordRequest(BaseModel):
    full_name: str
    passport_series: Optional[str] = None
    passport_number: Optional[str] = None
    issued_by: Optional[str] = None
    issued_date: Optional[str] = None
    consent_obtained_at: datetime
    reason: str                            # основание доступа (обязательно)


class ClientRecordResponse(BaseModel):
    id: uuid.UUID
    full_name: str                         # расшифровывается только при запросе
    passport_series: Optional[str]
    passport_number: Optional[str]
    issued_by: Optional[str]
    consent_obtained_at: datetime
    has_doc_file: bool
    created_at: datetime


# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/records", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_client_record(
    request: Request,
    data: CreateClientRecordRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),   # только admin
):
    """Создать запись ПД клиента. Все данные шифруются перед сохранением."""
    record = ClientRecord(
        full_name_enc=pd_crypto.encrypt(data.full_name),
        passport_series_enc=pd_crypto.encrypt(data.passport_series or ""),
        passport_number_enc=pd_crypto.encrypt(data.passport_number or ""),
        issued_by_enc=pd_crypto.encrypt(data.issued_by or ""),
        issued_date_enc=pd_crypto.encrypt(data.issued_date or ""),
        consent_obtained_at=data.consent_obtained_at,
        created_by_user_id=current_user.id,
    )
    db.add(record)
    await db.flush()

    # Логируем создание
    await _log_access(db, str(record.id), str(current_user.id), "create",
                      request, data.reason)
    await db.commit()

    return {"id": str(record.id), "status": "created"}


@router.get("/records/{record_id}", response_model=ClientRecordResponse)
async def get_client_record(
    record_id: uuid.UUID,
    reason: str,                           # обязательно указывать причину просмотра
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Получить расшифрованные ПД клиента.
    Каждый вызов фиксируется в журнале доступа.
    """
    rid = str(record_id)
    record = await db.get(ClientRecord, rid)
    if not record or record.deleted_at:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    # ОБЯЗАТЕЛЬНОЕ логирование каждого просмотра ПД
    await _log_access(db, rid, str(current_user.id), "view", request, reason)
    await db.commit()

    return ClientRecordResponse(
        id=uuid.UUID(record.id),
        full_name=pd_crypto.decrypt(record.full_name_enc),
        passport_series=pd_crypto.decrypt(record.passport_series_enc) or None,
        passport_number=pd_crypto.decrypt(record.passport_number_enc) or None,
        issued_by=pd_crypto.decrypt(record.issued_by_enc) or None,
        consent_obtained_at=record.consent_obtained_at,
        has_doc_file=bool(record.doc_file_path),
        created_at=record.created_at,
    )


@router.post("/records/{record_id}/document")
async def upload_pd_document(
    record_id: uuid.UUID,
    reason: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Загрузить документ с ПД (фото паспорта).
    Файл шифруется перед сохранением на диск.
    """
    rid = str(record_id)
    record = await db.get(ClientRecord, rid)
    if not record or record.deleted_at:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    # Проверка типа файла
    allowed = ["image/jpeg", "image/png", "image/webp", "application/pdf"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400,
                            detail="Недопустимый тип файла. Разрешены: JPG, PNG, PDF")

    content = await file.read()

    # Проверка размера (20 МБ)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл превышает 20 МБ")

    # Хеш оригинала для проверки целостности
    file_hash = pd_crypto.file_hash(content)

    # Шифруем и сохраняем
    encrypted = pd_crypto.encrypt_file(content)
    saved_path = pd_crypto.save_pd_file(encrypted, file.filename or "document")

    record.doc_file_path = saved_path
    record.doc_file_hash = file_hash

    await _log_access(db, rid, str(current_user.id), "view", request,
                      f"Загрузка документа: {reason}")
    await db.commit()

    return {"status": "uploaded", "file_hash": file_hash}


@router.get("/records/{record_id}/document")
async def download_pd_document(
    record_id: uuid.UUID,
    reason: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Скачать документ ПД. Каждое скачивание логируется."""
    from fastapi.responses import Response

    rid = str(record_id)
    record = await db.get(ClientRecord, rid)
    if not record or not record.doc_file_path or record.deleted_at:
        raise HTTPException(status_code=404, detail="Документ не найден")

    # Логируем скачивание
    await _log_access(db, rid, str(current_user.id), "download", request, reason)
    await db.commit()

    decrypted = pd_crypto.load_pd_file(record.doc_file_path)
    return Response(
        content=decrypted,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=document"},
    )


@router.delete("/records/{record_id}")
async def schedule_delete_client_record(
    record_id: uuid.UUID,
    reason: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Уничтожение ПД по требованию клиента (ст. 21 152-ФЗ).
    Файл перезаписывается нулями. Составляется запись в журнале.
    """
    rid = str(record_id)
    record = await db.get(ClientRecord, rid)
    if not record or record.deleted_at:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    # Уничтожаем файл безвозвратно
    if record.doc_file_path:
        pd_crypto.delete_pd_file(record.doc_file_path)
        record.doc_file_path = None

    # Затираем зашифрованные поля
    empty = pd_crypto.encrypt("")
    record.full_name_enc = empty
    record.passport_series_enc = empty
    record.passport_number_enc = empty
    record.issued_by_enc = empty
    record.issued_date_enc = empty
    record.deleted_at = datetime.now(timezone.utc)

    await _log_access(db, rid, str(current_user.id), "deleted", request, reason)
    await db.commit()

    return {"status": "destroyed", "message": "ПД уничтожены. Акт зафиксирован в журнале."}


@router.get("/access-log/{record_id}")
async def get_access_log(
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Журнал доступа к записи ПД (для проверки соответствия 152-ФЗ)."""
    from sqlalchemy import select
    rid = str(record_id)
    result = await db.execute(
        select(PDAccessLog)
        .where(PDAccessLog.record_id == rid)
        .order_by(PDAccessLog.accessed_at.desc())
        .limit(200)
    )
    logs = result.scalars().all()
    return [
        {
            "action":      log.action,
            "user_id":     str(log.user_id),
            "ip_address":  str(log.ip_address),
            "reason":      log.reason,
            "accessed_at": log.accessed_at.isoformat(),
        }
        for log in logs
    ]


# ── Вспомогательная функция ───────────────────────────────────────────────────

async def _log_access(
    db: AsyncSession,
    record_id: str,
    user_id: str,
    action: str,
    request: Request,
    reason: str,
):
    """Записать событие доступа в журнал (152-ФЗ, обязательно)."""
    client_ip = request.headers.get("X-Real-IP") or (
        request.client.host if request.client else None
    )
    log = PDAccessLog(
        record_id=record_id,
        user_id=user_id,
        action=action,
        ip_address=client_ip,
        user_agent=request.headers.get("User-Agent", "")[:500],
        reason=reason,
    )
    db.add(log)
