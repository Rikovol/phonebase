"""
Агрегаты розничных цен по данным PhoneBase (остаток / опционально проданные).
Внешние площадки (Авито и др.) здесь не парсятся — только сводка по вашей базе.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.products import _apply_product_filters
from app.core.database import get_db
from app.models.business import Product, Store, User

router = APIRouter()


class PriceAggRow(BaseModel):
    brand: Optional[str] = None
    model: str
    storage: Optional[str] = None
    condition: Optional[str] = None
    count: int = Field(..., ge=0)
    avg_retail: float
    min_retail: float
    max_retail: float


class PriceAggResponse(BaseModel):
    items: list[PriceAggRow]
    total: int


@router.get("/price-aggregates", response_model=PriceAggResponse)
async def price_aggregates(
    store: Optional[str] = Query(None, description="Фильтр по магазину (опционально)"),
    brand: Optional[str] = Query(None),
    condition: Optional[str] = Query(None, description="Состояние"),
    q: Optional[str] = Query(None, description="Поиск по модели"),
    include_sold: bool = Query(False, description="Включить проданные позиции"),
    is_new: Optional[bool] = Query(None, description="Фильтр: новые (true) или б/у (false)"),
    min_units: int = Query(1, ge=1, le=100, description="Минимум единиц в группе"),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    base = (
        select(
            Product.brand,
            Product.model,
            Product.storage,
            Product.condition,
            func.count().label("cnt"),
            func.avg(Product.price_retail).label("avg_p"),
            func.min(Product.price_retail).label("min_p"),
            func.max(Product.price_retail).label("max_p"),
        )
        .select_from(Product)
        .join(Store, Product.store_id == Store.id)
        .where(Product.price_retail.isnot(None))
    )
    base = _apply_product_filters(
        base,
        store=store,
        brand=brand,
        condition=condition,
        q=q,
        include_sold=include_sold,
        is_new=is_new,
    )
    base = base.group_by(Product.brand, Product.model, Product.storage, Product.condition)
    base = base.having(func.count() >= min_units)
    base = base.order_by(func.count().desc(), Product.model.asc()).limit(limit)

    rows = (await db.execute(base)).all()

    items = [
        PriceAggRow(
            brand=r.brand,
            model=r.model,
            storage=r.storage,
            condition=r.condition,
            count=int(r.cnt),
            avg_retail=float(r.avg_p or 0),
            min_retail=float(r.min_p or 0),
            max_retail=float(r.max_p or 0),
        )
        for r in rows
    ]
    return PriceAggResponse(items=items, total=len(items))
