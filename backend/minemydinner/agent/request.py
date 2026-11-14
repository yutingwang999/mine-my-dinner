"""The parsed planning request, its defaults (design 6.2), and the request-style preference:
ask first, or plan then adjust."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from minemydinner.models import AppPreference
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.validate import MAX_TOTAL_COUNT, PlanSpec

PROMPT_VERSION = "plan_v2"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / PROMPT_VERSION

REQUEST_STYLE_KEY = "request_style"
REQUEST_STYLES = ("ask_first", "plan_then_adjust")

# Every count the user can state. "dishes" is meat plus vegetable dishes (design 5.1).
COUNT_FIELDS = (
    "dishes",
    "soups",
    "meat_dishes",
    "vegetable_dishes",
    "staples",
    "desserts",
    "snacks",
)
CountField = Literal[
    "dishes", "soups", "meat_dishes", "vegetable_dishes", "staples", "desserts", "snacks"
]


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
        default=None,
        ge=0,
        description=(
            "Total number of meat plus vegetable dishes, if stated. Soups, staples, desserts "
            "and snacks are not part of it. Leave null when the user gives only a meat or "
            "vegetable count without a total."
        ),
    )
    soups: int | None = Field(default=None, ge=0, description="Number of soups, if stated.")
    meat_dishes: int | None = Field(
        default=None, ge=0, description="Number of meat dishes, if the user names it."
    )
    vegetable_dishes: int | None = Field(
        default=None, ge=0, description="Number of vegetable dishes, if the user names it."
    )
    staples: int | None = Field(
        default=None,
        ge=0,
        description="Number of staple foods (rice, noodles, fried rice, buns), if stated.",
    )
    desserts: int | None = Field(default=None, ge=0, description="Number of desserts, if stated.")
    snacks: int | None = Field(default=None, ge=0, description="Number of snacks, if stated.")
    range_fields: list[CountField] = Field(
        default_factory=list,
        description=(
            "Count fields filled from the lower end of a range the user gave "
            "(\"two or three dishes\" gives dishes 2 and lists dishes here)."
        ),
    )
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
        default_factory=list,
        max_length=10,
        description=(
            "Ingredients the user insists on using in the meal: at least one dish must use "
            "each of them."
        ),
    )
    prefer_use: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Ingredients the user would like to use up or prefers; only a preference.",
    )
    all_use: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Ingredients that every dish of the meal must use.",
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


def _dish_total(parsed: ParsedRequest) -> int:
    """The dish total: the stated one, else the sum of the named meat and vegetable counts."""
    if parsed.dishes is not None:
        return parsed.dishes
    return (parsed.meat_dishes or 0) + (parsed.vegetable_dishes or 0)


def request_problem(parsed: ParsedRequest) -> Literal["split_exceeds_total", "too_many"] | None:
    """A contradiction in the stated counts, which is always asked about (design 5.1)."""
    split = (parsed.meat_dishes or 0) + (parsed.vegetable_dishes or 0)
    if parsed.dishes is not None and split > parsed.dishes:
        return "split_exceeds_total"
    total = (
        _dish_total(parsed) + (parsed.soups or 0) + (parsed.staples or 0)
        + (parsed.desserts or 0) + (parsed.snacks or 0)
    )
    if total > MAX_TOTAL_COUNT:
        return "too_many"
    return None


def _stated_counts(parsed: ParsedRequest) -> bool:
    return any(getattr(parsed, name) is not None for name in COUNT_FIELDS)


def apply_defaults(
    parsed: ParsedRequest, self_diner_id: int | None = None
) -> tuple[PlanSpec, MealConstraints, list[str]]:
    """Fill unstated details (design 5.1). Returns the spec, the meal constraints, and the
    assumed fields: "headcount", the count fields in COUNT_FIELDS order, and "mode".

    Dishes and soups come from the headcount only when no count at all was stated; then an
    unstated count is 0. A range taken at its lower end is reported as assumed too.

    Only the self diner's rules are known in this plan; everyone else counts as an
    unconfirmed diner until diner selection exists, so S8 warns about them.
    """
    assumed: set[str] = {
        name for name in parsed.range_fields if getattr(parsed, name) is not None
    }
    headcount = parsed.headcount
    if headcount is None:
        headcount = 1
        assumed.add("headcount")
    dishes = _dish_total(parsed)
    soups = parsed.soups or 0
    staples, desserts, snacks = parsed.staples or 0, parsed.desserts or 0, parsed.snacks or 0
    meat_dishes, vegetable_dishes = parsed.meat_dishes, parsed.vegetable_dishes
    dishes_stated = parsed.dishes
    if not _stated_counts(parsed) or dishes + soups + staples + desserts + snacks == 0:
        # Nothing was stated, or the user stated only zeros: a meal needs at least one dish.
        dishes, soups = default_composition(headcount)
        staples = desserts = snacks = 0
        # The stated zeros are dropped with the rest, so no split rides on the default total.
        meat_dishes = vegetable_dishes = dishes_stated = None
        assumed.update(("dishes", "soups"))
    split = (meat_dishes or 0) + (vegetable_dishes or 0)
    named_split_below_total = (
        dishes_stated is not None
        and meat_dishes is not None
        and vegetable_dishes is not None
        and split < dishes_stated
    )
    mode = parsed.mode
    if mode is None:
        mode = "fridge_only"
        assumed.add("mode")
    ordered = ["headcount", *COUNT_FIELDS, "mode"]
    defaults_used = [name for name in ordered if name in assumed]
    spec = PlanSpec(
        mode=mode,
        dishes=dishes,
        soups=soups,
        time_limit_min=parsed.time_limit_min,
        meat_dishes=meat_dishes,
        vegetable_dishes=vegetable_dishes,
        split_is_minimum=named_split_below_total,
        staples=staples,
        desserts=desserts,
        snacks=snacks,
        must_use=parsed.must_use,
        all_use=parsed.all_use,
        prefer_use=parsed.prefer_use,
    )
    constraints = MealConstraints(
        diner_ids=(self_diner_id,) if self_diner_id is not None else (),
        unconfirmed_diners=headcount - 1,
    )
    return spec, constraints, defaults_used


def needs_question(parsed: ParsedRequest, style: str | None) -> list[str]:
    """With "ask first", the unstated details to ask about: the mode, and the composition
    when no count was stated."""
    if style != "ask_first":
        return []
    fields = []
    if parsed.mode is None:
        fields.append("mode")
    if not _stated_counts(parsed):
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
