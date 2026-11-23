"""Changing a plan through the real graph entry (plan 3b-2: the reader, the frame and the
picker), and the revise helpers the change path keeps. The plan 3b-1 revise step these tests
first covered is gone; the change path's own tests are in test_agent_graph.py."""

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from minemydinner.agent.edits import Add, apply_edit
from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.plan_card import meal_constraints
from minemydinner.agent.planner import PICK_TOOLS_VERSION
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import set_request_style
from minemydinner.agent.revise_step import EMPTY_PLAN_QUESTION_EN, EMPTY_PLAN_QUESTION_ZH
from minemydinner.models import AppPreference, PlanningRun
from minemydinner.planning.context import load_planning_context
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import (
    L,
    make_recipe,
    rollback_session_factory,
    rule,
    self_diner,
)
from tests.recipefakes import seeded  # noqa: F401


@dataclass
class Env:
    graph: Any
    model: ScriptedChatModel
    session: Any
    config: dict
    dishes: dict


@pytest.fixture
def env(tmp_path, session, seeded):  # noqa: F811
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            # A follow-up on a plan is read (plan 3b-2): by default it changes the plan.
            "FollowUpReading": [{"kind": "change"}],
            "ParsedRequest": [{"dishes": 3, "mode": "shopping"}],
        }
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, model)
    deps = GraphDeps(factory, client, PlanningRecorder(factory))
    dishes = {
        "鱼香茄子": make_recipe(session, "鱼香茄子", "meat dish", [L("茄子", "main"), L("猪肉")]),
        "番茄炒蛋": make_recipe(session, "番茄炒蛋", "vegetable dish", [L("番茄"), L("鸡蛋")]),
        "清炒小白菜": make_recipe(session, "清炒小白菜", "vegetable dish", [L("小白菜")]),
        "土豆炖牛肉": make_recipe(
            session, "土豆炖牛肉", "meat dish", [L("牛肉", "main"), L("土豆")]
        ),
        "青椒炒蛋": make_recipe(session, "青椒炒蛋", "vegetable dish", [L("青椒"), L("鸡蛋")]),
        "干煸豆角": make_recipe(session, "干煸豆角", "vegetable dish", [L("豆角"), L("辣椒")]),
        "红烧肉": make_recipe(session, "红烧肉", "meat dish", [L("五花肉", "main")]),
    }
    config = {"configurable": {"thread_id": "revise-1"}}
    return Env(build_graph(deps, InMemorySaver()), model, session, config, dishes)


def ids_of(env: Env, *titles: str) -> list[int]:
    return [env.dishes[t].id for t in titles]


def recipe_ids(plan: dict) -> list[int]:
    return [r["id"] for r in plan["recipes"]]


def first_plan(env: Env, *titles: str, parse: dict | None = None) -> dict:
    if parse is not None:
        env.model.structured["ParsedRequest"] = [parse]
    env.model.replies = [final_proposal(ids_of(env, *titles))]
    out = env.graph.invoke(
        {"messages": [HumanMessage("今晚吃什么")], "locale": "zh"}, env.config
    )
    assert out["current_plan"]["passed"]
    return out


def hand_edit(env: Env, action) -> dict:
    """A 3a edit as the API applies it; an add with conflicts is confirmed."""
    values = env.graph.get_state(env.config).values
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, update = apply_edit(env.session, ctx, values, action, "zh")
    if outcome.plan is None:
        action = action.model_copy(update={"confirm": outcome.needs_confirmation})
        outcome, update = apply_edit(env.session, ctx, values, action, "zh")
    env.graph.update_state(env.config, update, as_node="present_plan")
    return env.graph.get_state(env.config).values


def last_record(env: Env) -> PlanningRun:
    env.session.expire_all()
    return env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()


def test_from_version_names_the_version_the_plan_is():
    from minemydinner.agent.revise_step import from_version

    model, manual = {"recipes": []}, {"recipes": [], "edited": True, "model_version": 0}
    assert from_version(model, [model]) == 0
    assert from_version(manual, [model, manual]) == 1
    restored = {"recipes": [], "edited": False, "model_version": 0}
    assert from_version(restored, [model, manual]) == 0
    newer = {"recipes": []}
    assert from_version(newer, [model, manual, newer]) == 2


def routed_revise(env: Env, text: str, reading: dict, replies: list) -> dict:
    """A follow-up turn through the real graph entry (the reader, scripted as a change, plan
    3b-2), so the turn keeps the thread's request as `previous_request` as it does in the app."""
    env.model.replies = list(replies)
    env.model.structured["FollowUpReading"] = [{"kind": "change", **reading}]
    return env.graph.invoke({"messages": [HumanMessage(text)], "locale": "zh"}, env.config)


def picked(slots=(), choices=()):
    """The picker's answer: `slots` as (index, recipe id or None), `choices` as (index, ids)."""
    return tool_call(
        "PickerAnswer",
        {
            "choices": [{"index": i, "ids": list(ids)} for i, ids in choices],
            "slots": [{"index": i, "recipe_id": r or 0, "reason": ""} for i, r in slots],
            "note": "",
            "wish_met": "no_wish",
        },
    )


def test_a_turn_that_left_the_plan_does_not_move_the_baseline(env):
    """The plan was made for shopping. A wish nothing fits keeps the plan; the next message
    asks for the fridge, and the change must take it (the baseline is the plan's request)."""
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    egg = ids_of(env, "番茄炒蛋")[0]
    out = routed_revise(
        env, "换个辣的", {"wish": "辣的", "wish_targets": [egg]},
        [picked(slots=[(0, None)])],
    )
    assert out["current_plan"] == before["current_plan"]
    assert out["request"]["query"] == "辣的"
    routed_revise(
        env, "还是只用冰箱里的吧", {"conditions": {"mode": "fridge_only"}},
        [picked(slots=[(0, None), (1, None), (2, None)])],
    )
    assert last_record(env).request["spec"]["mode"] == "fridge_only"


def test_a_side_count_asked_again_after_a_failed_turn_still_counts(env):
    """The plan has one meat dish. Two meat dishes fail; asking for two again must still ask
    for two, not drop the side as equal to an earlier request."""
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    reading = {"counts": {"meat_dishes": 2}}
    routed_revise(env, "要两个荤菜", reading, [picked(slots=[(0, None)], choices=[(0, [ids_of(env, "番茄炒蛋")[0]])])])
    spec = last_record(env).request["spec"]
    assert (spec["meat_dishes"], spec["meat_bound"]) == (2, "exact")
    routed_revise(env, "还是要两个荤菜", reading, [picked(slots=[(0, None)])])
    spec = last_record(env).request["spec"]
    assert (spec["meat_dishes"], spec["meat_bound"]) == (2, "exact")


def test_a_dish_deleted_from_the_library_leaves_the_change_quietly(env):
    """A dish deleted since the plan was shown leaves the change: the reader and the picker
    never see it, the new version drops it with a quiet note, and the counts are the plan's
    without it."""
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    eggplant, beef, cabbage = ids_of(env, "鱼香茄子", "土豆炖牛肉", "清炒小白菜")
    env.session.delete(env.dishes["清炒小白菜"])
    env.session.flush()
    swap = {"remove": [{"id": eggplant, "mode": "replace", "quote": "茄子这道"}]}
    out = routed_revise(env, "茄子这道不要，换一个别的", swap, [picked(slots=[(0, beef)])])
    plan = out["current_plan"]
    assert set(recipe_ids(plan)) == set(ids_of(env, "番茄炒蛋", "土豆炖牛肉"))
    assert plan["passed"] is True
    assert plan["notes"] == [{"field": "deleted_recipes", "titles": ["清炒小白菜"]}]
    assert out["messages"][-1].content.startswith("把鱼香茄子换成了土豆炖牛肉。")
    sent = [str(m.content) for call in env.model.sent if call for m in call]
    assert not any(f"{cabbage} 清炒小白菜" in text for text in sent)
    record = last_record(env)
    assert record.tools_version == PICK_TOOLS_VERSION
    assert record.request["spec"]["dishes"] == 2


def test_overrides_carry_into_a_change(env, session):
    """A dish the user kept against a rule stays locked and kept through a change: its
    override carries, so the rule it breaks is no reason to replace it."""
    me = self_diner(session)
    rule(session, me.id, "dietary restriction", ingredient="五花肉")
    first_plan(env, "番茄炒蛋", "清炒小白菜", parse={"dishes": 2, "mode": "shopping"})
    pork, egg, beans = ids_of(env, "红烧肉", "番茄炒蛋", "干煸豆角")
    edited = hand_edit(env, Add(recipe_id=pork))
    [override] = edited["current_plan"]["edit"]["overrides"]
    swap = {"remove": [{"id": egg, "mode": "replace", "quote": "番茄"}]}
    out = routed_revise(env, "番茄这道换一个", swap, [picked(slots=[(0, beans)])])
    plan = out["current_plan"]
    assert set(recipe_ids(plan)) == set(ids_of(env, "清炒小白菜", "红烧肉", "干煸豆角"))
    assert plan["passed"] is True and plan["edited"] is False
    assert plan["edit"]["overrides"] == [override]
    assert plan["edit"]["conflicts"] == []
    assert plan["locked"] == [pork]
    # The manual version is the last entry, so the change starts from it.
    assert plan["revision"] == {"from_version": 1} and plan["model_version"] == 2
    [frame] = [e for e in last_record(env).events if e["kind"] == "frame"]
    assert frame["breaking"] == []


def test_locked_changed_text_in_both_languages():
    from minemydinner.agent.revise_step import empty_plan_question, locked_changed_text

    changes = [("红烧肉", "清炒小白菜", True), ("番茄炒蛋", None, False)]
    assert locked_changed_text(changes, "zh") == (
        "红烧肉是你手动加的，按你这次的话换成了清炒小白菜。番茄炒蛋是你确认保留的，按你这次的话去掉了。"
    )
    assert locked_changed_text(changes, "en") == (
        "红烧肉 was added by you; as your message asked, it was replaced with 清炒小白菜. "
        "You had chosen to keep 番茄炒蛋; as your message asked, it was removed."
    )
    # The hook for a one-meal avoidance's reason (Task 5).
    assert locked_changed_text(changes[:1], "zh", reason="今天不吃肉") == (
        "红烧肉是你手动加的，因为今天不吃肉，换成了清炒小白菜。"
    )
    assert empty_plan_question("en") == EMPTY_PLAN_QUESTION_EN
    assert empty_plan_question("zh") == EMPTY_PLAN_QUESTION_ZH


# ---- plan 3b-1b Task 5: one-meal avoidances (spec 5.4) ----

def test_meal_avoid_group_word_covers_every_meat_entry(env):
    """"肉" is no dictionary entry: it expands to every meat entry, so dishes whose names hold
    no 肉 are left out too, and a vegetable dish stays."""
    from minemydinner.agent.plan_card import meal_rules
    from minemydinner.planning.dietary import check_recipes

    ribs = make_recipe(env.session, "红烧排骨", "meat dish", [L("排骨", "main")])
    brisket = make_recipe(env.session, "土豆炖牛腩", "meat dish", [L("牛腩"), L("土豆")])
    leg = make_recipe(env.session, "香煎鸡腿", "meat dish", [L("鸡腿", "main")])
    cabbage = env.dishes["清炒小白菜"]
    rules = meal_rules(env.session, ["肉"])
    assert rules and all(r.diner_key == "meal" and r.text is None for r in rules)
    check = check_recipes(env.session, rules, [ribs.id, brisket.id, leg.id, cabbage.id])
    assert {c.recipe_id for c in check.hard} == {ribs.id, brisket.id, leg.id}
    # Another word is matched as before: a dictionary entry, else a text rule.
    assert [r.text for r in meal_rules(env.session, ["不存在的食材"])] == ["不存在的食材"]
    assert meal_rules(env.session, ["meat"]) == rules


def test_no_plan_reason_names_the_meal_wish():
    from minemydinner.agent.card_text import no_plan_reason

    assert no_plan_reason("H2", "zh", ["肉"]) == "这顿不吃肉，菜谱库里没有足够不含它的菜"
    assert no_plan_reason("H2", "en", ["pork"]) == "No plan fits leaving out pork at this meal"
    assert "diner" in no_plan_reason("H2", "en")

