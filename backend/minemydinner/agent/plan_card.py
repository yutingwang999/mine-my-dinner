"""The plan card's builder, shared by the planning graph's `present_plan` and manual edits,
and the meal constraints of a thread's state."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.card_text import next_actions, unmet_for_user
from minemydinner.models import Diner, Recipe
from minemydinner.planning.context import MealConstraints, PlanningContext, RuleRef
from minemydinner.planning.stock import compute_missing, missing_cookware


def meal_constraints(values: dict) -> MealConstraints:
    """The meal constraints of a thread's state, for `load_planning_context`."""
    data = values["constraints"]
    return MealConstraints(
        diner_ids=tuple(data["diner_ids"]),
        guest_rules=tuple(RuleRef(**rule) for rule in data.get("guest_rules") or []),
        unconfirmed_diners=data["unconfirmed_diners"],
    )


def diners_line(session: Session, settled: dict | None, constraints: dict) -> tuple[list, int]:
    """The plan card's diners line: the user, then saved friends, then guests, by display
    name (shown to the user only, never sent to a model), and the unmatched people count.
    Without a settled set the user alone eats; the others are unconfirmed. A user who
    deselected themselves on the card is left off."""
    self_row = session.scalar(select(Diner).where(Diner.is_self))
    me = {"name": None, "is_self": True, "guest": False}
    if settled is None:
        return [me], int(constraints.get("unconfirmed_diners") or 0)
    settled_ids = settled.get("diner_ids") or []
    # Without a self row the card could not offer deselecting the user.
    eats = self_row is None or self_row.id in settled_ids
    diners = [me] if eats else []
    ids = [i for i in settled_ids if self_row is None or i != self_row.id]
    rows = session.execute(select(Diner.id, Diner.display_name).where(Diner.id.in_(ids)))
    names = {row.id: row.display_name for row in rows}
    diners += [
        {"name": names[i], "is_self": False, "guest": False} for i in ids if i in names
    ]
    diners += [
        {"name": name, "is_self": False, "guest": True}
        for name in (settled.get("guest_names") or {}).values()
    ]
    return diners, int(settled.get("others") or 0)


def card_base(state_values: dict, session: Session) -> dict:
    """The plan card's keys that do not depend on the dishes, from a thread's state."""
    notes = state_values.get("search_notes") or {}
    diners, others = diners_line(
        session, state_values.get("settled_diners"), state_values["constraints"]
    )
    return {
        "defaults_used": state_values["defaults_used"],
        "spec": state_values["spec"],
        "headcount": state_values["constraints"]["headcount"],
        "left_out_unknown_allergens": notes.get("left_out_unknown_allergens", []),
        "left_out_details": notes.get("left_out_details", []),
        "semantic_paused": notes.get("semantic_paused", False),
        "diners": diners,
        "others": others,
        "reasons": [],
    }


def build_dishes_card(
    session: Session,
    ctx: PlanningContext,
    *,
    recipe_ids: list[int],
    validation: dict,
    base: dict,
    locale: str,
) -> dict:
    """The plan card for a set of dishes and its validation result (a dumped
    `ValidationResult`): recipes, what is missing, warnings, servings notes, and, when the
    validation failed, the unmet findings and next actions. `base` keys are copied as is."""
    rows = {r.id: r for r in session.scalars(select(Recipe).where(Recipe.id.in_(recipe_ids)))}
    missing = compute_missing(session, ctx, recipe_ids)
    cookware = missing_cookware(session, ctx, recipe_ids)
    recipes = [
        {
            "id": r.id,
            "title": r.title,
            "dish_type": r.dish_type,
            "time_min": r.time_min,
            "time_source": r.time_source,
            "servings": r.servings,
        }
        for r in (rows[i] for i in recipe_ids)
    ]
    headcount = base["headcount"]
    titles = {r["id"]: r["title"] for r in recipes}
    plan: dict[str, Any] = {
        "recipes": recipes,
        "missing": {str(i): [line.name for line in lines] for i, lines in missing.items() if lines},
        "missing_cookware": {
            str(i): [name for _, name in items] for i, items in cookware.items() if items
        },
        "warnings": validation["warnings"],
        "servings_notes": [
            {"recipe_id": r["id"], "servings": r["servings"], "headcount": headcount}
            for r in recipes
            if r["servings"] is not None and r["servings"] != headcount
        ],
        "unmet": [],
        "next_actions": [],
        **base,
        "passed": bool(validation["passed"]),
    }
    if not validation["passed"]:
        plan["unmet"] = [
            {
                "code": v["code"],
                "recipe_id": v["recipe_id"],
                "text": unmet_for_user(v, titles, locale),
            }
            for v in validation["violations"]
        ]
        plan["next_actions"] = next_actions(
            base["spec"], [], [v["code"] for v in validation["violations"]]
        )
    return plan
