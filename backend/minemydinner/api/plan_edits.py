"""Manual edits of the newest plan card (plan 3a spec 5.5-5.6): search the library from the
card, remove a dish, add one (after confirming its conflicts), keep a dish that gained a
conflict, and restore the model's plan.

An edit reads the thread's checkpointed state, applies `apply_edit` (code only, no model
call), and writes the result back as if `present_plan` had produced it, so the thread's next
step stays the end. No chat message is added, and the planning record is not touched.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from minemydinner.agent.edits import (
    Add,
    Conflict,
    EditError,
    Keep,
    Remove,
    Restore,
    apply_edit,
    newest_reply_id,
    search_markers,
)
from minemydinner.agent.labels import LabelTable, labels_scope, load_label_context
from minemydinner.agent.plan_card import client_card, meal_constraints
from minemydinner.api.chat import RuntimeDep, _snapshot, edit_refusal
from minemydinner.api.deps import ApiError
from minemydinner.api.recipe_match import recipe_text_match, title_contains
from minemydinner.db import get_session
from minemydinner.models import Diner, Recipe
from minemydinner.planning.context import load_planning_context
from minemydinner.planning.dietary import check_recipes
from minemydinner.planning.search import search_recipes

router = APIRouter(prefix="/api/chat")

SessionDep = Annotated[Session, Depends(get_session)]
Locale = Literal["zh", "en"]

# The most results the card's search shows.
SEARCH_LIMIT = 10

ERROR_STATUS = {
    "already_in_plan": 422,
    "not_in_plan": 422,
    "not_edited": 409,
    "no_plan": 409,
    "thread_busy": 409,
    "not_found": 404,
}

# An edit reads the state and writes it back; two edits of one thread must not interleave,
# or the later write would drop the earlier edit. Edits are short, so one lock serves all.
_edit_lock = threading.Lock()


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    locale: Locale

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class RemoveBody(BaseModel):
    recipe_id: int
    locale: Locale


class ConfirmBody(BaseModel):
    recipe_id: int
    confirm: list[Conflict] = Field(default_factory=list, max_length=100)
    locale: Locale


class RestoreBody(BaseModel):
    locale: Locale


def _editable(runtime, thread_id: str) -> tuple[dict, Any, dict]:
    """The thread's config, state and newest plan card; refused by `edit_refusal`'s rule
    (the same one `GET /threads/{id}` reports as `editable`)."""
    config, state = _snapshot(runtime, thread_id)
    refusal = edit_refusal(state)
    if refusal is not None:
        raise ApiError(ERROR_STATUS[refusal], refusal)
    return config, state, state.values["reply_plans"][newest_reply_id(state.values)]


@contextmanager
def _thread_labels(session: Session, values: dict, locale: str) -> Iterator[None]:
    """The thread's label table as the current label context (plan 2b), as an edit's card
    text is built. The table is not saved back."""
    self_id = session.scalar(select(Diner.id).where(Diner.is_self))
    table = LabelTable.from_state(values.get("diner_labels"), self_id)
    with labels_scope(load_label_context(session, table, locale)):
        yield


def _edit(
    runtime, session: Session, thread_id: str, action: Any, locale: str
) -> dict[str, Any]:
    with _edit_lock:
        config, state, _ = _editable(runtime, thread_id)
        values = state.values
        try:
            with _thread_labels(session, values, locale):
                ctx = load_planning_context(session, meal_constraints(values))
                outcome, update = apply_edit(session, ctx, values, action, locale)
        except EditError as error:
            raise ApiError(ERROR_STATUS[error.code], error.code) from None
        if outcome.plan is None:
            conflicts = [c.model_dump() for c in outcome.needs_confirmation]
            raise ApiError(409, "needs_confirmation", extra={"conflicts": conflicts})
        # Written as present_plan's output: the thread's next step stays the end.
        runtime.graph.update_state(config, update, as_node="present_plan")
    return {"plan": client_card(outcome.plan)}


@router.post("/threads/{thread_id}/plan/search")
def search(
    thread_id: str, body: SearchBody, runtime: RuntimeDep, session: SessionDep
) -> dict[str, Any]:
    """A plain text match, as on the library page (no model call): only recipes whose title,
    tags, lines, or ingredient names contain the query. Title matches first, then by title."""
    _, state, plan = _editable(runtime, thread_id)
    values = state.values
    in_plan = {r["id"] for r in plan.get("recipes") or []}
    matched = list(
        session.scalars(
            select(Recipe.id)
            .where(recipe_text_match(body.query))
            .order_by(case((title_contains(body.query), 0), else_=1), Recipe.title, Recipe.id)
            .limit(SEARCH_LIMIT)
        )
    )
    if not matched:
        return {"hits": []}
    ctx = load_planning_context(session, meal_constraints(values))
    found = search_recipes(
        session, None, ctx, limit=None, show_blocked=True, only_ids=matched
    ).hits
    order = {recipe_id: index for index, recipe_id in enumerate(matched)}
    hits = sorted(found, key=lambda hit: order[hit.id])
    dislikes = check_recipes(session, ctx.rules, [hit.id for hit in hits]).dislikes
    markers = search_markers(session, values, hits, dislikes, body.locale)
    return {
        "hits": [
            {
                "id": hit.id,
                "title": hit.title,
                "dish_type": hit.dish_type,
                "time_min": hit.time_min,
                "missing": hit.missing,
                "missing_cookware": hit.missing_cookware,
                "conflicts": markers[hit.id]["conflicts"],
                "blocked_unknown": hit.blocked_unknown,
                "dislikes": markers[hit.id]["dislikes"],
                "in_plan": hit.id in in_plan,
            }
            for hit in hits
        ],
    }


@router.post("/threads/{thread_id}/plan/remove")
def remove(
    thread_id: str, body: RemoveBody, runtime: RuntimeDep, session: SessionDep
) -> dict[str, Any]:
    return _edit(runtime, session, thread_id, Remove(recipe_id=body.recipe_id), body.locale)


@router.post("/threads/{thread_id}/plan/add")
def add(
    thread_id: str, body: ConfirmBody, runtime: RuntimeDep, session: SessionDep
) -> dict[str, Any]:
    action = Add(recipe_id=body.recipe_id, confirm=body.confirm)
    return _edit(runtime, session, thread_id, action, body.locale)


@router.post("/threads/{thread_id}/plan/keep")
def keep(
    thread_id: str, body: ConfirmBody, runtime: RuntimeDep, session: SessionDep
) -> dict[str, Any]:
    action = Keep(recipe_id=body.recipe_id, confirm=body.confirm)
    return _edit(runtime, session, thread_id, action, body.locale)


@router.post("/threads/{thread_id}/plan/restore")
def restore(
    thread_id: str, body: RestoreBody, runtime: RuntimeDep, session: SessionDep
) -> dict[str, Any]:
    return _edit(runtime, session, thread_id, Restore(), body.locale)
