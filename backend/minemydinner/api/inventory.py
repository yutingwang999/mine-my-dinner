"""Inventory, owned cookware, and the always-in-stock list."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError
from minemydinner.constants import STOCK_LEVELS
from minemydinner.db import get_session
from minemydinner.models import AlwaysInStock, Cookware, CookwareOwned, Ingredient, Inventory
from minemydinner.planning.inventory import _fits_stock_mode

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


class InventoryIn(BaseModel):
    ingredient_id: int
    count: int | None = Field(default=None, ge=0)
    level: Literal[*STOCK_LEVELS] | None = None
    grams: float | None = Field(default=None, ge=0)
    expires_on: date | None = None


class InventoryOut(BaseModel):
    ingredient_id: int
    name_zh: str | None
    name_en: str | None
    stock_mode: str
    count: int | None
    level: str | None
    grams: float | None
    expires_on: date | None
    updated_at: datetime


class EntryRef(BaseModel):
    id: int
    name_zh: str | None
    name_en: str | None


def _inventory_out(row: Inventory) -> InventoryOut:
    ingredient = row.ingredient
    return InventoryOut(
        ingredient_id=row.ingredient_id,
        name_zh=ingredient.name_zh,
        name_en=ingredient.name_en,
        stock_mode=ingredient.stock_mode,
        count=row.count,
        level=row.level,
        grams=row.grams,
        expires_on=row.expires_on,
        updated_at=row.updated_at,
    )


def _ingredient(session: Session, ingredient_id: int) -> Ingredient:
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "not_found")
    return ingredient


def _ref(entry: Ingredient | Cookware) -> EntryRef:
    return EntryRef(id=entry.id, name_zh=entry.name_zh, name_en=entry.name_en)


@router.get("/inventory")
def list_inventory(
    session: SessionDep, sort: Literal["name", "expiry"] = "name"
) -> list[InventoryOut]:
    query = select(Inventory).join(Inventory.ingredient)
    if sort == "expiry":
        query = query.order_by(Inventory.expires_on.asc().nulls_last(), Ingredient.name_zh)
    else:
        query = query.order_by(Ingredient.name_zh, Ingredient.name_en)
    return [_inventory_out(row) for row in session.scalars(query)]


@router.put("/inventory")
def put_inventory(data: InventoryIn, session: SessionDep) -> InventoryOut:
    ingredient = _ingredient(session, data.ingredient_id)
    if not _fits_stock_mode(ingredient.stock_mode, data):
        raise ApiError(422, "invalid_for_stock_mode", extra={"stock_mode": ingredient.stock_mode})
    row = session.get(Inventory, ingredient.id) or Inventory(ingredient_id=ingredient.id)
    row.count, row.level, row.grams = data.count, data.level, data.grams
    row.expires_on = data.expires_on
    session.add(row)
    session.commit()
    session.refresh(row)
    return _inventory_out(row)


@router.delete("/inventory/{ingredient_id}", status_code=204)
def delete_inventory(ingredient_id: int, session: SessionDep) -> Response:
    row = session.get(Inventory, ingredient_id)
    if row is None:
        raise ApiError(404, "not_found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/cookware-owned")
def list_owned(session: SessionDep) -> list[EntryRef]:
    query = select(Cookware).join(CookwareOwned).order_by(Cookware.id)
    return [_ref(entry) for entry in session.scalars(query)]


@router.get("/cookware-owned/{cookware_id}")
def get_owned(cookware_id: int, session: SessionDep) -> EntryRef:
    if session.get(CookwareOwned, cookware_id) is None:
        raise ApiError(404, "not_found")
    return _ref(session.get(Cookware, cookware_id))


@router.put("/cookware-owned/{cookware_id}")
def put_owned(cookware_id: int, session: SessionDep) -> EntryRef:
    cookware = session.get(Cookware, cookware_id)
    if cookware is None:
        raise ApiError(404, "not_found")
    if session.get(CookwareOwned, cookware_id) is None:
        session.add(CookwareOwned(cookware_id=cookware_id))
        session.commit()
    return _ref(cookware)


@router.delete("/cookware-owned/{cookware_id}", status_code=204)
def delete_owned(cookware_id: int, session: SessionDep) -> Response:
    row = session.get(CookwareOwned, cookware_id)
    if row is None:
        raise ApiError(404, "not_found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.get("/always-in-stock")
def list_always_in_stock(session: SessionDep) -> list[EntryRef]:
    query = select(Ingredient).join(AlwaysInStock).order_by(Ingredient.id)
    return [_ref(entry) for entry in session.scalars(query)]


@router.get("/always-in-stock/{ingredient_id}")
def get_always_in_stock(ingredient_id: int, session: SessionDep) -> EntryRef:
    if session.get(AlwaysInStock, ingredient_id) is None:
        raise ApiError(404, "not_found")
    return _ref(session.get(Ingredient, ingredient_id))


@router.put("/always-in-stock/{ingredient_id}")
def put_always_in_stock(ingredient_id: int, session: SessionDep) -> EntryRef:
    ingredient = _ingredient(session, ingredient_id)
    if session.get(AlwaysInStock, ingredient_id) is None:
        session.add(AlwaysInStock(ingredient_id=ingredient_id))
        session.commit()
    return _ref(ingredient)


@router.delete("/always-in-stock/{ingredient_id}", status_code=204)
def delete_always_in_stock(ingredient_id: int, session: SessionDep) -> Response:
    row = session.get(AlwaysInStock, ingredient_id)
    if row is None:
        raise ApiError(404, "not_found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)
