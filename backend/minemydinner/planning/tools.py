"""LangChain tools the planner calls. Each tool opens its own session from the run's
dependencies and returns a JSON-serialisable dict: diner codes only, no person names and
no recipe source text. The hard exclusions are enforced in code; these descriptions only
tell the model what code already does."""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from minemydinner.constants import CATEGORIES, STOCK_LEVELS
from minemydinner.llm.client import LLMClient
from minemydinner.models import Recipe
from minemydinner.planning.context import MealConstraints, load_planning_context
from minemydinner.planning.inventory import (
    InventoryChange,
    inventory_snapshot,
    meal_preferences,
    update_inventory,
)
from minemydinner.planning.search import recipe_detail, search_recipes
from minemydinner.planning.stock import compute_missing as stock_compute_missing
from minemydinner.planning.stock import missing_cookware

logger = logging.getLogger(__name__)


@dataclass
class PlannerDeps:
    """The agent's runtime context (`context_schema=PlannerDeps`)."""

    session_factory: sessionmaker[Session]
    client: LLMClient
    constraints: MealConstraints
    mode: str


def _default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    raise TypeError(f"not serialisable: {type(value)}")


def _plain(value: Any) -> Any:
    """Turn a result into JSON-safe values (dates become ISO strings)."""
    return json.loads(json.dumps(value, default=_default))


Category = Literal[CATEGORIES]  # type: ignore[valid-type]
StockLevel = Literal[STOCK_LEVELS]  # type: ignore[valid-type]
RecipeIds = Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1, max_length=20)]


class GetInventoryArgs(BaseModel):
    category: Category | None = Field(
        default=None, description="Only ingredients of this category, or omit for all."
    )
    sort_by_expiry: bool = Field(
        default=False, description="Soonest expiry first; items without a date last."
    )


class SearchRecipesArgs(BaseModel):
    query: str | None = Field(
        default=None, max_length=200, description="Free-text query matched by meaning."
    )
    max_time: int | None = Field(
        default=None,
        ge=1,
        le=1440,
        description="Maximum cooking minutes; recipes with no time stay.",
    )
    tags: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Preferred characteristics. Pass only when the user names one.",
    )
    must_use: list[str] | None = Field(
        default=None, max_length=10, description="Ingredient names the recipes must use."
    )
    exclude_recent_days: int | None = Field(
        default=None,
        ge=1,
        le=60,
        description="Skip dishes cooked in the last N days. Set only when the user asks.",
    )


class GetRecipeArgs(BaseModel):
    recipe_id: int = Field(ge=1, description="Recipe id from a search result.")


class RecipeIdsArgs(BaseModel):
    recipe_ids: RecipeIds = Field(description="Recipe ids to check, 1 to 20.")


class InventoryChangeArg(BaseModel):
    name: str
    action: Literal["set", "remove"]
    count: int | None = Field(default=None, ge=0)
    level: StockLevel | None = None
    grams: float | None = Field(default=None, ge=0)
    expires_on: date | None = None


class UpdateInventoryArgs(BaseModel):
    changes: list[InventoryChangeArg] = Field(
        min_length=1, max_length=30, description="Stock changes to apply, 1 to 30."
    )


def _run(deps: PlannerDeps, body: Callable[[Session], dict]) -> dict:
    """Run a tool body in its own session. Any failure rolls back, is logged, and becomes
    an error payload with no exception text, so one bad call never aborts the agent."""
    try:
        with deps.session_factory() as session:
            try:
                return _plain(body(session))
            except Exception:
                session.rollback()
                raise
    except Exception:
        logger.exception("planner tool failed")
        return {"error": "tool_failed"}


def _known_ids(session: Session, recipe_ids: list[int]) -> tuple[list[int], list[int]]:
    found = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(recipe_ids))))
    unique = list(dict.fromkeys(recipe_ids))
    return [i for i in unique if i in found], [i for i in unique if i not in found]


@tool(
    "get_inventory",
    args_schema=GetInventoryArgs,
    description=(
        "List what is in stock: ingredients with amounts and expiry dates, the always-in-stock "
        "list, and the owned cookware. Optionally filter by category or sort by expiry."
    ),
)
def get_inventory(
    runtime: ToolRuntime[PlannerDeps],
    category: str | None = None,
    sort_by_expiry: bool = False,
) -> dict:
    return _run(runtime.context, lambda s: inventory_snapshot(s, category, sort_by_expiry))


@tool(
    "search_recipes",
    args_schema=SearchRecipesArgs,
    description=(
        "Search the recipe library. Dishes the selected diners cannot eat are already "
        "excluded by code; you cannot include them. tags are a preference that ranks "
        "matching dishes first, never a filter: pass tags only when the user names a "
        "characteristic. Set exclude_recent_days only when the user asks to avoid recent "
        "dishes. Returns hits, left_out_unknown_allergens (titles of dishes left out because "
        "an ingredient could not be checked), semantic_paused (true when meaning-based "
        "search was unavailable), and unresolved_must_use (must_use names the dictionary "
        "does not know)."
    ),
)
def search_recipes_tool(
    runtime: ToolRuntime[PlannerDeps],
    query: str | None = None,
    max_time: int | None = None,
    tags: list[str] | None = None,
    must_use: list[str] | None = None,
    exclude_recent_days: int | None = None,
) -> dict:
    deps = runtime.context

    def body(session: Session) -> dict:
        ctx = load_planning_context(session, deps.constraints)
        result = search_recipes(
            session,
            deps.client,
            ctx,
            query=query,
            max_time=max_time,
            tags=tags,
            must_use=must_use,
            exclude_recent_days=exclude_recent_days,
        )
        return {
            "hits": result.hits,
            "left_out_unknown_allergens": [e.title for e in result.left_out_unknown_allergens],
            "semantic_paused": result.semantic_paused,
            "unresolved_must_use": result.unresolved_must_use,
        }

    return _run(deps, body)


@tool(
    "get_recipe",
    args_schema=GetRecipeArgs,
    description=(
        "Read one recipe: lines, cookware, tags, and a short steps summary. Returns "
        '{"error": "not_found"} for an unknown id.'
    ),
)
def get_recipe(runtime: ToolRuntime[PlannerDeps], recipe_id: int) -> dict:
    def body(session: Session) -> dict:
        detail = recipe_detail(session, recipe_id)
        return {"error": "not_found"} if detail is None else detail

    return _run(runtime.context, body)


@tool(
    "compute_missing",
    args_schema=RecipeIdsArgs,
    description=(
        "For each existing recipe id, list the required ingredients that are not in stock. "
        "Optional ingredients are ignored. Ids that do not exist are listed in "
        "unknown_recipe_ids."
    ),
)
def compute_missing(runtime: ToolRuntime[PlannerDeps], recipe_ids: list[int]) -> dict:
    deps = runtime.context

    def body(session: Session) -> dict:
        ctx = load_planning_context(session, deps.constraints)
        known, unknown = _known_ids(session, recipe_ids)
        missing = stock_compute_missing(session, ctx, known)
        return {
            "missing": {
                str(recipe_id): [
                    {
                        "name": line.name,
                        "ingredient_id": line.ingredient_id,
                        "reason": line.reason,
                        "choice_group": line.choice_group,
                    }
                    for line in lines
                ]
                for recipe_id, lines in missing.items()
            },
            "unknown_recipe_ids": unknown,
        }

    return _run(deps, body)


@tool(
    "check_cookware",
    args_schema=RecipeIdsArgs,
    description=(
        "For each existing recipe id, list the cookware the household does not own. Ids that "
        "do not exist are listed in unknown_recipe_ids."
    ),
)
def check_cookware(runtime: ToolRuntime[PlannerDeps], recipe_ids: list[int]) -> dict:
    deps = runtime.context

    def body(session: Session) -> dict:
        ctx = load_planning_context(session, deps.constraints)
        known, unknown = _known_ids(session, recipe_ids)
        missing = missing_cookware(session, ctx, known)
        return {
            "missing_cookware": {
                str(recipe_id): [{"id": item_id, "name": name} for item_id, name in items]
                for recipe_id, items in missing.items()
            },
            "unknown_recipe_ids": unknown,
        }

    return _run(deps, body)


@tool(
    "get_preferences",
    description=(
        "The dietary limits of the selected diners for this meal, under anonymous codes "
        "such as Diner 1. Dishes they cannot eat are already excluded by code."
    ),
)
def get_preferences(runtime: ToolRuntime[PlannerDeps]) -> dict:
    deps = runtime.context

    def body(session: Session) -> dict:
        ctx = load_planning_context(session, deps.constraints)
        return {"preferences": meal_preferences(session, ctx)}

    return _run(deps, body)


@tool(
    "update_inventory",
    args_schema=UpdateInventoryArgs,
    description=(
        "Apply stock changes. Unknown names are returned in unmatched and never created; "
        "changes that do not fit the ingredient's stock mode are returned in rejected."
    ),
)
def update_inventory_tool(
    runtime: ToolRuntime[PlannerDeps], changes: list[InventoryChangeArg]
) -> dict:
    parsed = [
        InventoryChange(**(c.model_dump() if isinstance(c, BaseModel) else c)) for c in changes
    ]

    def body(session: Session) -> dict:
        result = update_inventory(session, parsed)
        session.commit()
        return result

    return _run(runtime.context, body)


PLANNER_TOOLS: list[BaseTool] = [
    get_inventory,
    search_recipes_tool,
    get_recipe,
    compute_missing,
    check_cookware,
    get_preferences,
]
INVENTORY_TOOLS: list[BaseTool] = [update_inventory_tool]


def tools_version(tools: list[BaseTool]) -> str:
    """The first 12 hex characters of SHA-256 over each tool's name, description, and
    model-facing argument schema, so a changed tool changes the version."""
    spec = [
        {
            "name": item.name,
            "description": item.description,
            "args": item.tool_call_schema.model_json_schema(),
        }
        for item in tools
    ]
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


TOOLS_VERSION = tools_version(PLANNER_TOOLS)
