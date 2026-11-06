from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from minemydinner.models import Cookware, Ingredient


def _alias_matches(column, term: str):
    alias = func.unnest(column).table_valued("value").render_derived()
    return exists(select(1).select_from(alias).where(func.lower(alias.c.value) == term))


def find_term_owner[T: (Ingredient, Cookware)](
    session: Session, table: type[T], term: str
) -> T | None:
    """Return the entry whose name or alias equals the term, ignoring case and outer spaces.

    Matching is exact on purpose: a wrong guess would link a recipe to the wrong
    ingredient and break allergy checks.
    """
    term = term.strip().lower()
    if not term:
        return None
    query = select(table).where(
        or_(
            func.lower(table.name_zh) == term,
            func.lower(table.name_en) == term,
            _alias_matches(table.aliases_zh, term),
            _alias_matches(table.aliases_en, term),
        )
    )
    return session.scalars(query.order_by(table.id)).first()


def match_ingredient(session: Session, name: str) -> Ingredient | None:
    return find_term_owner(session, Ingredient, name)


def match_cookware(session: Session, name: str) -> Cookware | None:
    return find_term_owner(session, Cookware, name)
