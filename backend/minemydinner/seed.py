import json
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from minemydinner.db import SessionFactory, configure_engine
from minemydinner.models import AlwaysInStock, Cookware, Diner, Ingredient
from minemydinner.settings import get_settings

SEED_DIR = Path(__file__).resolve().parents[1] / "seed"


def _load(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


def _existing_row(session: Session, model: type, name_zh: str) -> Any:
    row = session.scalars(select(model).where(model.name_zh == name_zh)).one_or_none()
    if row is not None and row.source != "seed":
        raise ValueError(f"Seed entry {name_zh} collides with a user-created entry")
    return row


def _seed_ingredients(session: Session) -> None:
    entries = _load("ingredients.json")
    rows = {}
    for entry in entries:
        row = _existing_row(session, Ingredient, entry["name_zh"])
        if row is None:
            row = Ingredient(name_zh=entry["name_zh"], source="seed")
            session.add(row)
        row.name_en = entry["name_en"]
        row.aliases_zh = entry.get("aliases_zh", [])
        row.aliases_en = entry.get("aliases_en", [])
        row.category = entry["category"]
        row.stock_mode = entry["stock_mode"]
        row.allergens = entry["allergens"]
        rows[entry["name_zh"]] = row
    session.flush()
    for entry in entries:
        parent = entry.get("parent")
        rows[entry["name_zh"]].parent_id = rows[parent].id if parent else None


def _seed_cookware(session: Session) -> None:
    for entry in _load("cookware.json"):
        row = _existing_row(session, Cookware, entry["name_zh"])
        if row is None:
            row = Cookware(name_zh=entry["name_zh"], source="seed")
            session.add(row)
        row.name_en = entry["name_en"]
        row.aliases_zh = entry.get("aliases_zh", [])
        row.aliases_en = entry.get("aliases_en", [])


def _seed_always_in_stock(session: Session) -> None:
    names = _load("always_in_stock.json")
    query = select(Ingredient.id).where(Ingredient.name_zh.in_(names))
    session.add_all(AlwaysInStock(ingredient_id=row_id) for row_id in session.scalars(query))


def ensure_self_diner(session: Session) -> Diner:
    diner = session.scalars(select(Diner).where(Diner.is_self)).one_or_none()
    if diner is None:
        diner = Diner(display_name="Me", is_self=True)
        session.add(diner)
        session.flush()
    return diner


def seed_all(session: Session) -> None:
    """Load dictionaries and defaults. Safe to rerun: user rows and choices are kept."""
    first_run = not session.scalar(select(exists().where(Ingredient.source == "seed")))
    _seed_ingredients(session)
    _seed_cookware(session)
    session.flush()
    # Only on the first run, so items the user removed are not restored.
    if first_run:
        _seed_always_in_stock(session)
    ensure_self_diner(session)


def main() -> None:
    configure_engine(get_settings().database_url)
    with SessionFactory.begin() as session:
        seed_all(session)


if __name__ == "__main__":
    main()
