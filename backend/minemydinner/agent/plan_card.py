"""The plan card's builder, shared by the planning graph's `present_plan` and manual edits,
and the meal constraints of a thread's state."""

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.card_text import next_actions, unmet_for_user
from minemydinner.extraction.normalize import match_ingredient
from minemydinner.models import Diner, Ingredient, Recipe
from minemydinner.planning.context import MEAL_KEY, MealConstraints, PlanningContext, RuleRef
from minemydinner.planning.stock import compute_missing, missing_cookware


def meal_constraints(values: dict) -> MealConstraints:
    """The meal constraints of a thread's state, for `load_planning_context`."""
    data = values["constraints"]
    return MealConstraints(
        diner_ids=tuple(data["diner_ids"]),
        guest_rules=tuple(RuleRef(**rule) for rule in data.get("guest_rules") or []),
        unconfirmed_diners=data["unconfirmed_diners"],
        meal_avoid=tuple(data.get("meal_avoid") or ()),
    )


# Card fields kept for the server only: the request the card was built from (its
# `friend_rules` codes are diner labels such as "Diner 2", which are never shown) and whether a
# revision kept the plan. Next-action buttons read both from the stored state (plan 3b-1b).
SERVER_ONLY_CARD_FIELDS = ("request", "plan_kept")


def client_card(card: dict[str, Any] | None) -> dict[str, Any] | None:
    """The card as the client receives it: without the server-only fields."""
    if card is None:
        return None
    return {key: value for key, value in card.items() if key not in SERVER_ONLY_CARD_FIELDS}


# Group words of a one-meal avoidance and the dictionary category each covers ("今天不想吃肉"
# leaves out every meat entry, not only names that contain 肉). Person rules have no groups.
MEAL_GROUP_WORDS = {
    "肉": "meat",
    "肉类": "meat",
    "荤": "meat",
    "荤菜": "meat",
    "meat": "meat",
    "海鲜": "seafood",
    "水产": "seafood",
    "seafood": "seafood",
}


def _group_ingredient_ids(session: Session, category: str) -> list[int]:
    """Every dictionary entry of `category`, and their children, in id order."""
    ids = set(session.scalars(select(Ingredient.id).where(Ingredient.category == category)))
    if ids:
        ids |= set(session.scalars(select(Ingredient.id).where(Ingredient.parent_id.in_(ids))))
    return sorted(ids)


def meal_rules(session: Session, names: list[str]) -> list[RuleRef]:
    """This meal's own rules for a one-meal avoidance (plan 3b-1b spec 5.4), all "dietary
    restriction" under MEAL_KEY: a group word (MEAL_GROUP_WORDS) gives one ingredient rule per
    entry of its category; another name gives the ingredient the dictionary matches (personal
    aliases included) or else a text rule, as a stated restriction becomes."""
    rules: list[RuleRef] = []

    def add(rule: RuleRef) -> None:
        if rule not in rules:
            rules.append(rule)

    for raw in names:
        name = raw.strip()
        if not name:
            continue
        category = MEAL_GROUP_WORDS.get(name.casefold())
        group = _group_ingredient_ids(session, category) if category else []
        for ingredient_id in group:
            add(RuleRef(MEAL_KEY, "dietary restriction", None, ingredient_id, None))
        if group:
            continue
        entry = match_ingredient(session, name)
        add(
            RuleRef(MEAL_KEY, "dietary restriction", None, entry.id, None)
            if entry is not None
            else RuleRef(MEAL_KEY, "dietary restriction", None, None, name)
        )
    return rules


def with_meal_avoid(session: Session, constraints: dict, names: list[str]) -> dict:
    """The state's constraints with this meal's own rules replaced by those of `names`; the
    names are kept as `meal_avoid`, which the plan card carries so a later revision knows the
    plan's wish. A person's rules are untouched."""
    kept = [r for r in constraints.get("guest_rules") or [] if r["diner_key"] != MEAL_KEY]
    names = [name for name in dict.fromkeys(n.strip() for n in names) if name]
    return {
        **constraints,
        "guest_rules": [*kept, *(asdict(rule) for rule in meal_rules(session, names))],
        "meal_avoid": names,
    }


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
        # This meal's one-meal avoidance (plan 3b-1b spec 5.4), the base a revision carries.
        "meal_avoid": list(state_values["constraints"].get("meal_avoid") or []),
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
