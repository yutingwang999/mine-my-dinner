"""The parsed planning request, its defaults (design 6.2), and the request-style preference:
ask first, or plan then adjust."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from minemydinner.models import AppPreference
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.validate import PlanSpec

PROMPT_VERSION = "plan_v1"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / PROMPT_VERSION

REQUEST_STYLE_KEY = "request_style"
REQUEST_STYLES = ("ask_first", "plan_then_adjust")


def load_prompt(name: str) -> str:
    """A prompt file of the current planning prompt version, such as "parse.md"."""
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


class ParsedRequest(BaseModel):
    """What the user asked for in this conversation. A null field was not stated."""

    others_mentioned: bool = Field(
        default=False, description="True when the user says anyone else is eating."
    )
    headcount: int | None = Field(
        default=None, ge=1, description="How many people eat, the user included, if stated."
    )
    dishes: int | None = Field(
        default=None, ge=0, description="Number of meat or vegetable dishes, if stated."
    )
    soups: int | None = Field(default=None, ge=0, description="Number of soups, if stated.")
    mode: Literal["fridge_only", "shopping"] | None = Field(
        default=None,
        description="fridge_only to cook only from what is at home; shopping when buying is fine.",
    )
    time_limit_min: int | None = Field(
        default=None, ge=1, description="Cooking time limit in minutes, if stated."
    )
    query: str | None = Field(
        default=None, description="What the user feels like eating, in their own words."
    )
    must_use: list[str] = Field(
        default_factory=list, description="Ingredients the user wants to use up."
    )
    exclude_recent_days: int | None = Field(
        default=None, ge=1, description="Avoid dishes cooked in the last N days, only if asked."
    )


def default_composition(headcount: int) -> tuple[int, int]:
    """(dishes, soups) by headcount: 1 -> 1+0, 2 -> 2+0, 3-4 -> 2+1, 5 or more -> 3+1."""
    if headcount <= 1:
        return 1, 0
    if headcount == 2:
        return 2, 0
    if headcount <= 4:
        return 2, 1
    return 3, 1


def apply_defaults(
    parsed: ParsedRequest, self_diner_id: int | None = None
) -> tuple[PlanSpec, MealConstraints, list[str]]:
    """Fill unstated details. Returns the spec, the meal constraints, and the defaulted
    fields among "headcount", "composition", and "mode", in that order.

    Only the self diner's rules are known in this plan; everyone else counts as an
    unconfirmed diner until diner selection exists, so S8 warns about them.
    """
    defaults_used: list[str] = []
    headcount = parsed.headcount
    if headcount is None:
        headcount = 1
        defaults_used.append("headcount")
    dishes, soups = parsed.dishes, parsed.soups
    if dishes is None or soups is None:
        default_dishes, default_soups = default_composition(headcount)
        dishes = default_dishes if dishes is None else dishes
        soups = default_soups if soups is None else soups
        defaults_used.append("composition")
    mode = parsed.mode
    if mode is None:
        mode = "fridge_only"
        defaults_used.append("mode")
    spec = PlanSpec(mode=mode, dishes=dishes, soups=soups, time_limit_min=parsed.time_limit_min)
    constraints = MealConstraints(
        diner_ids=(self_diner_id,) if self_diner_id is not None else (),
        unconfirmed_diners=headcount - 1,
    )
    return spec, constraints, defaults_used


def needs_question(parsed: ParsedRequest, style: str | None) -> list[str]:
    """With "ask first", the unstated details to ask about: the mode and the dish count."""
    if style != "ask_first":
        return []
    fields = []
    if parsed.mode is None:
        fields.append("mode")
    if parsed.dishes is None:
        fields.append("composition")
    return fields


def get_request_style(session: Session) -> str | None:
    row = session.get(AppPreference, REQUEST_STYLE_KEY)
    return row.value if row else None


def set_request_style(session: Session, value: str | None) -> None:
    if value is not None and value not in REQUEST_STYLES:
        raise ValueError(f"Unknown request style {value}")
    statement = insert(AppPreference).values(key=REQUEST_STYLE_KEY, value=value)
    # A JSON null must be stored as JSON, not as SQL NULL.
    statement = statement.on_conflict_do_update(
        index_elements=["key"], set_={"value": statement.excluded.value}
    )
    session.execute(statement)
    session.commit()
