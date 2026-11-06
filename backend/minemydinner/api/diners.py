"""The user's own record, saved friends, and their dietary rules."""

from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.api.deps import ApiError
from minemydinner.constants import ALLERGENS, RULE_TYPES
from minemydinner.db import get_session
from minemydinner.models import DietaryRule, Diner, Ingredient

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


class DinerIn(BaseModel):
    display_name: str

    @field_validator("display_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class DinerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    is_self: bool


class RuleIn(BaseModel):
    """Exactly one target: a listed allergen, a dictionary entry, or free text.
    An allergy never uses free text, so it can always be checked by code."""

    rule_type: Literal[*RULE_TYPES]
    allergen: Literal[*ALLERGENS] | None = None
    ingredient_id: int | None = None
    text: str | None = None

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def one_target(self) -> Self:
        targets = [self.allergen, self.ingredient_id, self.text]
        if sum(target is not None for target in targets) != 1:
            raise ValueError("a rule needs exactly one of allergen, ingredient_id, or text")
        if self.rule_type == "allergy" and self.text is not None:
            raise ValueError("an allergy needs a listed allergen or a dictionary entry")
        return self


class RuleOut(BaseModel):
    id: int
    diner_id: int
    rule_type: str
    allergen: str | None
    ingredient_id: int | None
    ingredient_name_zh: str | None
    ingredient_name_en: str | None
    text: str | None


def _diner(session: Session, diner_id: int) -> Diner:
    diner = session.get(Diner, diner_id)
    if diner is None:
        raise ApiError(404, "not_found")
    return diner


def _rule_out(rule: DietaryRule) -> RuleOut:
    ingredient = rule.ingredient
    return RuleOut(
        id=rule.id,
        diner_id=rule.diner_id,
        rule_type=rule.rule_type,
        allergen=rule.allergen,
        ingredient_id=rule.ingredient_id,
        ingredient_name_zh=ingredient.name_zh if ingredient else None,
        ingredient_name_en=ingredient.name_en if ingredient else None,
        text=rule.text,
    )


@router.get("/diners")
def list_diners(session: SessionDep) -> list[DinerOut]:
    query = select(Diner).order_by(Diner.is_self.desc(), Diner.id)
    return [DinerOut.model_validate(diner) for diner in session.scalars(query)]


@router.post("/diners", status_code=201)
def create_diner(data: DinerIn, session: SessionDep) -> DinerOut:
    diner = Diner(display_name=data.display_name, is_self=False)
    session.add(diner)
    session.commit()
    return DinerOut.model_validate(diner)


@router.patch("/diners/{diner_id}")
def rename_diner(diner_id: int, data: DinerIn, session: SessionDep) -> DinerOut:
    diner = _diner(session, diner_id)
    diner.display_name = data.display_name
    session.commit()
    return DinerOut.model_validate(diner)


@router.delete("/diners/{diner_id}", status_code=204)
def delete_diner(diner_id: int, session: SessionDep) -> Response:
    """Deleting a friend deletes their rules too. The user's own record stays."""
    diner = _diner(session, diner_id)
    if diner.is_self:
        raise ApiError(409, "self_diner")
    session.delete(diner)
    session.commit()
    return Response(status_code=204)


@router.get("/diners/{diner_id}/rules")
def list_rules(diner_id: int, session: SessionDep) -> list[RuleOut]:
    _diner(session, diner_id)
    query = select(DietaryRule).where(DietaryRule.diner_id == diner_id).order_by(DietaryRule.id)
    return [_rule_out(rule) for rule in session.scalars(query)]


@router.post("/diners/{diner_id}/rules", status_code=201)
def create_rule(diner_id: int, data: RuleIn, session: SessionDep) -> RuleOut:
    _diner(session, diner_id)
    if data.ingredient_id is not None and session.get(Ingredient, data.ingredient_id) is None:
        raise ApiError(422, "unknown_ingredient")
    rule = DietaryRule(diner_id=diner_id, **data.model_dump())
    session.add(rule)
    session.commit()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, session: SessionDep) -> Response:
    rule = session.get(DietaryRule, rule_id)
    if rule is None:
        raise ApiError(404, "not_found")
    session.delete(rule)
    session.commit()
    return Response(status_code=204)
