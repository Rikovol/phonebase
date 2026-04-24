"""Правила доступа к товарам по ролям.

Роль Staff (привязка к магазину: user.store_id):
  • Каталог GET /products — все товары всех магазинов; параметр store= только сужает список.
  • Учётная цена и маржа в ответе — только для товаров своего магазина; для остальных скрыты.
  • Карточка товара GET /products/{id}, работа с фото и документами закупки — только для товаров
    своего магазина (чужие карточки недоступны).
  • PATCH товара, загрузка и удаление фото — только свой магазин.

Роль info: каталог всех магазинов без учётной/прибыли и без редактирования; карточки — просмотр по
всем магазинам (без чувствительных операций в API).

Админ: полный доступ ко всем магазинам.
"""
from app.models.business import Product, SiteBonus, SiteMessage, SitePromotion, User


def can_view_product(user: User, product: Product) -> bool:
    """Просмотр карточки товара (детали, фото, документы в связанных эндпоинтах)."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return True
    if user.role == "staff":
        return bool(user.store_id and product.store_id == user.store_id)
    return False


def can_modify_product(user: User, product: Product) -> bool:
    """PATCH товара и загрузка/удаление фото."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return False
    if user.role == "staff":
        return bool(user.store_id and product.store_id == user.store_id)
    return False


def can_view_site_message(user: User, message: SiteMessage) -> bool:
    """Просмотр заявки с сайта. info — read-only весь список без PATCH/POST."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return True
    if user.role == "staff":
        return bool(user.store_id and message.store_id == user.store_id)
    return False


def can_modify_site_message(user: User, message: SiteMessage) -> bool:
    """PATCH заявки, ответ клиенту."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return False
    if user.role == "staff":
        return bool(user.store_id and message.store_id == user.store_id)
    return False


def can_view_site_promotion(user: User, promotion: SitePromotion) -> bool:
    """Просмотр акции. info — read-only."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return True
    if user.role == "staff":
        # staff видит свои акции + глобальные (store_id=None)
        if promotion.store_id is None:
            return True
        return bool(user.store_id and promotion.store_id == user.store_id)
    return False


def can_modify_site_promotion(user: User, promotion: SitePromotion) -> bool:
    """POST/PATCH/DELETE акции."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return False
    if user.role == "staff":
        # staff не может редактировать глобальные акции
        if promotion.store_id is None:
            return False
        return bool(user.store_id and promotion.store_id == user.store_id)
    return False


def can_view_site_bonus(user: User, bonus: SiteBonus) -> bool:
    """Просмотр бонусной программы. info — read-only."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return True
    if user.role == "staff":
        return bool(user.store_id and bonus.store_id == user.store_id)
    return False


def can_modify_site_bonus(user: User, bonus: SiteBonus) -> bool:
    """POST/PATCH/DELETE бонусной программы."""
    if user.role == "admin":
        return True
    if user.role == "info":
        return False
    if user.role == "staff":
        return bool(user.store_id and bonus.store_id == user.store_id)
    return False
