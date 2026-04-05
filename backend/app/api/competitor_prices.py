"""API конкурентных цен — просмотр и ручной запуск парсинга."""
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.business import CompetitorPrice, User

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────────

class CompetitorPriceRow(BaseModel):
    id: str
    source: str
    brand: str
    model: str
    memory: Optional[str] = None
    full_name: Optional[str] = None
    price_excellent: Optional[int] = None
    price_good: Optional[int] = None
    price_poor: Optional[int] = None
    price_repair: Optional[int] = None
    parsed_at: str


class CompetitorPriceResponse(BaseModel):
    items: list[CompetitorPriceRow]
    total: int
    brands: list[str]
    sources: list[str]
    last_parsed: Optional[str] = None


class ParseResult(BaseModel):
    status: str
    message: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", response_model=CompetitorPriceResponse)
async def list_competitor_prices(
    source: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Поиск по модели"),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    stmt = select(CompetitorPrice)
    if source:
        stmt = stmt.where(CompetitorPrice.source == source)
    if brand:
        stmt = stmt.where(CompetitorPrice.brand == brand)
    if q:
        stmt = stmt.where(CompetitorPrice.model.ilike(f"%{q}%"))
    stmt = stmt.order_by(CompetitorPrice.brand, CompetitorPrice.model, CompetitorPrice.memory)

    total_q = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    # Справочные данные для фильтров
    brands_q = select(CompetitorPrice.brand).distinct().order_by(CompetitorPrice.brand)
    brands = [r for r in (await db.execute(brands_q)).scalars().all()]

    sources_q = select(CompetitorPrice.source).distinct().order_by(CompetitorPrice.source)
    sources = [r for r in (await db.execute(sources_q)).scalars().all()]

    last_q = select(func.max(CompetitorPrice.parsed_at))
    last_parsed = (await db.execute(last_q)).scalar()

    return CompetitorPriceResponse(
        items=[
            CompetitorPriceRow(
                id=r.id,
                source=r.source,
                brand=r.brand,
                model=r.model,
                memory=r.memory,
                full_name=r.full_name,
                price_excellent=r.price_excellent,
                price_good=r.price_good,
                price_poor=r.price_poor,
                price_repair=r.price_repair,
                parsed_at=r.parsed_at.isoformat(),
            )
            for r in rows
        ],
        total=total,
        brands=brands,
        sources=sources,
        last_parsed=last_parsed.isoformat() if last_parsed else None,
    )


async def _run_parse_bg(source: str):
    """Фоновая задача парсинга."""
    from app.core.database import AsyncSessionLocal

    if source == "goodcom":
        from app.services.parse_goodcom import run_goodcom_parse
        async with AsyncSessionLocal() as db:
            try:
                count = await run_goodcom_parse(db)
                logger.info("Парсинг %s завершён: %d записей", source, count)
            except Exception:
                logger.exception("Ошибка парсинга %s", source)


@router.post("/parse", response_model=ParseResult)
async def trigger_parse(
    bg: BackgroundTasks,
    source: str = Query("goodcom", description="Источник для парсинга"),
    user: User = Depends(get_current_user),
):
    if user.role != "admin":
        from fastapi import HTTPException
        raise HTTPException(403, "Только администратор может запускать парсинг")

    supported = ["goodcom"]
    if source not in supported:
        from fastapi import HTTPException
        raise HTTPException(400, f"Неизвестный источник: {source}. Доступные: {supported}")

    bg.add_task(_run_parse_bg, source)
    return ParseResult(status="started", message=f"Парсинг {source} запущен в фоне")
