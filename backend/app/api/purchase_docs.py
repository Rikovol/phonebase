"""
Документы закупки: фото/PDF подтверждения приобретения у клиента.
Хранятся только на сервере (не в фидах Авито), по папкам IMEI/S/N внутри PURCHASE_DOCS_ROOT.

Реестр: админ — все магазины (фильтр store=); staff — только документы своего магазина.
Загрузка и удаление: админ и staff только для товаров своего магазина (как в карточке товара).
"""
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.access import can_view_product
from app.api.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.utils.imei_sn import imei_or_sn_display
from app.models.business import DocAccessLog, Product, PurchaseDoc, Store, User

router = APIRouter()

ALLOWED_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)
DOC_TYPE_MAP = {
    "receipt": "receipt",
    "contract": "contract",
    "passport": "passport_scan",
    "passport_scan": "passport_scan",
    "other": "other",
}
DOC_LABELS = {
    "receipt": "Чек",
    "contract": "Договор",
    "passport_scan": "Паспорт / удостоверение",
    "other": "Другое",
}


def _ensure_product_read(product: Product, user: User) -> None:
    if not can_view_product(user, product):
        raise HTTPException(status_code=403, detail="Нет доступа к товарам другого магазина")


def _reject_info(user: User) -> None:
    if user.role == "info":
        raise HTTPException(status_code=403, detail="Недоступно для роли «Инфо»")


def can_manage_purchase_docs(user: User, product: Product) -> bool:
    if user.role == "admin":
        return True
    if user.role == "staff" and user.store_id == product.store_id:
        return True
    return False


def _imei_dir(sku: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (sku or "").strip())[:128]
    return s or "unknown"


def _safe_orig_name(name: str) -> str:
    base = os.path.basename(name or "") or "document"
    base = "".join(c for c in base if c.isalnum() or c in "._- ")
    return (base[:180] or "file").strip()


def _purchase_root() -> Path:
    root = Path(settings.PURCHASE_DOCS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


class PurchaseDocOut(BaseModel):
    id: str
    doc_type: str
    doc_type_label: str
    supplier_name: Optional[str]
    has_personal_data: bool
    created_at: str
    filename: str


class RegistryRow(BaseModel):
    id: str
    product_id: str
    imei: str
    model: str
    store_name: str
    doc_type: str
    doc_type_label: str
    supplier_name: Optional[str]
    created_at: str
    filename: str


@router.get("/registry", response_model=dict)
async def list_registry(
    store: Optional[str] = Query(None, description="Фильтр по названию магазина"),
    page: int = Query(1, ge=1),
    size: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Реестр: админ — все магазины; staff — только свой магазин."""
    _reject_info(current_user)
    base_filter = (
        select(PurchaseDoc.id)
        .join(Product, PurchaseDoc.product_id == Product.id)
        .join(Store, Product.store_id == Store.id)
    )
    if current_user.role == "staff":
        if not current_user.store_id:
            return {"items": [], "total": 0, "page": page, "size": size}
        base_filter = base_filter.where(Product.store_id == current_user.store_id)
    elif current_user.role == "admin" and store:
        base_filter = base_filter.where(Store.name == store)

    total = (
        await db.execute(select(func.count()).select_from(base_filter.subquery()))
    ).scalar() or 0

    q = (
        select(PurchaseDoc, Product, Store.name.label("store_name"))
        .join(Product, PurchaseDoc.product_id == Product.id)
        .join(Store, Product.store_id == Store.id)
    )
    if current_user.role == "staff":
        q = q.where(Product.store_id == current_user.store_id)
    elif current_user.role == "admin" and store:
        q = q.where(Store.name == store)

    q = q.order_by(PurchaseDoc.created_at.desc()).offset((page - 1) * size).limit(size)
    rows = (await db.execute(q)).all()

    items = []
    for doc, product, store_name in rows:
        rel = doc.file_path or ""
        fname = Path(rel).name if rel else "—"
        items.append(
            RegistryRow(
                id=doc.id,
                product_id=product.id,
                imei=imei_or_sn_display(product.sku_1c),
                model=product.model,
                store_name=store_name,
                doc_type=doc.doc_type,
                doc_type_label=DOC_LABELS.get(doc.doc_type, doc.doc_type),
                supplier_name=doc.supplier_name,
                created_at=doc.created_at.isoformat(),
                filename=fname,
            )
        )
    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/product/{product_id}", response_model=PurchaseDocOut)
async def upload_purchase_doc(
    product_id: str,
    doc_type: str = Query(..., description="receipt | contract | passport | other"),
    supplier_name: str = Query(..., min_length=1, max_length=255),
    has_pd_consent: bool = Query(False, description="Согласие на ПД для скана паспорта"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_read(product, current_user)
    if not can_manage_purchase_docs(current_user, product):
        raise HTTPException(
            status_code=403,
            detail="Загрузку документов закупки выполняют администраторы и сотрудники своего магазина",
        )
    if product.is_sold and current_user.role != "admin":
        raise HTTPException(status_code=400, detail="Нельзя добавлять документы к проданному товару")

    mapped = DOC_TYPE_MAP.get(doc_type.strip().lower())
    if not mapped:
        raise HTTPException(status_code=400, detail="Недопустимый тип документа")

    if mapped == "passport_scan" and not has_pd_consent:
        raise HTTPException(
            status_code=400,
            detail="Для скана паспорта подтвердите согласие клиента на обработку ПД (152-ФЗ)",
        )

    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Разрешены только PDF, JPG, PNG, WEBP",
        )

    raw = await file.read()
    max_b = settings.MAX_DOC_SIZE_MB * 1024 * 1024
    if len(raw) > max_b:
        raise HTTPException(status_code=400, detail=f"Файл больше {settings.MAX_DOC_SIZE_MB} МБ")

    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get(ct, ".bin")
    imei_part = _imei_dir(product.sku_1c)
    orig = _safe_orig_name(file.filename or "")
    if not orig.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
        orig = orig + ext
    fname = f"{uuid.uuid4().hex}_{orig}"
    rel_dir = imei_part
    out_dir = _purchase_root() / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname
    rel_path = f"{rel_dir}/{fname}".replace("\\", "/")

    with open(out_path, "wb") as f:
        f.write(raw)

    doc = PurchaseDoc(
        product_id=product_id,
        uploaded_by=current_user.id,
        doc_type=mapped,
        supplier_name=supplier_name.strip(),
        has_personal_data=(mapped == "passport_scan"),
        file_path=rel_path,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return PurchaseDocOut(
        id=doc.id,
        doc_type=doc.doc_type,
        doc_type_label=DOC_LABELS.get(doc.doc_type, doc.doc_type),
        supplier_name=doc.supplier_name,
        has_personal_data=doc.has_personal_data,
        created_at=doc.created_at.isoformat(),
        filename=fname,
    )


@router.get("/{doc_id}/file")
async def download_purchase_doc(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = await db.get(PurchaseDoc, doc_id)
    if not doc or not doc.file_path:
        raise HTTPException(status_code=404, detail="Документ не найден")

    product = await db.get(Product, doc.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_read(product, current_user)

    path = _purchase_root() / doc.file_path.replace("\\", "/")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл отсутствует на диске")

    suf = path.suffix.lower()
    mt = "application/octet-stream"
    if suf == ".pdf":
        mt = "application/pdf"
    elif suf in (".jpg", ".jpeg"):
        mt = "image/jpeg"
    elif suf == ".png":
        mt = "image/png"
    elif suf == ".webp":
        mt = "image/webp"

    db.add(DocAccessLog(doc_id=doc_id, user_id=current_user.id, action="download"))
    await db.commit()

    return FileResponse(
        path,
        media_type=mt,
        filename=path.name,
    )


@router.post("/{doc_id}/log")
async def log_doc_access(
    doc_id: str,
    action: str = Query(..., regex="^(view|print)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Логирование просмотра/печати документа с фронтенда."""
    doc = await db.get(PurchaseDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    product = await db.get(Product, doc.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_read(product, current_user)

    db.add(DocAccessLog(doc_id=doc_id, user_id=current_user.id, action=action))
    await db.commit()
    return {"ok": True}


@router.get("/{doc_id}/log")
async def get_doc_access_log(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """История просмотров/печати/скачиваний документа."""
    doc = await db.get(PurchaseDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")
    product = await db.get(Product, doc.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_read(product, current_user)

    rows = (await db.execute(
        select(DocAccessLog, User.username, User.full_name)
        .join(User, DocAccessLog.user_id == User.id)
        .where(DocAccessLog.doc_id == doc_id)
        .order_by(DocAccessLog.created_at.desc())
        .limit(100)
    )).all()

    action_labels = {"view": "Просмотр", "print": "Печать", "download": "Скачивание"}
    return [
        {
            "action": action_labels.get(r[0].action, r[0].action),
            "user": r[2] or r[1],
            "at": r[0].created_at.isoformat(),
        }
        for r in rows
    ]


@router.delete("/{doc_id}")
async def delete_purchase_doc(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = await db.get(PurchaseDoc, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Документ не найден")

    product = await db.get(Product, doc.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")
    _reject_info(current_user)
    _ensure_product_read(product, current_user)
    if not can_manage_purchase_docs(current_user, product):
        raise HTTPException(
            status_code=403,
            detail="Удаление документов — только у администраторов и сотрудников своего магазина",
        )
    if product.is_sold and current_user.role != "admin":
        raise HTTPException(status_code=400, detail="Нельзя удалять документы у проданного товара")

    rel = doc.file_path
    await db.delete(doc)
    await db.commit()

    if rel:
        p = _purchase_root() / rel.replace("\\", "/")
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass

    return {"status": "deleted", "id": doc_id}
