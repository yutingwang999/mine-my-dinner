"""The parsed planning request, its defaults (design 6.2), and the request-style preference:
ask first, or plan then adjust."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from minemydinner.constants import ALLERGENS
from minemydinner.models import AppPreference
from minemydinner.planning.composition import Bound, ranges_for
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.validate import MAX_TOTAL_COUNT, PlanSpec

# One version per prompt in use, so route, parse, planner, the follow-up reader and the dish
# picker change independently. A version any record has used is never edited; a change makes
# the next version of that prompt. The revise prompts (revise_v1..revise_v4) stay as files,
# the record of earlier runs; no step loads them since plan 3b-2.
PROMPT_VERSIONS = {
    "route": "route_v6",
    "parse": "parse_v8",
    "planner": "planner_v5",
    "follow_up": "follow_up_v4",
    "pick": "pick_v4",
}
PROMPT_VERSION = "+".join(PROMPT_VERSIONS.values())
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# The fixed message of "plan again" (spec 5.7); code reuses the previous parsed request for it.
REPLAN_MESSAGE = {"zh": "按同样的要求重新规划", "en": "Plan again with the same request"}

# The fixed messages of the plan card's next-action buttons, by locale and action. They must
# equal chat.actionMessage in the frontend message files (a test checks it). Code skips the
# route call for them and changes the request of the card that offered the button itself
# (agent/actions.py, plan 3b-1b spec 5.5); only a card saved without its request is parsed.
ACTION_MESSAGES = {
    "zh": {
        "switch_to_shopping": "允许购物",
        "fewer_dishes": "少做几道菜",
        "drop_time_limit": "不限时间",
        "relax_split": "荤素都行",
        "relax_named": "不一定要用指定的食材",
    },
    "en": {
        "switch_to_shopping": "Allow shopping",
        "fewer_dishes": "Fewer dishes",
        "drop_time_limit": "No time limit",
        "relax_split": "Any mix of meat and vegetable dishes is fine",
        "relax_named": "The named ingredients are optional",
    },
}

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


def prompt_files() -> list[Path]:
    """The prompt files in use, in the order of PROMPT_VERSIONS."""
    return [PROMPT_DIR / f"{version}.md" for version in PROMPT_VERSIONS.values()]


def load_prompt(
    kind: Literal["route", "parse", "planner", "follow_up", "pick"],
) -> str:
    """The current version of one prompt."""
    return (PROMPT_DIR / f"{PROMPT_VERSIONS[kind]}.md").read_text(encoding="utf-8")


class AvoidItem(BaseModel):
    """A named ingredient or ingredient group a person avoids."""

    name: str = Field(
        min_length=1, max_length=40, description="The ingredient or group as the user said it."
    )
    kind: Literal["restriction", "dislike"] = Field(
        description=(
            "restriction for doesn't eat, can't eat, or an allergy outside the allergen list; "
            "dislike for doesn't like."
        )
    )


class StatedRules(BaseModel):
    """Rules the user stated in the chat for one person. Nothing here applies before the
    diner card is confirmed."""

    allergens: list[Literal[ALLERGENS]] = Field(  # type: ignore[valid-type]
        default_factory=list,
        description="Allergies from the fixed list only; any other allergy is an avoid item.",
    )
    avoid: list[AvoidItem] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Named ingredients or ingredient groups only. Taste preferences (spicy, light) go "
            "to query and diet words go to diet_words, never here."
        ),
    )
    diet_words: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Diet words as said (\"吃素\", \"vegetarian\", \"vegan\"), never in avoid.",
    )

    @field_validator("allergens", mode="before")
    @classmethod
    def _known_allergens(cls, value):
        # An allergen outside the fixed list is dropped, not a failure of the whole parse.
        if isinstance(value, list):
            return [item for item in value if item in ALLERGENS]
        return value


class StatedGuest(StatedRules):
    """Someone eating who is not a saved friend (saved friends appear as "Diner N")."""

    name: str | None = Field(
        default=None, description="The person as the user named them, or null when unnamed."
    )


class StatedFriendRules(StatedRules):
    """A new rule the user stated for a saved friend."""

    code: str = Field(
        description="The friend's label as it appears in the chat, such as \"Diner 2\"."
    )


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
    meat_bound: Bound | None = Field(
        default=None,
        description=(
            "How meat_dishes binds: exact for a plain number, at_least or at_most when the "
            "user says so. Null when meat_dishes is null."
        ),
    )
    vegetable_bound: Bound | None = Field(
        default=None,
        description=(
            "How vegetable_dishes binds: exact for a plain number, at_least or at_most when "
            "the user says so. Null when vegetable_dishes is null."
        ),
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
    # Plan 3b-1b spec 5.4. Code applies it to this meal as the meal's own rule (diner key
    # "meal"), never to a profile; requests stored before parse_v7 load it as empty.
    meal_avoid: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Ingredients or ingredient groups the user does not want at this meal only "
            "(\"今天不想吃肉\" is [\"肉\"]), as said. Never taste words (\"不要辣的\" goes to "
            "query) and never a lasting rule of a person (\"我不吃肉\" goes to self_rules)."
        ),
    )
    # None on requests parsed before parse_v6; a revision then compares counts with the
    # previous request instead (agent/revision.py).
    counts_stated_now: bool | None = Field(
        default=None,
        description=(
            "True when the newest user message itself states or changes a count of any "
            "category, as a number (\"4个菜就够了\") or relative to the plan (\"再加一道吧\"). "
            "False when the newest message states no count, even if the count fields carry "
            "counts from earlier messages. The app note about the current plan never makes "
            "it true."
        ),
    )
    guests: list[StatedGuest] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "People eating who are named or described but are not saved friends (never a "
            "\"Diner N\" label), each with the allergies, avoided ingredients, and diet words "
            "the user stated. Taste preferences go to query, never to avoid."
        ),
    )
    friend_rules: list[StatedFriendRules] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "New allergies, avoided ingredients, or diet words the user states for a saved "
            "friend, who appears as a \"Diner N\" label. Taste preferences go to query."
        ),
    )
    self_rules: StatedRules | None = Field(
        default=None,
        description=(
            "Allergies, avoided ingredients, or diet words the user states for themselves; "
            "null when none. Taste preferences go to query, never to avoid."
        ),
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


def _bound(count: int | None, bound: Bound | None) -> Bound | None:
    """A named side's bound: a count with no bound is exact; no count has no bound."""
    if count is None:
        return None
    return bound or "exact"


def _has_bound_side(parsed: ParsedRequest) -> bool:
    return any(
        _bound(count, bound) in ("at_least", "at_most")
        for count, bound in (
            (parsed.meat_dishes, parsed.meat_bound),
            (parsed.vegetable_dishes, parsed.vegetable_bound),
        )
    )


def dish_total(parsed: ParsedRequest, headcount: int) -> int:
    """The dish total (design 5.11): the stated one; with no total and a side named "at
    least" or "at most", the headcount default, raised to the sum of the lower bounds and,
    when both sides have an upper bound (exact or at most), lowered to the sum of the upper
    bounds; otherwise the sum of the named counts ("一荤一素" is 2).

    The total is never lowered to 0: with both sides at most 0 it stays at the default, so
    request_problem reports the empty ranges as a contradiction."""
    if parsed.dishes is not None:
        return parsed.dishes
    if _has_bound_side(parsed):
        sides = [
            (count, _bound(count, bound))
            for count, bound in (
                (parsed.meat_dishes, parsed.meat_bound),
                (parsed.vegetable_dishes, parsed.vegetable_bound),
            )
        ]
        lower = sum(count for count, bound in sides if count is not None and bound != "at_most")
        total = max(default_composition(headcount)[0], lower)
        if all(count is not None and bound != "at_least" for count, bound in sides):
            upper = sum(count for count, _ in sides)
            if upper >= 1:
                total = min(total, upper)
        return total
    return (parsed.meat_dishes or 0) + (parsed.vegetable_dishes or 0)


def _parsed_ranges(parsed: ParsedRequest, total: int) -> dict[str, tuple[int, int]]:
    return ranges_for(
        total,
        parsed.meat_dishes,
        _bound(parsed.meat_dishes, parsed.meat_bound),
        parsed.vegetable_dishes,
        _bound(parsed.vegetable_dishes, parsed.vegetable_bound),
    )


def request_problem(
    parsed: ParsedRequest,
) -> Literal["split_conflicts_total", "too_many"] | None:
    """A contradiction in the stated counts, which is always asked about (design 5.1, 5.11):
    a meat or vegetable range that comes out empty against the dish total, or more than
    MAX_TOTAL_COUNT items in total."""
    total = dish_total(parsed, parsed.headcount or 1)
    if any(low > high for low, high in _parsed_ranges(parsed, total).values()):
        return "split_conflicts_total"
    everything = (
        total + (parsed.soups or 0) + (parsed.staples or 0)
        + (parsed.desserts or 0) + (parsed.snacks or 0)
    )
    if everything > MAX_TOTAL_COUNT:
        return "too_many"
    return None


def _stated_counts(parsed: ParsedRequest) -> bool:
    return any(getattr(parsed, name) is not None for name in COUNT_FIELDS)


def apply_defaults(
    parsed: ParsedRequest, self_diner_id: int | None = None, headcount: int | None = None
) -> tuple[PlanSpec, MealConstraints, list[str], int]:
    """Fill unstated details (design 5.1). Returns the spec, the meal constraints, the
    assumed fields ("headcount", the count fields in COUNT_FIELDS order, and "mode"), and the
    headcount.

    A given `headcount` (the confirmed diner card's) wins over the parse's and is not
    assumed; the caller then replaces the constraints with the card's.

    Dishes and soups come from the headcount only when no count at all was stated; then an
    unstated count is 0. A range taken at its lower end is reported as assumed too, and so
    is a dish total taken from the headcount for a side named "at least" or "at most".

    Without the card, only the self diner's rules are known; everyone else counts as an
    unconfirmed diner, so S8 warns about them.
    """
    assumed: set[str] = {
        name for name in parsed.range_fields if getattr(parsed, name) is not None
    }
    if headcount is None:
        headcount = parsed.headcount
    if headcount is None:
        headcount = 1
        assumed.add("headcount")
    dishes = dish_total(parsed, headcount)
    if parsed.dishes is None and _has_bound_side(parsed):
        # The total came from the headcount (design 5.11): it is assumed, soups are not added.
        assumed.add("dishes")
    soups = parsed.soups or 0
    staples, desserts, snacks = parsed.staples or 0, parsed.desserts or 0, parsed.snacks or 0
    meat_dishes, vegetable_dishes = parsed.meat_dishes, parsed.vegetable_dishes
    meat_bound = _bound(meat_dishes, parsed.meat_bound)
    vegetable_bound = _bound(vegetable_dishes, parsed.vegetable_bound)
    if not _stated_counts(parsed) or dishes + soups + staples + desserts + snacks == 0:
        # Nothing was stated, or the user stated only zeros: a meal needs at least one dish.
        dishes, soups = default_composition(headcount)
        staples = desserts = snacks = 0
        # The stated zeros are dropped with the rest, so no split rides on the default total.
        meat_dishes = vegetable_dishes = meat_bound = vegetable_bound = None
        assumed.update(("dishes", "soups"))
    if (
        meat_dishes is not None
        and vegetable_dishes is not None
        and meat_bound == vegetable_bound == "exact"
        and meat_dishes + vegetable_dishes < dishes
    ):
        # Both sides named exactly below the total: the total wins, the sides are minimums.
        meat_bound = vegetable_bound = "at_least"
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
        meat_bound=meat_bound,
        vegetable_bound=vegetable_bound,
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
    return spec, constraints, defaults_used, headcount


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
