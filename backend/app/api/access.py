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
from app.models.business import Product, User


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
