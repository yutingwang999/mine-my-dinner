import json
import uuid
from unittest.mock import ANY

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from minemydinner.agent.edits import (
    Add,
    Conflict,
    EditError,
    Keep,
    Remove,
    Restore,
    apply_edit,
)
from minemydinner.agent.plan_card import build_dishes_card, card_base, meal_constraints
from minemydinner.models import Diner
from minemydinner.planning.context import load_planning_context
from minemydinner.planning.validate import H2Override, PlanSpec, validate_plan
from tests.planningfakes import L, make_recipe, rule, self_diner, stock
from tests.recipefakes import by_zh, seeded  # noqa: F401


def _diner(session, name: str) -> Diner:
    diner = Diner(display_name=name, is_self=False)
    session.add(diner)
    session.flush()
    return diner


@pytest.fixture
def env(session, seeded):  # noqa: F811
    class Env:
        pass

    e = Env()
    e.session = session
    e.me = self_diner(session)
    e.wang = _diner(session, "小王")
    e.veg = make_recipe(session, "清炒小白菜", lines=[L("小白菜")])
    e.egg = make_recipe(session, "番茄炒蛋", lines=[L("番茄"), L("鸡蛋")])
    e.meat = make_recipe(session, "红烧肉", dish_type="meat dish", lines=[L("五花肉")])
    e.soup = make_recipe(
        session, "豆腐汤", dish_type="soup", lines=[L("豆腐")], cookware=["蒸锅"]
    )
    return e


def _spec(**kwargs) -> dict:
    fields = {"mode": "shopping", "dishes": 2, "soups": 0, **kwargs}
    return PlanSpec(**fields).model_dump(mode="json")


def _values(env, card, spec: dict, diner_ids, defaults_used=(), reply_id=None) -> dict:
    reply_id = reply_id or str(uuid.uuid4())
    return {
        "messages": [HumanMessage("dinner", id="h1"), AIMessage("note", id=reply_id)],
        "request": None,
        "spec": spec,
        "constraints": {
            "diner_ids": list(diner_ids),
            "guest_rules": [],
            "unconfirmed_diners": 0,
            "headcount": len(diner_ids),
        },
        "defaults_used": list(defaults_used),
        "settled_diners": {
            "diner_ids": list(diner_ids),
            "meal_rules": [],
            "guest_names": {},
            "others": 0,
            "others_answer": None,
            "headcount": len(diner_ids),
        },
        "search_notes": {
            "left_out_unknown_allergens": [],
            "left_out_details": [],
            "semantic_paused": False,
        },
        "current_plan": card,
        "plan_versions": [card],
        "reply_plans": {reply_id: card},
        "_reply_id": reply_id,
    }


def model_state(env, recipe_ids, *, spec=None, diner_ids=None, defaults_used=()) -> dict:
    """A thread state as present_plan leaves it after a passing (or closest) model plan."""
    session = env.session
    spec = spec or _spec()
    diner_ids = diner_ids if diner_ids is not None else [env.me.id, env.wang.id]
    values = _values(env, None, spec, diner_ids, defaults_used)
    ctx = load_planning_context(session, meal_constraints(values))
    result = validate_plan(session, ctx, PlanSpec.model_validate(spec), recipe_ids)
    card = build_dishes_card(
        session,
        ctx,
        recipe_ids=recipe_ids,
        validation=result.model_dump(mode="json"),
        base=card_base(values, session),
        locale="zh",
    )
    return _values(env, card, spec, diner_ids, defaults_used, values["_reply_id"])


def no_plan_state(env) -> dict:
    """A thread state after an early stop: no recipes, reasons and next actions."""
    values = _values(env, None, _spec(), [env.me.id, env.wang.id])
    card = {
        "recipes": [],
        "missing": {},
        "missing_cookware": {},
        "warnings": [],
        "unmet": [],
        "next_actions": ["import_recipes"],
        "servings_notes": [],
        **card_base(values, env.session),
        "passed": False,
    }
    card["reasons"] = [
        {
            "reason": "not_enough_in_library",
            "category": "dishes",
            "needed": 2,
            "available": 0,
            "ingredient": None,
            "absent_from_stock": False,
            "text": "你的菜谱库里你能吃的只有 0 道菜，这餐需要 2 道菜",
        }
    ]
    return _values(env, card, _spec(), [env.me.id, env.wang.id], (), values["_reply_id"])


def run(env, values: dict, action, locale="zh"):
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, update = apply_edit(env.session, ctx, values, action, locale)
    return outcome, update, {**values, **update}


def ids(plan: dict) -> list[int]:
    return [r["id"] for r in plan["recipes"]]


def test_remove_starts_manual_version(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    outcome, update, after = run(env, values, Remove(recipe_id=env.egg.id))
    plan = outcome.plan
    assert plan["edited"] is True
    assert ids(plan) == [env.veg.id]
    assert len(update["plan_versions"]) == 2
    assert plan["model_version"] == 0
    assert update["plan_versions"][0] == values["plan_versions"][0]
    assert update["plan_versions"][1] == plan
    assert update["current_plan"] == plan
    assert update["reply_plans"][values["_reply_id"]] == plan
    assert plan["edit"]["log"] == [{"action": "remove", "recipe_id": env.egg.id, "at": ANY}]
    assert outcome.needs_confirmation == []


def test_second_edit_updates_same_version(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    _, _, after = run(env, values, Remove(recipe_id=env.egg.id))
    outcome, update, after = run(env, after, Add(recipe_id=env.meat.id, confirm=[]))
    assert len(update["plan_versions"]) == 2
    assert ids(update["plan_versions"][1]) == [env.veg.id, env.meat.id]
    assert update["plan_versions"][0] == values["plan_versions"][0]
    assert outcome.plan["model_version"] == 0
    assert [e["action"] for e in outcome.plan["edit"]["log"]] == ["remove", "add"]
    _, update, _ = run(env, after, Remove(recipe_id=env.veg.id))
    assert len(update["plan_versions"]) == 2
    assert ids(update["plan_versions"][1]) == [env.meat.id]


def test_add_without_conflict(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    outcome, update, _ = run(env, values, Add(recipe_id=env.soup.id, confirm=[]))
    plan = outcome.plan
    assert outcome.needs_confirmation == []
    assert ids(plan) == [env.veg.id, env.egg.id, env.soup.id]
    assert plan["missing"][str(env.soup.id)] == ["豆腐"]
    assert plan["missing_cookware"][str(env.soup.id)] == ["蒸锅"]
    assert plan["passed"] is True
    assert plan["edit"]["overrides"] == []
    assert plan["edit"]["log"] == [
        {"action": "add", "recipe_id": env.soup.id, "at": ANY, "overrides": []}
    ]


def _restrict_pork(env):
    return rule(env.session, env.wang.id, "dietary restriction", ingredient="五花肉")


def test_add_conflict_needs_confirmation(env):
    _restrict_pork(env)
    values = model_state(env, [env.veg.id, env.egg.id])
    outcome, update, _ = run(env, values, Add(recipe_id=env.meat.id, confirm=[]))
    assert outcome.plan is None
    assert update == {}
    [conflict] = outcome.needs_confirmation
    assert conflict.recipe_id == env.meat.id
    assert conflict.diner_key == f"diner:{env.wang.id}"
    assert conflict.diner_name == "小王"
    assert conflict.is_self is False
    assert conflict.kind == "ingredient"
    assert conflict.ingredient_id == by_zh(env.session, "五花肉").id
    assert conflict.ingredient_name == "五花肉"
    assert conflict.rule_type == "dietary restriction"


def test_add_conflict_names_the_user_as_self(env):
    rule(env.session, env.me.id, "allergy", allergen="egg")
    values = model_state(env, [env.veg.id], spec=_spec(dishes=1))
    outcome, _, _ = run(env, values, Add(recipe_id=env.egg.id, confirm=[]))
    [conflict] = outcome.needs_confirmation
    assert conflict.is_self is True
    assert conflict.diner_name is None
    assert conflict.kind == "allergen"
    assert conflict.allergen == "egg"


def _confirmed_meat(env):
    _restrict_pork(env)
    values = model_state(env, [env.veg.id, env.egg.id])
    asked, _, _ = run(env, values, Add(recipe_id=env.meat.id, confirm=[]))
    outcome, update, after = run(
        env, values, Add(recipe_id=env.meat.id, confirm=asked.needs_confirmation)
    )
    return outcome, update, after


def test_add_confirmed_records_override_and_log(env):
    outcome, _, _ = _confirmed_meat(env)
    plan = outcome.plan
    pork = by_zh(env.session, "五花肉").id
    assert ids(plan) == [env.veg.id, env.egg.id, env.meat.id]
    assert plan["passed"] is True
    assert plan["edit"]["overrides"] == [
        H2Override(
            recipe_id=env.meat.id, diner_key=f"diner:{env.wang.id}", ingredient_id=pork
        ).model_dump()
    ]
    assert plan["edit"]["log"] == [
        {
            "action": "add",
            "recipe_id": env.meat.id,
            "at": ANY,
            "overrides": [{"diner_key": f"diner:{env.wang.id}", "ingredient_id": pork}],
        }
    ]
    assert "小王" not in json.dumps(plan["edit"]["log"], ensure_ascii=False)


def test_confirmed_override_is_shown_as_kept(env):
    outcome, _, after = _confirmed_meat(env)
    [kept] = outcome.plan["edit"]["kept"]
    assert kept["recipe_id"] == env.meat.id
    assert kept["diner_name"] == "小王" and kept["is_self"] is False
    assert kept["kind"] == "ingredient"
    assert kept["ingredient_name"] == "五花肉"
    assert kept["rule_type"] == "dietary restriction"
    assert "Diner" not in json.dumps(outcome.plan["edit"], ensure_ascii=False)
    # A later edit keeps showing it; removing the dish drops it with its override.
    outcome, _, after = run(env, after, Remove(recipe_id=env.veg.id))
    assert [k["recipe_id"] for k in outcome.plan["edit"]["kept"]] == [env.meat.id]
    outcome, _, _ = run(env, after, Remove(recipe_id=env.meat.id))
    assert outcome.plan["edit"]["kept"] == []


def test_unknown_allergen_override_is_shown_as_kept(env):
    rule(env.session, env.wang.id, "allergy", allergen="peanut")
    odd = make_recipe(env.session, "神秘菜", lines=[L("某种酱", linked=False)])
    values = model_state(env, [env.veg.id, env.egg.id])
    asked, _, _ = run(env, values, Add(recipe_id=odd.id, confirm=[]))
    outcome, _, _ = run(env, values, Add(recipe_id=odd.id, confirm=asked.needs_confirmation))
    [kept] = outcome.plan["edit"]["kept"]
    assert kept["kind"] == "unknown_allergens" and kept["lines"] == ["某种酱"]


def test_forged_confirmation_creates_no_override(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    forged = Conflict(
        recipe_id=env.soup.id,
        diner_key=f"diner:{env.wang.id}",
        diner_name="小王",
        is_self=False,
        kind="allergen",
        allergen="soy",
        ingredient_id=None,
        ingredient_name=None,
        text=None,
        rule_type="allergy",
        lines=[],
    )
    outcome, _, _ = run(env, values, Add(recipe_id=env.soup.id, confirm=[forged]))
    assert ids(outcome.plan) == [env.veg.id, env.egg.id, env.soup.id]
    assert outcome.plan["edit"]["overrides"] == []
    assert outcome.plan["edit"]["log"][-1]["overrides"] == []


def test_forged_confirmation_on_real_conflict_asks(env):
    _restrict_pork(env)
    values = model_state(env, [env.veg.id, env.egg.id])
    asked, _, _ = run(env, values, Add(recipe_id=env.meat.id, confirm=[]))
    [real] = asked.needs_confirmation
    forged = real.model_copy(update={"ingredient_id": by_zh(env.session, "豆腐").id})
    outcome, update, _ = run(env, values, Add(recipe_id=env.meat.id, confirm=[forged]))
    assert outcome.plan is None
    assert update == {}
    assert [c.key() for c in outcome.needs_confirmation] == [real.key()]


def test_partial_confirmation_still_asks(env):
    _restrict_pork(env)
    rule(env.session, env.me.id, "dietary restriction", ingredient="五花肉")
    values = model_state(env, [env.veg.id, env.egg.id])
    asked, _, _ = run(env, values, Add(recipe_id=env.meat.id, confirm=[]))
    assert len(asked.needs_confirmation) == 2
    outcome, update, _ = run(
        env, values, Add(recipe_id=env.meat.id, confirm=asked.needs_confirmation[:1])
    )
    assert outcome.plan is None
    assert update == {}
    assert len(outcome.needs_confirmation) == 2


def test_confirmed_not_raised_again(env):
    _, _, after = _confirmed_meat(env)
    outcome, _, _ = run(env, after, Remove(recipe_id=env.veg.id))
    assert ids(outcome.plan) == [env.egg.id, env.meat.id]
    assert outcome.plan["edit"]["conflicts"] == []
    assert outcome.plan["passed"] is True
    assert len(outcome.plan["edit"]["overrides"]) == 1


def test_new_conflict_on_existing_dish_is_marked(env):
    values = model_state(env, [env.veg.id, env.egg.id, env.meat.id], spec=_spec(dishes=3))
    rule(env.session, env.wang.id, "allergy", allergen="egg")
    outcome, _, _ = run(env, values, Remove(recipe_id=env.veg.id))
    plan = outcome.plan
    assert ids(plan) == [env.egg.id, env.meat.id]
    [conflict] = plan["edit"]["conflicts"]
    assert conflict["recipe_id"] == env.egg.id
    assert conflict["diner_name"] == "小王"
    assert conflict["kind"] == "allergen"
    assert conflict["allergen"] == "egg"
    assert plan["passed"] is False
    assert outcome.needs_confirmation == []


def test_keep_records_override(env):
    values = model_state(env, [env.veg.id, env.egg.id, env.meat.id], spec=_spec(dishes=3))
    rule(env.session, env.wang.id, "allergy", allergen="egg")
    marked, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    confirm = [Conflict(**c) for c in marked.plan["edit"]["conflicts"]]
    outcome, update, _ = run(env, after, Keep(recipe_id=env.egg.id, confirm=confirm))
    plan = outcome.plan
    assert plan["edit"]["conflicts"] == []
    assert plan["passed"] is True
    assert plan["edit"]["overrides"] == [
        H2Override(
            recipe_id=env.egg.id, diner_key=f"diner:{env.wang.id}", allergen="egg"
        ).model_dump()
    ]
    assert plan["edit"]["log"][-1] == {
        "action": "keep",
        "recipe_id": env.egg.id,
        "at": ANY,
        "overrides": [{"diner_key": f"diner:{env.wang.id}", "allergen": "egg"}],
    }
    assert len(update["plan_versions"]) == 2


def test_keep_after_conflict_gone_returns_rechecked_card(env):
    values = model_state(env, [env.veg.id, env.egg.id, env.meat.id], spec=_spec(dishes=3))
    egg_rule = rule(env.session, env.wang.id, "allergy", allergen="egg")
    marked, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    confirm = [Conflict(**c) for c in marked.plan["edit"]["conflicts"]]
    # The rule is deleted elsewhere before the user presses keep.
    env.session.delete(egg_rule)
    env.session.flush()
    outcome, update, _ = run(env, after, Keep(recipe_id=env.egg.id, confirm=confirm))
    plan = outcome.plan
    assert ids(plan) == [env.egg.id, env.meat.id]
    assert plan["edit"]["conflicts"] == []
    assert plan["passed"] is True
    assert plan["edit"]["overrides"] == []
    # No keep is logged: nothing was confirmed.
    assert [e["action"] for e in plan["edit"]["log"]] == ["remove"]
    assert update["current_plan"] == plan
    assert len(update["plan_versions"]) == 2


def test_keep_after_conflict_gone_on_restored_card(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    _, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    egg_rule = rule(env.session, env.wang.id, "allergy", allergen="egg")
    restored, _, after = run(env, after, Restore())
    assert restored.plan["edit"]["conflicts"]
    env.session.delete(egg_rule)
    env.session.flush()
    outcome, update, _ = run(env, after, Keep(recipe_id=env.egg.id, confirm=[]))
    plan = outcome.plan
    assert ids(plan) == [env.veg.id, env.egg.id]
    assert plan["edited"] is False
    assert plan["edit"]["conflicts"] == []
    assert plan["passed"] is True
    # A restored card stays the model's card: no new manual version.
    assert update["plan_versions"] == after["plan_versions"]
    assert update["current_plan"] == plan


def test_keep_with_forged_confirmation_asks(env):
    values = model_state(env, [env.veg.id, env.egg.id, env.meat.id], spec=_spec(dishes=3))
    rule(env.session, env.wang.id, "allergy", allergen="egg")
    marked, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    forged = Conflict(**{**marked.plan["edit"]["conflicts"][0], "allergen": "milk"})
    outcome, update, _ = run(env, after, Keep(recipe_id=env.egg.id, confirm=[forged]))
    assert outcome.plan is None
    assert update == {}
    assert [c.allergen for c in outcome.needs_confirmation] == ["egg"]


def test_remove_drops_overrides(env):
    _, _, after = _confirmed_meat(env)
    outcome, _, after = run(env, after, Remove(recipe_id=env.meat.id))
    assert outcome.plan["edit"]["overrides"] == []
    again, update, _ = run(env, after, Add(recipe_id=env.meat.id, confirm=[]))
    assert again.plan is None
    assert update == {}
    assert len(again.needs_confirmation) == 1


def test_unknown_allergen_override_covers_dish(env):
    rule(env.session, env.wang.id, "allergy", allergen="peanut")
    odd = make_recipe(env.session, "神秘菜", lines=[L("某种酱", linked=False)])
    values = model_state(env, [env.veg.id, env.egg.id])
    asked, _, _ = run(env, values, Add(recipe_id=odd.id, confirm=[]))
    [conflict] = asked.needs_confirmation
    assert conflict.kind == "unknown_allergens"
    assert conflict.diner_key == "*"
    assert conflict.lines == ["某种酱"]
    outcome, _, after = run(env, values, Add(recipe_id=odd.id, confirm=[conflict]))
    assert outcome.plan["edit"]["overrides"] == [
        H2Override(recipe_id=odd.id, diner_key="*", unknown_allergens=True).model_dump()
    ]
    # Another diner with an allergy joins: the unknown-allergen confirmation still covers it.
    li = _diner(env.session, "小李")
    rule(env.session, li.id, "allergy", allergen="milk")
    joined = {
        **after,
        "constraints": {**after["constraints"], "diner_ids": [env.me.id, env.wang.id, li.id]},
    }
    outcome, _, _ = run(env, joined, Remove(recipe_id=env.veg.id))
    assert odd.id in ids(outcome.plan)
    assert outcome.plan["edit"]["conflicts"] == []
    assert outcome.plan["passed"] is True


def test_duplicate_add_refused(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    with pytest.raises(EditError) as raised:
        run(env, values, Add(recipe_id=env.veg.id, confirm=[]))
    assert raised.value.code == "already_in_plan"


def test_counts_note_only_for_stated(env):
    spec = _spec(dishes=3)
    values = model_state(env, [env.veg.id, env.egg.id, env.meat.id], spec=spec)
    outcome, _, _ = run(env, values, Remove(recipe_id=env.meat.id))
    assert outcome.plan["count_notes"] == [{"field": "dishes", "stated": 3, "now": 2}]
    assert outcome.plan["passed"] is True
    assert outcome.plan["unmet"] == []
    values = model_state(
        env, [env.veg.id, env.egg.id, env.meat.id], spec=spec, defaults_used=["dishes"]
    )
    outcome, _, _ = run(env, values, Remove(recipe_id=env.meat.id))
    assert outcome.plan["count_notes"] == []


def test_unstated_zero_count_gives_no_note(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    values["request"] = {"dishes": 2, "soups": None}
    outcome, _, _ = run(env, values, Add(recipe_id=env.soup.id, confirm=[]))
    assert outcome.plan["count_notes"] == []
    values["request"] = {"dishes": 2, "soups": 0}
    outcome, _, _ = run(env, values, Add(recipe_id=env.soup.id, confirm=[]))
    assert outcome.plan["count_notes"] == [{"field": "soups", "stated": 0, "now": 1}]


def test_h1_h3_h6_h7_are_warnings(env):
    session = env.session
    stock(session, "小白菜", count=2)
    stock(session, "五花肉", count=1)
    slow = make_recipe(
        session,
        "慢炖豆腐",
        lines=[L("豆腐")],
        cookware=["蒸锅"],
        time_min=90,
        time_source="stated",
    )
    spec = _spec(mode="fridge_only", time_limit_min=30, must_use=["五花肉"])
    values = model_state(env, [env.veg.id, env.meat.id], spec=spec)
    assert values["current_plan"]["passed"] is True
    _, _, after = run(env, values, Remove(recipe_id=env.meat.id))
    outcome, _, _ = run(env, after, Add(recipe_id=slow.id, confirm=[]))
    plan = outcome.plan
    assert plan["passed"] is True
    assert plan["unmet"] == []
    assert plan["next_actions"] == []
    codes = {w["code"] for w in plan["warnings"]}
    assert {"H1", "H3", "H6", "H7"} <= codes
    assert all(w["text"] for w in plan["warnings"] if w["code"].startswith("H"))


def test_restore_rechecks_today(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    model = values["plan_versions"][0]
    _, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    rule(env.session, env.wang.id, "allergy", allergen="egg")
    outcome, update, after = run(env, after, Restore())
    plan = outcome.plan
    assert ids(plan) == ids(model)
    assert plan["edited"] is False
    assert plan["edit"]["overrides"] == []
    assert plan["edit"]["log"] == []
    [conflict] = plan["edit"]["conflicts"]
    assert conflict["recipe_id"] == env.egg.id
    assert plan["passed"] is False
    assert update["current_plan"] == plan
    assert update["reply_plans"][values["_reply_id"]] == plan
    # The manual version stays recorded, with the restore in its log.
    assert len(update["plan_versions"]) == 2
    assert update["plan_versions"][0] == model
    assert [e["action"] for e in update["plan_versions"][1]["edit"]["log"]] == [
        "remove",
        "restore",
    ]
    # The next edit starts a new manual version.
    _, update, _ = run(env, after, Remove(recipe_id=env.egg.id))
    assert len(update["plan_versions"]) == 3
    assert update["plan_versions"][2]["model_version"] == 0


def test_restore_drops_deleted_recipe(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    _, _, after = run(env, values, Remove(recipe_id=env.veg.id))
    env.session.delete(env.veg)
    env.session.flush()
    outcome, _, _ = run(env, after, Restore())
    assert ids(outcome.plan) == [env.egg.id]
    assert outcome.plan["notes"] == [{"field": "deleted_recipes", "titles": ["清炒小白菜"]}]


def test_edit_drops_deleted_recipe(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    env.session.delete(env.veg)
    env.session.flush()
    outcome, _, _ = run(env, values, Add(recipe_id=env.soup.id, confirm=[]))
    assert ids(outcome.plan) == [env.egg.id, env.soup.id]
    assert outcome.plan["notes"] == [{"field": "deleted_recipes", "titles": ["清炒小白菜"]}]


def test_restore_no_plan_reply_as_recorded(env):
    values = no_plan_state(env)
    recorded = values["plan_versions"][0]
    _, _, after = run(env, values, Add(recipe_id=env.veg.id, confirm=[]))
    outcome, update, _ = run(env, after, Restore())
    assert outcome.plan == recorded
    assert update["current_plan"] == recorded
    assert update["reply_plans"][values["_reply_id"]] == recorded


def test_edit_on_no_plan_reply(env):
    values = no_plan_state(env)
    outcome, update, _ = run(env, values, Add(recipe_id=env.veg.id, confirm=[]))
    plan = outcome.plan
    assert ids(plan) == [env.veg.id]
    assert plan["edited"] is True
    assert plan["model_version"] == 0
    assert plan["reasons"] == []
    assert plan["next_actions"] == []
    assert plan["unmet"] == []
    assert plan["count_notes"] == [{"field": "dishes", "stated": 2, "now": 1}]
    assert len(update["plan_versions"]) == 2


def test_remove_unknown_dish_refused(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    with pytest.raises(EditError) as raised:
        run(env, values, Remove(recipe_id=env.meat.id))
    assert raised.value.code == "not_in_plan"


def test_restore_without_edit_refused(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    with pytest.raises(EditError) as raised:
        run(env, values, Restore())
    assert raised.value.code == "not_edited"


def test_guest_conflict_named_by_guest_name(env):
    values = model_state(env, [env.veg.id, env.egg.id])
    guest_rule = {
        "diner_key": "guest:1",
        "rule_type": "allergy",
        "allergen": "egg",
        "ingredient_id": None,
        "text": None,
    }
    values["constraints"] = {**values["constraints"], "guest_rules": [guest_rule]}
    values["settled_diners"] = {**values["settled_diners"], "guest_names": {"guest:1": "阿美"}}
    outcome, _, _ = run(env, values, Remove(recipe_id=env.veg.id))
    [conflict] = outcome.plan["edit"]["conflicts"]
    assert conflict["diner_key"] == "guest:1"
    assert conflict["diner_name"] == "阿美"
    assert "Diner" not in json.dumps(outcome.plan, ensure_ascii=False)
