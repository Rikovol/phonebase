from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.access import can_modify_product, can_view_product
from app.api.auth import get_current_user
from app.api.purchase_docs import DOC_LABELS
from app.core.database import get_db
from app.models.business import Product, ProductPhoto, PurchaseDoc, Store, User
from app.utils.imei_sn import imei_or_sn_display

router = APIRouter()


class ProductOut(BaseModel):
    id: str
    store_name: str
    brand: Optional[str]
    model: str
    storage: Optional[str]
    color: Optional[str]
    condition: Optional[str]
    battery_pct: Optional[str]
    in_repair: bool
    imei: str
    price_retail: Optional[float]
    price_cost: Optional[float]
    is_sold: bool
    is_new: bool = False
    quantity: int = 0
    sold_at: Optional[str]
    sim_count: Optional[int] = None
    sim_type: Optional[str] = None
    completeness: Optional[str] = None
    site_published: bool = False
    avito_published: bool
    photos_count: int = 0
    docs_count: int = 0
    thumbnail_url: Optional[str] = None


class ProductUpdate(BaseModel):
    price_retail: Optional[float] = None
    price_cost: Optional[float] = None
    condition: Optional[str] = None
    battery_pct: Optional[str] = None
    in_repair: Optional[bool] = None
    sim_count: Optional[int] = None
    sim_type: Optional[str] = None
    completeness: Optional[str] = None
    site_published: Optional[bool] = None
    avito_published: Optional[bool] = None
    avito_title: Optional[str] = None
    avito_description: Optional[str] = None


class ProductPhotoOut(BaseModel):
    id: str
    url: str
    is_main: bool


class PurchaseDocItem(BaseModel):
    id: str
    doc_type: str
    doc_type_label: str
    supplier_name: Optional[str]
    has_personal_data: bool
    created_at: str
    filename: str


class ProductDetailOut(BaseModel):
    id: str
    store_id: str
    store_name: str
    brand: Optional[str]
    model: str
    storage: Optional[str]
    color: Optional[str]
    condition: Optional[str]
    battery_pct: Optional[str]
    in_repair: bool
    imei: str
    price_retail: Optional[float]
    price_cost: Optional[float]
    is_sold: bool
    is_new: bool = False
    sold_at: Optional[str]
    purchased_at: Optional[str] = None
    sim_count: Optional[int] = None
    sim_type: Optional[str] = None
    completeness: Optional[str] = None
    site_published: bool = False
    avito_published: bool
    avito_title: Optional[str]
    avito_description: Optional[str]
    photos_count: int
    docs_count: int
    photos: list[ProductPhotoOut]
    docs: list[PurchaseDocItem] = Field(default_factory=list)


def _photo_public_url(photo: ProductPhoto) -> str:
    path = photo.file_path.lstrip("/").replace("\\", "/")
    return f"/media/{path}"


def _media_url_from_path(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    path = file_path.lstrip("/").replace("\\", "/")
    return f"/media/{path}"


def _can_see_cost(user: User, product: Product) -> bool:
    """Учётная цена и маржа в списке и карточке: admin — всё; staff — только свой магазин; info — нет."""
    if user.role == "info":
        return False
    if user.role == "admin":
        return True
    return product.store_id == user.store_id


def _apply_product_filters(
    query,
    *,
    store: Optional[str],
    brand: Optional[str],
    condition: Optional[str],
    q: Optional[str],
    include_sold: bool,
    is_new: Optional[bool] = None,
    sold_only: bool = False,
    avito_published: Optional[bool] = None,
):
    """Фильтры списка. По ролям не сужаем: весь каталог для всех авторизованных; store — только явный query."""
    if sold_only:
        query = query.where(Product.is_sold == True)  # noqa: E712
    elif not include_sold:
        query = query.where(Product.is_sold == False)  # noqa: E712

    if is_new is not None:
        query = query.where(Product.is_new == is_new)  # noqa: E712

    if avito_published is not None:
        query = query.where(Product.avito_published == avito_published)  # noqa: E712

    if store:
        s = store.strip()
        if s:
            # Совпадение без учёта регистра и лишних пробелов в БД/параметре
            query = query.where(
                func.lower(func.trim(Store.name)) == func.lower(s)
            )

    if brand:
        query = query.where(Product.brand == brand)

    if condition:
        query = query.where(Product.condition == condition)

    if q:
        # Каждый токен ищется отдельно, но в единой строке brand+model+storage
        # (или в imei/sku_1c). Так «15 128» найдёт «Apple iPhone 15 128GB»,
        # но не смешает поля между собой.
        combined = func.concat(
            func.coalesce(Product.brand, ""), " ",
            func.coalesce(Product.model, ""), " ",
            func.coalesce(Product.storage, ""),
        )
        for token in q.strip().split():
            pattern = f"%{token}%"
            query = query.where(or_(
                combined.ilike(pattern),
                Product.imei.ilike(pattern),
                Product.sku_1c.ilike(pattern),
            ))
    return query


@router.get("/")
async def list_products(
    store: Optional[str] = Query(
        None,
        description="Сузить список по названию магазина. Не влияет на права: staff видит все магазины, если не задан.",
    ),
    brand: Optional[str] = Query(None),
    condition: Optional[str] = Query(None, description="Состояние"),
    q: Optional[str] = Query(None, description="Поиск по модели / IMEI"),
    include_sold: bool = Query(False, description="Показывать проданные"),
    sold_only: bool = Query(False, description="Только проданные"),
    is_new: Optional[bool] = Query(None, description="Фильтр: новые (true) или б/у (false) товары"),
    avito_published: Optional[bool] = Query(None, description="Фильтр: на Авито (true) или нет (false)"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=20000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip_media = is_new is True

    if skip_media:
        query = (
            select(Product, Store.name.label("store_name"))
            .join(Store, Product.store_id == Store.id)
        )
    else:
        photos_subq = (
            select(ProductPhoto.product_id, func.count(ProductPhoto.id).label("pc"))
            .group_by(ProductPhoto.product_id)
        ).subquery()
        docs_subq = (
            select(PurchaseDoc.product_id, func.count(PurchaseDoc.id).label("dc"))
            .group_by(PurchaseDoc.product_id)
        ).subquery()
        photo_pick = (
            select(
                ProductPhoto.product_id,
                ProductPhoto.file_path,
                func.row_number()
                .over(
                    partition_by=ProductPhoto.product_id,
                    order_by=(ProductPhoto.is_main.desc(), ProductPhoto.created_at.asc()),
                )
                .label("rn"),
            )
        ).subquery()
        thumb_subq = (
            select(photo_pick.c.product_id, photo_pick.c.file_path).where(photo_pick.c.rn == 1)
        ).subquery()
        query = (
            select(
                Product,
                Store.name.label("store_name"),
                func.coalesce(photos_subq.c.pc, 0).label("photos_count"),
                func.coalesce(docs_subq.c.dc, 0).label("docs_count"),
                thumb_subq.c.file_path.label("thumb_path"),
            )
            .join(Store, Product.store_id == Store.id)
            .outerjoin(photos_subq, photos_subq.c.product_id == Product.id)
            .outerjoin(docs_subq, docs_subq.c.product_id == Product.id)
            .outerjoin(thumb_subq, thumb_subq.c.product_id == Product.id)
        )

    query = _apply_product_filters(
        query,
        store=store,
        brand=brand,
        condition=condition,
        q=q,
        include_sold=include_sold,
        is_new=is_new,
        sold_only=sold_only,
        avito_published=avito_published,
    )
    query = query.order_by(Product.updated_at.desc())

    count_base = select(Product.id).join(Store, Product.store_id == Store.id)
    count_base = _apply_product_filters(
        count_base,
        store=store,
        brand=brand,
        condition=condition,
        q=q,
        include_sold=include_sold,
        is_new=is_new,
        sold_only=sold_only,
        avito_published=avito_published,
    )
    total = (await db.execute(select(func.count()).select_from(count_base.subquery()))).scalar() or 0

    query = query.offset((page - 1) * size).limit(size)
    rows = (await db.execute(query)).all()

    items = []
    for row in rows:
        if skip_media:
            product, store_name = row
            photos_count, docs_count, thumb_path = 0, 0, None
        else:
            product, store_name, photos_count, docs_count, thumb_path = row
        can_see_cost = _can_see_cost(current_user, product)
        hide_media = current_user.role == "info"
        items.append(
            ProductOut(
                id=product.id,
                store_name=store_name,
                brand=product.brand,
                model=product.model,
                storage=product.storage,
                color=product.color,
                condition=product.condition,
                battery_pct=product.battery_pct,
                in_repair=product.in_repair,
                imei=imei_or_sn_display(product.sku_1c),
                price_retail=float(product.price_retail) if product.price_retail else None,
                price_cost=float(product.price_cost) if can_see_cost and product.price_cost else None,
                is_sold=product.is_sold,
                is_new=product.is_new,
                quantity=product.quantity or 0,
                sold_at=product.sold_at.isoformat() if product.sold_at else None,
                sim_count=product.sim_count,
                sim_type=product.sim_type,
                completeness=product.completeness,
                site_published=product.site_published,
                avito_published=product.avito_published,
                photos_count=0 if hide_media else int(photos_count),
                docs_count=0 if hide_media else int(docs_count),
                thumbnail_url=None if hide_media else _media_url_from_path(thumb_path),
            )
        )

    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/{product_id}", response_model=ProductDetailOut)
async def get_product(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Product, Store.name.label("store_name"))
        .join(Store, Product.store_id == Store.id)
        .where(Product.id == product_id)
        .options(selectinload(Product.photos), selectinload(Product.docs))
    )
    row = result.unique().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Товар не найден")
    product, store_name = row

    if not can_view_product(current_user, product):
        raise HTTPException(status_code=403, detail="Нет доступа к товарам другого магазина")

    photos_sorted = sorted(
        product.photos, key=lambda ph: (not ph.is_main, ph.created_at)
    )
    if current_user.role == "info":
        photo_items = []
        doc_items = []
        photos_cnt = 0
        docs_cnt = 0
    else:
        photo_items = [
            ProductPhotoOut(id=ph.id, url=_photo_public_url(ph), is_main=ph.is_main)
            for ph in photos_sorted
        ]
        docs_sorted = sorted(product.docs, key=lambda d: d.created_at, reverse=True)
        doc_items = [
            PurchaseDocItem(
                id=d.id,
                doc_type=d.doc_type,
                doc_type_label=DOC_LABELS.get(d.doc_type, d.doc_type),
                supplier_name=d.supplier_name,
                has_personal_data=d.has_personal_data,
                created_at=d.created_at.isoformat(),
                filename=Path(d.file_path).name if d.file_path else "—",
            )
            for d in docs_sorted
        ]
        photos_cnt = len(product.photos)
        docs_cnt = len(product.docs)

    can_see_cost = _can_see_cost(current_user, product)

    return ProductDetailOut(
        id=product.id,
        store_id=product.store_id,
        store_name=store_name,
        brand=product.brand,
        model=product.model,
        storage=product.storage,
        color=product.color,
        condition=product.condition,
        battery_pct=product.battery_pct,
        in_repair=product.in_repair,
        imei=imei_or_sn_display(product.sku_1c),
        price_retail=float(product.price_retail) if product.price_retail else None,
        price_cost=float(product.price_cost) if can_see_cost and product.price_cost else None,
        is_sold=product.is_sold,
        is_new=product.is_new,
        sold_at=product.sold_at.isoformat() if product.sold_at else None,
        purchased_at=product.purchased_at.isoformat() if product.purchased_at else None,
        sim_count=product.sim_count,
                sim_type=product.sim_type,
        completeness=product.completeness,
        site_published=product.site_published,
        avito_published=product.avito_published,
        avito_title=product.avito_title,
        avito_description=product.avito_description,
        photos_count=photos_cnt,
        docs_count=docs_cnt,
        photos=photo_items,
        docs=doc_items,
    )


@router.patch("/{product_id}")
async def update_product(
    product_id: str,
    body: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Товар не найден")

    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Роль «Инфо» не может редактировать товары")

    if product.is_sold and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Проданный товар нельзя редактировать")

    if not can_modify_product(current_user, product):
        raise HTTPException(status_code=403, detail="Нет доступа к редактированию товаров другого магазина")

    if body.avito_published is True and product.is_sold:
        raise HTTPException(status_code=400, detail="Нельзя публиковать проданный товар на Авито")

    if body.avito_published is True and product.in_repair:
        raise HTTPException(status_code=400, detail="Нельзя публиковать товар в ремонте на Авито")

    if body.avito_published is True:
        cnt = (
            await db.execute(
                select(func.count()).select_from(ProductPhoto).where(ProductPhoto.product_id == product_id)
            )
        ).scalar() or 0
        if cnt == 0:
            raise HTTPException(
                status_code=400,
                detail="Нельзя опубликовать на Авито без фотографий товара",
            )

    if body.price_retail is not None:
        product.price_retail = body.price_retail
    if body.price_cost is not None:
        product.price_cost = body.price_cost
    if body.condition is not None:
        product.condition = body.condition
    if body.battery_pct is not None:
        product.battery_pct = body.battery_pct[:10] if body.battery_pct else None
    if body.in_repair is not None:
        product.in_repair = body.in_repair
        if body.in_repair and product.avito_published:
            product.avito_published = False
    if body.sim_count is not None:
        product.sim_count = body.sim_count if body.sim_count in (1, 2, 3) else None
    if body.completeness is not None:
        product.completeness = body.completeness[:100] if body.completeness else None
    if body.site_published is not None:
        product.site_published = body.site_published
    if body.avito_published is not None:
        product.avito_published = body.avito_published
    if body.avito_title is not None:
        product.avito_title = body.avito_title[:50] if body.avito_title else None
    if body.avito_description is not None:
        product.avito_description = body.avito_description[:7500] if body.avito_description else None

    product.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # Авто-синхронизация с Авито при изменении цены
    if body.price_retail is not None and product.avito_published and product.avito_item_id:
        from app.tasks import avito_push_price
        avito_push_price.delay(product.id)

    return {"status": "updated", "id": product.id}


@router.post("/bulk-avito-publish")
async def bulk_avito_publish(
    store: Optional[str] = Query(None, description="Название магазина"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Массовая публикация на Авито всех б/у товаров с фото, которые ещё не опубликованы."""
    if current_user.role == "info":
        raise HTTPException(status_code=403, detail="Недоступно для роли «Инфо»")

    photos_subq = (
        select(ProductPhoto.product_id)
        .group_by(ProductPhoto.product_id)
        .having(func.count(ProductPhoto.id) > 0)
    ).subquery()

    query = (
        select(Product)
        .join(Store, Product.store_id == Store.id)
        .join(photos_subq, photos_subq.c.product_id == Product.id)
        .where(
            Product.is_sold == False,  # noqa: E712
            Product.is_new == False,  # noqa: E712
            Product.avito_published == False,  # noqa: E712
            Product.in_repair == False,  # noqa: E712
            Product.price_retail.isnot(None),
        )
    )
    if store:
        query = query.where(func.lower(func.trim(Store.name)) == func.lower(store.strip()))
    if current_user.role != "admin":
        query = query.where(Product.store_id == current_user.store_id)

    products = (await db.execute(query)).scalars().all()
    now = datetime.now(timezone.utc)
    for p in products:
        p.avito_published = True
        p.updated_at = now
    await db.commit()
    return {"published": len(products)}
