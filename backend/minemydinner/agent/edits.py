"""Manual plan edits (design 7.5, plan 3a spec 5.2-5.4): remove a dish, add one after
confirming its conflicts, keep a dish that gained a conflict, or restore the model's plan.

Every edit is checked by code (`validate_plan`), with no model call. The client's
confirmations are only the user's acceptance: the conflicts are computed here for each
edit, and an override is recorded only for a computed conflict the user confirmed.

The edited card is the plan card (`build_dishes_card`) plus:
- `edited`: True on a manual version, False on a restored model plan.
- `model_version`: the index in `plan_versions` of the model version it derives from.
- `edit`: `overrides` (H2Override dumps), `log` (one entry per edit, diner keys only, never
  a name), and `conflicts` (Conflict dumps: H2 findings on dishes in the plan that no
  override covers).
- `count_notes`: `{"field", "stated", "now"}` for each count the user stated that the edited
  plan no longer matches.
- `notes`: quiet notes, such as `{"field": "deleted_recipes", "titles": [...]}` for dishes
  dropped because they left the library.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.agent.card_text import is_zh, unmet_for_user
from minemydinner.agent.plan_card import build_dishes_card, card_base
from minemydinner.models import Diner, Ingredient, Recipe
from minemydinner.planning.composition import (
    SPLIT_CATEGORIES,
    count_categories,
    outside,
    side_ranges,
)
from minemydinner.planning.context import MEAL_KEY, PlanningContext
from minemydinner.planning.dietary import RuleConflict
from minemydinner.planning.search import BlockedHit
from minemydinner.planning.validate import (
    Finding,
    H2Override,
    PlanSpec,
    ValidationResult,
    validate_plan,
)

# The diner key of an unknown-allergen override: it covers the dish for every diner.
EVERY_DINER = "*"
# Failed hard checks the user may allow on an edited plan; they show as warnings.
ALLOWED_CODES = ("H1", "H3", "H6", "H7")
# The counts a note can name, in the card's order.
COUNT_NOTE_FIELDS = (
    "dishes",
    "soups",
    "meat_dishes",
    "vegetable_dishes",
    "staples",
    "desserts",
    "snacks",
)


class EditError(Exception):
    """An edit that cannot apply: `code` is already_in_plan, not_in_plan, not_found,
    not_edited, or no_plan."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Conflict(BaseModel):
    """One H2 conflict of a dish, for the user to confirm. Diners are named by display name
    (the user: `is_self` with no name; a guest: the guest's name), never by a label. This
    meal's own wish (`diner_key` "meal", a one-meal avoidance) has no name and is no one."""

    recipe_id: int
    diner_key: str
    diner_name: str | None = None
    is_self: bool = False
    kind: Literal["allergen", "ingredient", "text", "unknown_allergens"]
    allergen: str | None = None
    ingredient_id: int | None = None
    ingredient_name: str | None = None
    text: str | None = None
    rule_type: str | None = None
    # The unknown-allergen lines, or the lines that hit the rule.
    lines: list[str] = Field(default_factory=list)

    def key(self) -> tuple:
        return (
            self.recipe_id,
            self.diner_key,
            self.kind,
            self.allergen,
            self.ingredient_id,
            self.text,
        )

    def override(self) -> H2Override:
        if self.kind == "unknown_allergens":
            return H2Override(
                recipe_id=self.recipe_id, diner_key=EVERY_DINER, unknown_allergens=True
            )
        return H2Override(
            recipe_id=self.recipe_id,
            diner_key=self.diner_key,
            allergen=self.allergen,
            ingredient_id=self.ingredient_id,
            text=self.text,
        )


class Remove(BaseModel):
    action: Literal["remove"] = "remove"
    recipe_id: int


class Add(BaseModel):
    action: Literal["add"] = "add"
    recipe_id: int
    confirm: list[Conflict] = Field(default_factory=list)


class Keep(BaseModel):
    action: Literal["keep"] = "keep"
    recipe_id: int
    confirm: list[Conflict] = Field(default_factory=list)


class Restore(BaseModel):
    action: Literal["restore"] = "restore"


class EditOutcome(BaseModel):
    # The new card; None when the edit needs confirmation first.
    plan: dict | None
    needs_confirmation: list[Conflict] = Field(default_factory=list)


def newest_reply_id(values: dict) -> str:
    """The id of the newest assistant reply with a plan: the only editable card."""
    plans = values.get("reply_plans") or {}
    for message in reversed(values.get("messages") or []):
        message_id = getattr(message, "id", None)
        if message_id in plans:
            return message_id
    raise EditError("no_plan")


def latest_model_index(versions: list[dict]) -> int:
    """The latest model version: manual versions are the only entries marked `edited`."""
    for index in range(len(versions) - 1, -1, -1):
        if not versions[index].get("edited"):
            return index
    raise EditError("no_plan")


def locked_ids(plan: dict) -> set[int]:
    """The dishes a revision must keep: those added by hand and still in the plan (`locked`)
    and those an override covers. A card saved before locks existed reads as no locks."""
    ids = set(plan.get("locked") or [])
    ids.update(o["recipe_id"] for o in (plan.get("edit") or {}).get("overrides", []))
    return ids


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_override(override: H2Override) -> dict:
    """An override as the edit log keeps it: the diner key and the rule's target only."""
    entry: dict = {"diner_key": override.diner_key}
    if override.unknown_allergens:
        entry["unknown_allergens"] = True
    for name in ("allergen", "ingredient_id", "text"):
        value = getattr(override, name)
        if value is not None:
            entry[name] = value
    return entry


def _diner_names(session: Session, values: dict, keys: set[str]) -> dict[str, tuple]:
    """(display name, is_self) per diner key: diners from the diners table, guests from the
    settled set's guest names. The user has no name."""
    ids = {int(key.split(":", 1)[1]) for key in keys if key.startswith("diner:")}
    names: dict[str, tuple] = {}
    if ids:
        for row in session.execute(
            select(Diner.id, Diner.display_name, Diner.is_self).where(Diner.id.in_(ids))
        ):
            names[f"diner:{row.id}"] = (None, True) if row.is_self else (row.display_name, False)
    guests = (values.get("settled_diners") or {}).get("guest_names") or {}
    for key in keys:
        if key.startswith("guest:"):
            names[key] = (guests.get(key), False)
    return names


def _conflicts(
    session: Session, values: dict, violations: list[Finding], locale: str
) -> list[Conflict]:
    """The H2 findings as conflicts, one per recipe, diner and rule, in finding order."""
    h2 = [v for v in violations if v.code == "H2"]
    names = _diner_names(
        session, values, {v.detail["diner_key"] for v in h2 if "diner_key" in v.detail}
    )
    ingredient_ids = {v.detail["ingredient_id"] for v in h2 if v.detail.get("ingredient_id")}
    ingredients: dict[int, str | None] = {}
    if ingredient_ids:
        for row in session.execute(
            select(Ingredient.id, Ingredient.name_zh, Ingredient.name_en).where(
                Ingredient.id.in_(ingredient_ids)
            )
        ):
            first, second = (row.name_zh, row.name_en) if is_zh(locale) else (
                row.name_en, row.name_zh
            )
            ingredients[row.id] = first or second
    found: dict[tuple, Conflict] = {}
    for finding in h2:
        detail = finding.detail
        if "unknown" in detail:
            conflict = Conflict(
                recipe_id=finding.recipe_id,
                diner_key=EVERY_DINER,
                kind="unknown_allergens",
                lines=list(detail["unknown"]),
            )
        else:
            if detail.get("allergen") is not None:
                kind = "allergen"
            elif detail.get("ingredient_id") is not None:
                kind = "ingredient"
            else:
                kind = "text"
            # This meal's own wish (diner key MEAL_KEY, plan 3b-1b spec 5.4) is no person: no
            # name and never the user; the card shows it as the meal's wish.
            name, is_self = (
                (None, False)
                if detail["diner_key"] == MEAL_KEY
                else names.get(detail["diner_key"], (None, False))
            )
            conflict = Conflict(
                recipe_id=finding.recipe_id,
                diner_key=detail["diner_key"],
                diner_name=name,
                is_self=is_self,
                kind=kind,
                allergen=detail.get("allergen"),
                ingredient_id=detail.get("ingredient_id"),
                ingredient_name=ingredients.get(detail.get("ingredient_id")),
                text=detail.get("text"),
                rule_type=detail.get("rule_type"),
                lines=[detail["line"]] if detail.get("line") else [],
            )
        existing = found.get(conflict.key())
        if existing is None:
            found[conflict.key()] = conflict
        else:
            existing.lines += [line for line in conflict.lines if line not in existing.lines]
    return list(found.values())


def search_markers(
    session: Session,
    values: dict,
    hits: list[BlockedHit],
    dislikes: list[RuleConflict],
    locale: str,
) -> dict[int, dict]:
    """Per hit of the card's search (`search_recipes(show_blocked=True)`): its conflicts as
    `Conflict` dumps, the same ones adding the dish asks to confirm, and its dislikes as
    `{diner_key, diner_name, is_self, line}`. Diners are named as in `Conflict`; the search's
    own `dislikes` strings carry labels and are never shown."""
    findings = [
        Finding(code="H2", recipe_id=hit.id, detail=dict(conflict))
        for hit in hits
        for conflict in hit.conflicts
    ]
    findings += [
        Finding(code="H2", recipe_id=hit.id, detail={"unknown": list(hit.blocked_unknown)})
        for hit in hits
        if hit.blocked_unknown
    ]
    markers: dict[int, dict] = {hit.id: {"conflicts": [], "dislikes": []} for hit in hits}
    for conflict in _conflicts(session, values, findings, locale):
        markers[conflict.recipe_id]["conflicts"].append(conflict.model_dump())
    names = _diner_names(session, values, {d.diner_key for d in dislikes})
    for item in dislikes:
        if item.recipe_id not in markers:
            continue
        name, is_self = names.get(item.diner_key, (None, False))
        entry = {
            "diner_key": item.diner_key,
            "diner_name": name,
            "is_self": is_self,
            "line": item.line_name,
        }
        if entry not in markers[item.recipe_id]["dislikes"]:
            markers[item.recipe_id]["dislikes"].append(entry)
    return markers


def _count_notes(values: dict, spec: PlanSpec, recipes: list[dict]) -> list[dict]:
    """How the plan differs from each count the user stated. A count filled by default (in
    `defaults_used`) gives no note, nor does a 0 for a category the request never named."""
    present = count_categories(r["dish_type"] for r in recipes)
    ranges = side_ranges(spec)
    defaults = set(values.get("defaults_used") or [])
    # The request the plan was built from; a later unclear turn may have parsed another.
    request = values.get("plan_request") or values.get("request")
    notes = []
    for field in COUNT_NOTE_FIELDS:
        stated = getattr(spec, field)
        if field in defaults or stated is None:
            continue
        if stated == 0 and request is not None and request.get(field) is None:
            continue
        if field in SPLIT_CATEGORIES:
            low, high = ranges[field]
            differs = outside(present[field], low, high) > 0
        else:
            differs = present[field] != stated
        if differs:
            notes.append({"field": field, "stated": stated, "now": present[field]})
    return notes


def _check(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    recipe_ids: list[int],
    overrides: list[H2Override],
    titles: dict[int, str],
) -> tuple[list[int], list[H2Override], list[dict], ValidationResult]:
    """Validate the dishes still in the library. Returns their ids, the overrides of those
    dishes, the quiet notes (dishes dropped because they left the library) and the result."""
    existing = set(session.scalars(select(Recipe.id).where(Recipe.id.in_(recipe_ids))))
    deleted = [i for i in recipe_ids if i not in existing]
    live = [i for i in recipe_ids if i in existing]
    kept = [o for o in overrides if o.recipe_id in existing]
    notes = []
    if deleted:
        notes.append(
            {"field": "deleted_recipes", "titles": [titles[i] for i in deleted if i in titles]}
        )
    spec = PlanSpec.model_validate(values["spec"])
    return live, kept, notes, validate_plan(session, ctx, spec, live, kept)


def _kept(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    spec: PlanSpec,
    recipe_ids: list[int],
    overrides: list[H2Override],
    locale: str,
) -> list[Conflict]:
    """The conflicts the overrides cover, named for the card ("kept: 小王 avoids pork"): the
    plan checked without overrides, keeping the conflicts an override matches. An override
    whose conflict no longer exists (a rule deleted since) shows nothing."""
    if not overrides:
        return []
    bare = validate_plan(session, ctx, spec, recipe_ids, [])
    found = _conflicts(session, values, bare.violations, locale)
    return [c for c in found if c.override() in overrides]


def _base(session: Session, values: dict, recipe_ids: list[int]) -> dict:
    """The card's base keys, without a left-out entry for a dish that is now in the plan."""
    base = card_base(values, session)
    if not recipe_ids:
        return base
    in_plan = set(recipe_ids)
    titles = set(
        session.execute(select(Recipe.title).where(Recipe.id.in_(recipe_ids))).scalars().all()
    )
    base["left_out_details"] = [d for d in base["left_out_details"] if d["id"] not in in_plan]
    base["left_out_unknown_allergens"] = [
        t for t in base["left_out_unknown_allergens"] if t not in titles
    ]
    return base


def _edited_card(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    *,
    recipe_ids: list[int],
    result: ValidationResult,
    conflicts: list[Conflict],
    overrides: list[H2Override],
    locked: list[int],
    log: list[dict],
    notes: list[dict],
    model_index: int,
    locale: str,
) -> dict:
    """The card of a manual version: H1, H3, H6 and H7 as warnings with their plain text,
    H4 as count notes, and `passed` meaning no unresolved conflict."""
    titles = dict(
        session.execute(select(Recipe.id, Recipe.title).where(Recipe.id.in_(recipe_ids))).all()
    )
    allowed = [
        {**v.model_dump(mode="json"), "text": unmet_for_user(v.model_dump(), titles, locale)}
        for v in result.violations
        if v.code in ALLOWED_CODES
    ]
    card = build_dishes_card(
        session,
        ctx,
        recipe_ids=recipe_ids,
        validation={
            "passed": True,
            "warnings": allowed + [w.model_dump(mode="json") for w in result.warnings],
            "violations": [],
        },
        base=_base(session, values, recipe_ids),
        locale=locale,
    )
    spec = PlanSpec.model_validate(values["spec"])
    kept = _kept(session, ctx, values, spec, recipe_ids, overrides, locale)
    card.update(
        passed=not conflicts,
        edited=True,
        locked=locked,
        model_version=model_index,
        edit={
            "overrides": [o.model_dump() for o in overrides],
            "log": log,
            "conflicts": [c.model_dump() for c in conflicts],
            "kept": [c.model_dump() for c in kept],
        },
        count_notes=_count_notes(values, spec, card["recipes"]),
        notes=notes,
    )
    return card


def _restored_card(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    model: dict,
    model_index: int,
    locale: str,
) -> dict:
    """The model version's dishes checked again against today's data, with the overrides and
    locks the version carries (a revised version has some; the model's own plan has none):
    the card the model's plan would get today. A new H2 conflict is marked, not listed as
    unmet."""
    titles = {r["id"]: r["title"] for r in model["recipes"]}
    carried = [H2Override(**o) for o in (model.get("edit") or {}).get("overrides", [])]
    ids, overrides, notes, result = _check(session, ctx, values, list(titles), carried, titles)
    conflicts = _conflicts(session, values, result.violations, locale)
    others = [v for v in result.violations if v.code not in ("H2", "H5")]
    card = build_dishes_card(
        session,
        ctx,
        recipe_ids=ids,
        validation={
            "passed": not others,
            "warnings": [w.model_dump(mode="json") for w in result.warnings],
            "violations": [v.model_dump(mode="json") for v in others],
        },
        base=_base(session, values, ids),
        locale=locale,
    )
    spec = PlanSpec.model_validate(values["spec"])
    kept = _kept(session, ctx, values, spec, ids, overrides, locale)
    card.update(
        passed=not others and not conflicts,
        edited=False,
        locked=[i for i in model.get("locked") or [] if i in ids],
        model_version=model_index,
        edit={
            "overrides": [o.model_dump() for o in overrides],
            "log": [],
            "conflicts": [c.model_dump() for c in conflicts],
            "kept": [c.model_dump() for c in kept],
        },
        count_notes=[],
        notes=notes,
    )
    # The request the version was built from (and `plan_kept`), so a next-action button on the
    # restored card changes that request in code (plan 3b-1b spec 5.5).
    for key in ("request", "plan_kept"):
        if key in model:
            card[key] = model[key]
    return card


def revised_card(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    *,
    recipe_ids: list[int],
    result: ValidationResult,
    overrides: list[H2Override],
    locked: list[int],
    index: int,
    locale: str,
) -> dict:
    """A revision that passed its checks, as a model version (plan 3b-1 spec 5.5): the card of
    a manual version (H1, H3, H6 and H7 of locked dishes as warnings, the carried overrides
    with their kept conflicts), but not edited, with `model_version` its own index in
    `plan_versions`, an empty edit log and no count notes. `values["spec"]` is the
    revision's spec. The caller adds `revision`."""
    card = _edited_card(
        session,
        ctx,
        values,
        recipe_ids=recipe_ids,
        result=result,
        conflicts=_conflicts(session, values, result.violations, locale),
        overrides=overrides,
        locked=locked,
        log=[],
        notes=[],
        model_index=index,
        locale=locale,
    )
    card.update(edited=False, count_notes=[])
    return card


def _update(values: dict, reply_id: str, versions: list[dict], card: dict) -> dict:
    return {
        "current_plan": card,
        "plan_versions": versions,
        "reply_plans": {**(values.get("reply_plans") or {}), reply_id: card},
    }


def apply_edit(
    session: Session,
    ctx: PlanningContext,
    values: dict,
    action: Remove | Add | Keep | Restore,
    locale: str,
) -> tuple[EditOutcome, dict]:
    """Apply one edit to the newest reply's plan. Returns the outcome and the state update
    (`current_plan`, `plan_versions`, `reply_plans`); the update is empty when the edit
    needs confirmation. The first edit after a model version appends a manual version to
    `plan_versions`; later edits replace that entry. Raises EditError."""
    reply_id = newest_reply_id(values)
    current = values["reply_plans"][reply_id]
    versions = list(values.get("plan_versions") or [])
    model_index = latest_model_index(versions)
    editing = bool(current.get("edited"))

    if isinstance(action, Restore):
        if not editing:
            raise EditError("not_edited")
        model = versions[model_index]
        if model["recipes"]:
            card = _restored_card(session, ctx, values, model, model_index, locale)
        else:
            # A no-plan or early-stop reply comes back with its reasons as recorded.
            card = model
        manual = versions[-1]
        log = [*manual["edit"]["log"], {"action": "restore", "recipe_id": None, "at": _now()}]
        versions[-1] = {**manual, "edit": {**manual["edit"], "log": log}}
        return EditOutcome(plan=card), _update(values, reply_id, versions, card)

    # A model version may carry overrides and locks too (revised or restored): they carry into
    # the manual version, whose log starts empty.
    edit = current.get("edit") or {}
    overrides = [H2Override(**o) for o in edit.get("overrides", [])]
    log = list(edit.get("log", [])) if editing else []
    locked = list(current.get("locked") or [])
    recipe_ids = [r["id"] for r in current["recipes"]]
    titles = {r["id"]: r["title"] for r in current["recipes"]}
    target = action.recipe_id

    if isinstance(action, Remove):
        if target not in recipe_ids:
            raise EditError("not_in_plan")
        recipe_ids.remove(target)
        # Removing a dish removes its overrides: adding it back asks again.
        overrides = [o for o in overrides if o.recipe_id != target]
        locked = [i for i in locked if i != target]
    elif isinstance(action, Add):
        if target in recipe_ids:
            raise EditError("already_in_plan")
        if session.get(Recipe, target) is None:
            raise EditError("not_found")
        recipe_ids.append(target)
        locked.append(target)
    elif target not in recipe_ids:
        raise EditError("not_in_plan")

    ids, overrides, notes, result = _check(session, ctx, values, recipe_ids, overrides, titles)
    conflicts = _conflicts(session, values, result.violations, locale)
    entry: dict | None = {"action": action.action, "recipe_id": target, "at": _now()}
    if isinstance(action, Add | Keep):
        asked = [c for c in conflicts if c.recipe_id == target]
        if isinstance(action, Keep) and not asked:
            # The conflict went away since the card was built (a rule deleted elsewhere):
            # the card comes back checked again, with nothing logged and no override.
            if not editing:
                # A restored model card stays the model's card, checked against today's data.
                model = versions[model_index]
                card = _restored_card(session, ctx, values, model, model_index, locale)
                return EditOutcome(plan=card), _update(values, reply_id, versions, card)
            entry = None
        confirmed = {c.key() for c in action.confirm}
        if any(c.key() not in confirmed for c in asked):
            # Every conflict of the dish is listed, so one confirmation covers them all.
            return EditOutcome(plan=None, needs_confirmation=asked), {}
        added = [c.override() for c in asked]
        if added:
            overrides = [*overrides, *added]
            spec = PlanSpec.model_validate(values["spec"])
            result = validate_plan(session, ctx, spec, ids, overrides)
            conflicts = _conflicts(session, values, result.violations, locale)
        if entry is not None:
            entry["overrides"] = [_log_override(o) for o in added]

    card = _edited_card(
        session,
        ctx,
        values,
        recipe_ids=ids,
        result=result,
        conflicts=conflicts,
        overrides=overrides,
        locked=[i for i in locked if i in ids],
        log=log if entry is None else [*log, entry],
        notes=notes,
        model_index=model_index,
        locale=locale,
    )
    if editing:
        versions[-1] = card
    else:
        versions.append(card)
    return EditOutcome(plan=card), _update(values, reply_id, versions, card)
