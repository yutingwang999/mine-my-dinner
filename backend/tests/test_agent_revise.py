"""The revise step of the planning graph (plan 3b-1 Task 4). A test enters it by writing the
turn as route_intent's output with intent "revise"; the scripted parse then runs as on a real
revise turn. The real route path is tested in test_agent_graph.py."""

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from minemydinner.agent.edits import Add, apply_edit
from minemydinner.agent.graph import MAX_RETRIES, GraphDeps, build_graph
from minemydinner.agent.plan_card import meal_constraints
from minemydinner.agent.planner import REVISE_TOOLS_VERSION
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import PROMPT_VERSION, set_request_style
from minemydinner.agent.revise_step import (
    EMPTY_PLAN_QUESTION_EN,
    EMPTY_PLAN_QUESTION_ZH,
    count_fix_hint,
)
from minemydinner.models import AppPreference, PlanningRun
from minemydinner.planning.context import load_planning_context
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import (
    L,
    make_recipe,
    rollback_session_factory,
    rule,
    self_diner,
    stock,
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


def proposal(target: str, remove=(), add=(), note: str = "", **fields):
    """`remove` holds (id, quote) or (id, quote, mode) tuples, or full dicts."""
    removed = [
        item if isinstance(item, dict) else dict(zip(("id", "quote", "mode"), item))
        for item in remove
    ]
    args = {"target": target, "remove": removed, "add": list(add), "note": note, **fields}
    return tool_call("RevisionProposal", args)


def revise(env: Env, text: str, replies: list, parse: dict | None = None) -> dict:
    """A follow-up turn routed to the revise step. The revision compares the parse with the
    request the current plan was built from (`plan_request`), so a field counts as changed
    when the parse states it with a value that differs from that request."""
    env.model.replies = list(replies)
    env.model.structured["ParsedRequest"] = [parse or {}]
    env.graph.update_state(
        env.config,
        {"messages": [HumanMessage(text)], "intent": "revise", "model_calls": []},
        as_node="route_intent",
    )
    return env.graph.invoke(None, env.config)


def last_record(env: Env) -> PlanningRun:
    env.session.expire_all()
    return env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()


def reviser_requests(env: Env) -> list[list]:
    """The messages of each revise model call."""
    return [
        sent
        for sent in env.model.sent
        if sent and sent[0].type == "system" and "RevisionProposal" in sent[0].content
    ]


def reply_plan(out: dict) -> dict:
    return out["reply_plans"][out["messages"][-1].id]


def test_named_swap_keeps_the_rest(env):
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    eggplant, beef = ids_of(env, "鱼香茄子", "土豆炖牛肉")
    message = "茄子这道不要，换一个别的"
    out = revise(
        env,
        message,
        [proposal("named", remove=[(eggplant, "茄子这道")], add=[beef], note="换了道炖菜。")],
    )
    plan = out["current_plan"]
    assert recipe_ids(plan) == ids_of(env, "番茄炒蛋", "清炒小白菜", "土豆炖牛肉")
    assert plan["passed"] is True and plan["edited"] is False
    assert out["messages"][-1].content.startswith("把鱼香茄子换成了土豆炖牛肉。")
    assert out["messages"][-1].content.endswith("换了道炖菜。")
    assert reply_plan(out) == plan
    versions = out["plan_versions"]
    assert len(versions) == len(before["plan_versions"]) + 1
    assert versions[-1] == plan
    assert plan["revision"] == {"from_version": 0}
    assert plan["model_version"] == 1
    assert plan["locked"] == []
    assert plan["edit"] == {"overrides": [], "log": [], "conflicts": [], "kept": []}
    # The opening shows the current plan and the message.
    [opening] = [m for m in reviser_requests(env)[0] if m.type == "human"]
    assert "Revise this meal." in opening.content
    assert "Plan this meal." not in opening.content
    assert f"{eggplant} 鱼香茄子 (meat dish; 茄子)" in opening.content
    assert opening.content.endswith(f"The user's message: {message}")
    record = last_record(env)
    assert record.request["revision"] == {"from_version": 0}
    assert record.final_passed is True and record.first_passed is True
    assert record.prompt_version == PROMPT_VERSION
    assert record.tools_version == REVISE_TOOLS_VERSION
    kinds = [(e["kind"], e.get("step")) for e in record.events]
    assert ("model_call", "revise") in kinds and ("validation", None) in kinds


def test_locked_dish_removed_by_general_wish_is_retried(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    pepper, egg, beans = ids_of(env, "青椒炒蛋", "番茄炒蛋", "干煸豆角")
    hand_edit(env, Add(recipe_id=pepper))
    out = revise(
        env,
        "想要辣一些的",
        [
            proposal("general", remove=[(pepper, "")], add=[beans]),
            proposal("general", remove=[(egg, "")], add=[beans]),
        ],
        parse={"query": "辣一些的"},
    )
    plan = out["current_plan"]
    assert pepper in recipe_ids(plan) and egg not in recipe_ids(plan)
    assert plan["passed"] is True and out["retries"] == 1
    [opening, retry] = [m for m in reviser_requests(env)[1] if m.type == "human"]
    assert f"{pepper} 青椒炒蛋 (vegetable dish; 青椒, 鸡蛋) LOCKED" in opening.content
    assert "is locked and the message does not name it" in retry.content
    record = last_record(env)
    validations = [e for e in record.events if e["kind"] == "validation"]
    assert [v["problems"][0]["code"] for v in validations[:1]] == ["R3"]
    assert validations[1]["problems"] == [] and validations[1]["passed"] is True
    assert (record.first_passed, record.final_passed, record.retries) == (False, True, 1)


def test_overrides_carry_into_revision(env, session):
    me = self_diner(session)
    rule(session, me.id, "dietary restriction", ingredient="五花肉")
    first_plan(env, "番茄炒蛋", "清炒小白菜", parse={"dishes": 2, "mode": "shopping"})
    pork, egg, beans = ids_of(env, "红烧肉", "番茄炒蛋", "干煸豆角")
    edited = hand_edit(env, Add(recipe_id=pork))
    [override] = edited["current_plan"]["edit"]["overrides"]
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", remove=[(egg, "")], add=[beans])],
        parse={"query": "辣一些的"},
    )
    plan = out["current_plan"]
    assert recipe_ids(plan) == ids_of(env, "清炒小白菜", "红烧肉", "干煸豆角")
    assert plan["passed"] is True and plan["edited"] is False
    assert plan["edit"]["overrides"] == [override]
    assert plan["edit"]["conflicts"] == []
    assert [k["recipe_id"] for k in plan["edit"]["kept"]] == [pork]
    assert plan["locked"] == [pork]
    # The manual version is the last entry, so the revision starts from it.
    assert plan["revision"] == {"from_version": 1} and plan["model_version"] == 2
    record = last_record(env)
    [validation] = [e for e in record.events if e["kind"] == "validation"]
    assert all(v["code"] != "H2" for v in validation["result"]["violations"])


def test_failed_revision_leaves_plan_unchanged(env):
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    egg, beef = ids_of(env, "番茄炒蛋", "土豆炖牛肉")
    # A vegetable dish swapped for a meat dish breaks the plan's split every time.
    out = revise(
        env,
        "不要这么多鸡蛋的",
        [proposal("general", remove=[(egg, "")], add=[beef])],
        parse={"query": "少一些鸡蛋"},
    )
    assert out["retries"] == MAX_RETRIES
    assert out["current_plan"] == before["current_plan"]
    assert out["plan_versions"] == before["plan_versions"]
    shown = reply_plan(out)
    assert recipe_ids(shown) == recipe_ids(before["current_plan"])
    assert shown["passed"] is False
    # The type pairing (R7) stops it before the counts: the attempt's counts follow the
    # change list, so H4 cannot fire.
    assert [u["code"] for u in shown["unmet"]] == ["R7"]
    assert "同一类" in shown["unmet"][0]["text"]
    assert "relax_split" in shown["next_actions"]
    assert out["messages"][-1].content.startswith("没能按这次的要求改好方案")
    record = last_record(env)
    assert (record.final_passed, record.retries) == (False, MAX_RETRIES)
    assert record.request["revision"] == {"from_version": 0}


def test_count_fix_hint_names_the_categories_that_are_off():
    detail = {
        "required": {"dishes": 4, "soups": 1, "staples": 0, "desserts": 0, "snacks": 0},
        "present": {
            "dishes": 4,
            "soups": 0,
            "meat_dishes": 3,
            "vegetable_dishes": 1,
            "staples": 0,
            "desserts": 0,
            "snacks": 1,
        },
        "ranges": {"meat_dishes": [2, 2], "vegetable_dishes": [2, 2]},
    }
    replaceable = {
        "meat_dishes": [(12, "清蒸鲈鱼"), (18, "土豆炖牛腩")],
        "vegetable_dishes": [(3, "清炒小白菜")],
    }
    hint = count_fix_hint(detail, replaceable)
    assert hint.startswith("The revised plan must have 4 dishes: 2 meat dishes and 2 vegetable dishes.")
    assert "Too many: 1 meat dish, 1 snack." in hint
    assert "Too few: 1 vegetable dish, 1 soup." in hint
    assert "same dish type" in hint and "meat dish for a meat dish" in hint
    # The dishes of the type that is over are named, the other side's are not.
    assert hint.endswith("Replace one of these meat dishes: 清蒸鲈鱼 (id 12), 土豆炖牛腩 (id 18).")
    assert "清炒小白菜" not in hint
    # A total that is off while the sides are free is a dish of either side.
    free = {
        "required": {"dishes": 3, "soups": 0, "staples": 0, "desserts": 0, "snacks": 0},
        "present": {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1, "soups": 0},
        "ranges": {"meat_dishes": [0, 3], "vegetable_dishes": [0, 3]},
    }
    hint = count_fix_hint(free, {})
    assert hint.startswith("The revised plan must have 3 dishes. Too few: 1 dish (meat or vegetable).")
    assert "Too many" not in hint
    # A side given as a range keeps the total in front of it.
    ranged = {**free, "ranges": {"meat_dishes": [2, 3], "vegetable_dishes": [0, 3]}}
    assert count_fix_hint(ranged, {}).startswith(
        "The revised plan must have 3 dishes: at least 2 meat dishes."
    )


def test_wrong_type_is_retried_with_the_dishes_named(env):
    """Plan 3b-1b spec 5.2: a vegetable dish swapped for a meat dish fails the type pairing
    (R7) on the first attempt, and the retry names the meat dishes that may be replaced."""
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    eggplant, egg, pork, beans = ids_of(env, "鱼香茄子", "番茄炒蛋", "红烧肉", "干煸豆角")
    out = revise(
        env,
        "想要辣一些的",
        [
            proposal("general", remove=[(egg, "")], add=[pork]),
            proposal("general", remove=[(egg, "")], add=[beans]),
        ],
        parse={"query": "辣一些的"},
    )
    assert out["current_plan"]["passed"] is True and out["retries"] == 1
    assert recipe_ids(out["current_plan"]) == ids_of(env, "鱼香茄子", "清炒小白菜", "干煸豆角")
    retry = [m for m in reviser_requests(env)[1] if m.type == "human"][-1].content
    assert "so its replacement must be a vegetable dish too" in retry
    assert "must have 3 dishes: 1 meat dish and 2 vegetable dishes." in retry
    assert "Too many: 1 meat dish." in retry and "Too few: 1 vegetable dish." in retry
    assert f"Replace one of these meat dishes: 鱼香茄子 (id {eggplant})." in retry
    record = last_record(env)
    validations = [e for e in record.events if e["kind"] == "validation"]
    assert [p["code"] for p in validations[0]["problems"]] == ["R7"]
    assert all(v["code"] != "H4" for v in validations[0]["result"]["violations"])


def test_unclear_asks_and_changes_nothing(env):
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    out = revise(env, "那道汤不要了", [proposal("unclear", note="你说的是哪一道汤？")])
    assert out["messages"][-1].content == "你说的是哪一道汤？"
    assert out["current_plan"] == before["current_plan"]
    assert out["plan_versions"] == before["plan_versions"]
    shown = reply_plan(out)
    assert recipe_ids(shown) == recipe_ids(before["current_plan"])
    assert shown["passed"] is False and shown["unmet"] == []
    record = last_record(env)
    assert record.final_passed is False and record.retries == 0


def test_locked_untouched_sentence(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    pepper, egg, beans = ids_of(env, "青椒炒蛋", "番茄炒蛋", "干煸豆角")
    hand_edit(env, Add(recipe_id=pepper))
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", remove=[(egg, "")], add=[beans])],
        parse={"query": "辣一些的"},
    )
    text = out["messages"][-1].content
    assert text.startswith("把番茄炒蛋换成了干煸豆角。")
    assert "青椒炒蛋是你手动加的，没有改动。" in text
    assert out["current_plan"]["locked"] == [pepper]
    assert out["current_plan"]["revision"] == {"from_version": 1}


def test_manual_edit_works_on_a_failed_revisions_card(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    egg, beef = ids_of(env, "番茄炒蛋", "土豆炖牛肉")
    out = revise(
        env, "不要这么多鸡蛋的", [proposal("general", remove=[(egg, "")], add=[beef])]
    )
    assert reply_plan(out)["passed"] is False
    from minemydinner.agent.edits import Remove

    values = hand_edit(env, Remove(recipe_id=egg))
    plan = values["current_plan"]
    assert plan["edited"] is True and egg not in recipe_ids(plan)
    assert len(values["plan_versions"]) == 2


def test_only_locked_dishes_may_keep_h1_h3_h6_h7():
    from minemydinner.agent.revise_step import split_violations
    from minemydinner.planning.validate import Finding

    findings = [
        Finding(code="H1", recipe_id=1, detail={"missing": ["x"]}),
        Finding(code="H1", recipe_id=2, detail={"missing": ["x"]}),
        Finding(code="H6", recipe_id=1, detail={"time_min": 90, "limit": 30}),
        Finding(code="H7", detail={"name": "鸡蛋", "rule": "all", "recipes_without": [1]}),
        Finding(code="H7", detail={"name": "鸡蛋", "rule": "all", "recipes_without": [1, 2]}),
        Finding(code="H7", detail={"name": "鸡蛋", "rule": "must", "recipes_without": [1]}),
        Finding(code="H2", recipe_id=1, detail={"diner_key": "diner:1"}),
        Finding(code="H4", detail={}),
    ]
    failing, allowed = split_violations(findings, {1})
    assert [(f.code, f.recipe_id) for f in allowed] == [("H1", 1), ("H6", 1), ("H7", None)]
    assert allowed[2].detail["recipes_without"] == [1]
    assert [f.code for f in failing] == ["H1", "H7", "H7", "H2", "H4"]


def test_from_version_names_the_version_the_plan_is():
    from minemydinner.agent.revise_step import from_version

    model, manual = {"recipes": []}, {"recipes": [], "edited": True, "model_version": 0}
    assert from_version(model, [model]) == 0
    assert from_version(manual, [model, manual]) == 1
    restored = {"recipes": [], "edited": False, "model_version": 0}
    assert from_version(restored, [model, manual]) == 0
    newer = {"recipes": []}
    assert from_version(newer, [model, manual, newer]) == 2


def test_open_conflict_dish_left_in_the_plan_fails(env, session):
    me = self_diner(session)
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    # A rule added after the plan: the egg dish now has an open conflict on the card.
    rule(session, me.id, "allergy", allergen="egg")
    egg, cabbage, beans = ids_of(env, "番茄炒蛋", "清炒小白菜", "干煸豆角")
    from minemydinner.agent.edits import Remove

    values = hand_edit(env, Remove(recipe_id=cabbage))
    assert [c["recipe_id"] for c in values["current_plan"]["edit"]["conflicts"]] == [egg]
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", add=[beans])],
        parse={"query": "辣一些的", "dishes": 3},
    )
    [opening] = [m for m in reviser_requests(env)[0] if m.type == "human"]
    assert f"{egg} 番茄炒蛋 (vegetable dish; 番茄, 鸡蛋) OPEN CONFLICT" in opening.content
    shown = reply_plan(out)
    assert shown["passed"] is False and "H2" in [u["code"] for u in shown["unmet"]]
    assert out["current_plan"] == values["current_plan"]


def test_nothing_fits_is_a_failed_revision_with_next_actions(env):
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", note="库里没有更辣的菜了。")],
        parse={"query": "辣一些的"},
    )
    assert out["retries"] == 0
    assert out["current_plan"] == before["current_plan"]
    assert out["plan_versions"] == before["plan_versions"]
    shown = reply_plan(out)
    assert recipe_ids(shown) == recipe_ids(before["current_plan"])
    assert shown["passed"] is False
    assert [u["code"] for u in shown["unmet"]] == ["wish_unmet"]
    assert shown["unmet"][0]["text"] == "你的菜谱库里没有找到符合这次要求的菜"
    assert shown["next_actions"] == ["import_recipes"]
    text = out["messages"][-1].content
    assert text.startswith("没能按这次的要求改好方案") and text.endswith("库里没有更辣的菜了。")
    record = last_record(env)
    assert record.final_passed is False
    [validation] = [e for e in record.events if e["kind"] == "validation"]
    assert validation["passed"] is True


def test_open_conflict_with_an_empty_change_is_retried_and_fails_h2(env, session):
    me = self_diner(session)
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    rule(session, me.id, "allergy", allergen="egg")
    from minemydinner.agent.edits import Remove

    values = hand_edit(env, Remove(recipe_id=ids_of(env, "清炒小白菜")[0]))
    out = revise(
        env, "想要辣一些的", [proposal("general", note="没有更合适的。")],
        parse={"query": "辣一些的"},
    )
    assert out["retries"] == MAX_RETRIES
    shown = reply_plan(out)
    assert shown["passed"] is False and "H2" in [u["code"] for u in shown["unmet"]]
    assert out["current_plan"] == values["current_plan"]
    retry = [m for m in reviser_requests(env)[1] if m.type == "human"][-1]
    assert "breaks a dietary limit" in retry.content


def test_kept_dish_is_named_with_its_own_wording(env, session):
    from minemydinner.agent.edits import Keep

    me = self_diner(session)
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    rule(session, me.id, "allergy", allergen="egg")
    egg, cabbage, beans = ids_of(env, "番茄炒蛋", "清炒小白菜", "干煸豆角")
    hand_edit(env, Keep(recipe_id=egg))
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", remove=[(cabbage, "")], add=[beans])],
        parse={"query": "辣一些的"},
    )
    text = out["messages"][-1].content
    assert text.startswith("把清炒小白菜换成了干煸豆角。番茄炒蛋是你确认保留的，没有改动。")
    assert "手动加的" not in text
    # The model sees which locked dish was kept despite a conflict (plan 3b-1b spec 5.4).
    [opening] = [m for m in reviser_requests(env)[0] if m.type == "human"]
    assert f"{egg} 番茄炒蛋 (vegetable dish; 番茄, 鸡蛋) LOCKED KEPT CONFLICT" in opening.content


def test_add_only_turn_names_no_untouched_dish(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    pepper, beans = ids_of(env, "青椒炒蛋", "干煸豆角")
    hand_edit(env, Add(recipe_id=pepper))
    out = revise(
        env, "再加一道吧", [proposal("general", add=[beans])], parse={"dishes": 5}
    )
    assert out["current_plan"]["passed"] is True
    assert out["messages"][-1].content == "加了干煸豆角。"


def test_must_use_the_plan_already_missed_does_not_fail_a_swap(env):
    from minemydinner.agent.edits import Remove

    parse = {"dishes": 3, "mode": "shopping", "must_use": ["茄子"]}
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜", parse=parse)
    eggplant, egg, pepper = ids_of(env, "鱼香茄子", "番茄炒蛋", "青椒炒蛋")
    hand_edit(env, Remove(recipe_id=eggplant))
    out = revise(
        env,
        "番茄这道不要，换一个别的",
        [proposal("named", remove=[(egg, "番茄这道")], add=[pepper])],
    )
    plan = out["current_plan"]
    assert plan["passed"] is True and pepper in recipe_ids(plan)
    assert any(
        w["code"] == "H7" and w["detail"]["rule"] == "must" for w in plan["warnings"]
    )


def test_must_use_the_revision_breaks_fails():
    from minemydinner.agent.revise_step import split_violations
    from minemydinner.planning.validate import Finding

    must = Finding(code="H7", detail={"name": "茄子", "rule": "must", "recipes_without": []})
    assert split_violations([must], set(), {"茄子"}) == ([], [must])
    assert split_violations([must], set(), {"玉米"}) == ([must], [])


def routed_revise(env: Env, text: str, replies: list, parse: dict) -> dict:
    """A follow-up turn through the real route (scripted as revise), so route_intent keeps the
    thread's request as `previous_request` as it does in the app."""
    env.model.replies = list(replies)
    env.model.structured["Intent"] = [{"intent": "revise"}]
    env.model.structured["ParsedRequest"] = [parse]
    return env.graph.invoke({"messages": [HumanMessage(text)], "locale": "zh"}, env.config)


def test_a_turn_that_left_the_plan_does_not_move_the_baseline(env):
    """The plan was made for shopping. A wish to cook from the fridge finds nothing, so the
    plan stays; the next message still asks for the fridge, and the revision must take it."""
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    out = routed_revise(
        env,
        "只用冰箱里的，换个辣的",
        [proposal("general", note="没有合适的。")],
        parse={"mode": "fridge_only", "query": "辣的"},
    )
    assert out["current_plan"] == before["current_plan"]
    assert out["request"]["mode"] == "fridge_only"
    routed_revise(
        env,
        "还是只用冰箱里的吧",
        [proposal("general", note="没有合适的。")],
        parse={"mode": "fridge_only"},
    )
    assert last_record(env).request["spec"]["mode"] == "fridge_only"


def test_a_side_count_asked_again_after_a_failed_turn_still_counts(env):
    """The plan has one meat dish. Two meat dishes fail; asking for two again must still ask
    for two, not drop the side as a split carried over from an earlier message."""
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    parse = {"meat_dishes": 2, "counts_stated_now": True}
    out = routed_revise(env, "要两个荤菜", [proposal("general", note="没有合适的。")], parse)
    assert out["current_plan"] == before["current_plan"]
    routed_revise(env, "还是要两个荤菜", [proposal("general", note="没有合适的。")], parse)
    spec = last_record(env).request["spec"]
    assert (spec["meat_dishes"], spec["meat_bound"]) == (2, "exact")


def test_a_dish_deleted_from_the_library_leaves_the_revision_quietly(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    eggplant, beef, cabbage = ids_of(env, "鱼香茄子", "土豆炖牛肉", "清炒小白菜")
    env.session.delete(env.dishes["清炒小白菜"])
    env.session.flush()
    out = revise(
        env,
        "茄子这道不要，换一个别的",
        [proposal("named", remove=[(eggplant, "茄子这道")], add=[beef])],
    )
    assert out["retries"] == 0
    plan = out["current_plan"]
    assert recipe_ids(plan) == ids_of(env, "番茄炒蛋", "土豆炖牛肉")
    assert plan["passed"] is True
    assert plan["notes"] == [{"field": "deleted_recipes", "titles": ["清炒小白菜"]}]
    assert out["messages"][-1].content.startswith("把鱼香茄子换成了土豆炖牛肉。")
    # The model never sees the deleted dish.
    [opening] = [m for m in reviser_requests(env)[0] if m.type == "human"]
    assert f"{cabbage} 清炒小白菜" not in opening.content
    # The revision keeps the plan's size without the deleted dish.
    assert last_record(env).request["spec"]["dishes"] == 2


def test_failed_card_drops_a_deleted_dish(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    ids_before = ids_of(env, "鱼香茄子", "番茄炒蛋")
    env.session.delete(env.dishes["清炒小白菜"])
    env.session.flush()
    out = revise(
        env,
        "想要辣一些的",
        [proposal("general", note="库里没有更辣的菜了。")],
        parse={"query": "辣一些的"},
    )
    shown = reply_plan(out)
    assert shown["passed"] is False
    assert recipe_ids(shown) == ids_before
    assert shown["notes"] == [{"field": "deleted_recipes", "titles": ["清炒小白菜"]}]


# ---- plan 3b-1b: drop or replace, add-only, the attempt's counts, the empty plan ----


def four_dish_plan(env: Env) -> dict:
    """2 meat dishes, 1 vegetable dish and 1 soup."""
    env.dishes["紫菜蛋花汤"] = make_recipe(
        env.session, "紫菜蛋花汤", "soup", [L("紫菜", "main"), L("鸡蛋")]
    )
    return first_plan(
        env,
        "鱼香茄子",
        "土豆炖牛肉",
        "番茄炒蛋",
        "紫菜蛋花汤",
        parse={"dishes": 3, "soups": 1, "mode": "shopping"},
    )


def counts_of(plan: dict) -> tuple[int, int]:
    """(dishes, soups) of a card's dishes."""
    types = [r["dish_type"] for r in plan["recipes"]]
    return len(types), types.count("soup")


def test_bare_drop_shrinks_the_plan(env):
    before = four_dish_plan(env)
    [soup] = ids_of(env, "紫菜蛋花汤")
    out = revise(env, "汤不要了", [proposal("named", remove=[(soup, "汤", "drop")])])
    plan = out["current_plan"]
    assert plan["passed"] is True and out["retries"] == 0
    assert counts_of(plan) == (3, 0)
    assert soup not in recipe_ids(plan)
    # The new version's own spec matches its dishes, so a later hand edit checks against it.
    assert (plan["spec"]["dishes"], plan["spec"]["soups"]) == (3, 0)
    assert len(out["plan_versions"]) == len(before["plan_versions"]) + 1
    assert out["messages"][-1].content.startswith("去掉了紫菜蛋花汤。")


def test_replace_keeps_the_size(env):
    four_dish_plan(env)
    eggplant, pork = ids_of(env, "鱼香茄子", "红烧肉")
    out = revise(
        env,
        "茄子换成别的",
        [proposal("named", remove=[(eggplant, "茄子", "replace")], add=[pork])],
    )
    plan = out["current_plan"]
    assert plan["passed"] is True
    assert counts_of(plan) == (4, 1)
    assert out["messages"][-1].content.startswith("把鱼香茄子换成了红烧肉。")


def test_add_only_adds(env):
    before = four_dish_plan(env)
    [cabbage] = ids_of(env, "清炒小白菜")
    out = revise(env, "加个青菜", [proposal("add", add=[cabbage])])
    plan = out["current_plan"]
    assert plan["passed"] is True
    assert recipe_ids(plan) == [*recipe_ids(before["current_plan"]), cabbage]
    assert out["messages"][-1].content == "加了清炒小白菜。"


def test_named_replacement_of_another_type_passes_in_the_graph(env):
    """Spec 5.2: the user names the replacement dish, so another dish type is fine. The
    named dish's quote is matched against its own facts (hand-off from Task 2)."""
    env.dishes["青椒肉丝"] = make_recipe(
        env.session, "青椒肉丝", "meat dish", [L("猪肉", "main"), L("青椒")]
    )
    # Lines without a dictionary entry: the test dictionary has no broccoli or garlic.
    lines = [L("西兰花", "main", linked=False), L("大蒜", linked=False)]
    env.dishes["蒜蓉西兰花"] = make_recipe(env.session, "蒜蓉西兰花", "vegetable dish", lines)
    first_plan(env, "青椒肉丝", "鱼香茄子", "清炒小白菜")
    pork, broccoli = ids_of(env, "青椒肉丝", "蒜蓉西兰花")
    removed = {
        "id": pork,
        "quote": "青椒肉丝",
        "mode": "replace",
        "replace_with": broccoli,
        "replace_with_quote": "蒜蓉西兰花",
    }
    out = revise(
        env, "把青椒肉丝换成蒜蓉西兰花", [proposal("named", remove=[removed], add=[broccoli])]
    )
    assert out["retries"] == 0
    plan = out["current_plan"]
    assert plan["passed"] is True
    assert recipe_ids(plan) == ids_of(env, "鱼香茄子", "清炒小白菜", "蒜蓉西兰花")
    assert out["messages"][-1].content.startswith("把青椒肉丝换成了蒜蓉西兰花。")


def test_empty_plan_asks(env):
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    message = "全都不要了"
    removed = [(i, "", "drop") for i in recipe_ids(before["current_plan"])]
    out = revise(
        env,
        message,
        [proposal("scope", remove=removed, scope_quote=message, note="好的。")],
    )
    assert out["retries"] == 0
    assert len(reviser_requests(env)) == 1
    assert out["current_plan"] == before["current_plan"]
    assert out["plan_versions"] == before["plan_versions"]
    reply = out["messages"][-1]
    assert reply.content == EMPTY_PLAN_QUESTION_ZH
    # A plain-text question: no card follows the reply.
    assert reply.id not in out["reply_plans"]
    assert out["spec"] == before["current_plan"]["spec"]
    record = last_record(env)
    assert record.final_passed is False and record.retries == 0
    validations = [e for e in record.events if e["kind"] == "validation"]
    assert "R9" in [p["code"] for p in validations[0]["problems"]]
    assert EMPTY_PLAN_QUESTION_EN == "That would leave no dish. Shall I plan a new meal?"


def test_stated_count_still_wins(env):
    first_plan(
        env,
        "鱼香茄子",
        "土豆炖牛肉",
        "番茄炒蛋",
        "清炒小白菜",
        "红烧肉",
        parse={"dishes": 5, "mode": "shopping"},
    )
    [cabbage] = ids_of(env, "清炒小白菜")
    out = revise(
        env,
        "4个菜就够了",
        [
            proposal("general", note="没有要改的。"),
            proposal("general", remove=[(cabbage, "", "drop")]),
        ],
        parse={"dishes": 4, "counts_stated_now": True},
    )
    # The empty first answer is checked against the stated 4 (H4), as in 3b-1; the drop then
    # passes because the message lowers the count.
    assert out["retries"] == 1
    plan = out["current_plan"]
    assert plan["passed"] is True and len(plan["recipes"]) == 4
    record = last_record(env)
    validations = [e for e in record.events if e["kind"] == "validation"]
    assert "H4" in [v["code"] for v in validations[0]["result"]["violations"]]
    assert record.request["spec"]["dishes"] == 4


def test_locked_dish_changed_by_name_is_named_in_the_reply(env):
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    pepper, beans = ids_of(env, "青椒炒蛋", "干煸豆角")
    hand_edit(env, Add(recipe_id=pepper))
    out = revise(
        env,
        "青椒这道换一个",
        [proposal("named", remove=[(pepper, "青椒这道", "replace")], add=[beans])],
    )
    assert out["current_plan"]["passed"] is True
    text = out["messages"][-1].content
    assert text.startswith("青椒炒蛋是你手动加的，按你这次的话换成了干煸豆角。")
    assert "把青椒炒蛋换成了" not in text


def test_fridge_only_add_with_nothing_makeable_offers_shopping(env, session):
    """Hand-off from Task 2: a fridge-only "再加一道" that finds nothing more to make fails
    on the counts; its card must offer to allow shopping."""
    for name in ("番茄", "鸡蛋", "小白菜"):
        stock(session, name, count=3)
    first_plan(env, "番茄炒蛋", "清炒小白菜", parse={"dishes": 2, "mode": "fridge_only"})
    out = revise(
        env,
        "再加一道",
        [proposal("add", note="冰箱里没有能再做的菜了。")],
        parse={"dishes": 3, "counts_stated_now": True},
    )
    shown = reply_plan(out)
    assert shown["passed"] is False
    assert "switch_to_shopping" in shown["next_actions"]
    # The same when the model answers with an empty general change ("nothing fits").
    out = revise(
        env,
        "再加一道",
        [proposal("general", note="冰箱里没有能再做的菜了。")],
        parse={"dishes": 3, "counts_stated_now": True},
    )
    assert "switch_to_shopping" in reply_plan(out)["next_actions"]


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


def test_named_wrong_type_retry_names_no_other_dishes(env):
    """A named change must keep its dishes, so its type-fix hint lists no other dishes to
    replace; only a general wish gets that list."""
    first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    egg, pork, beans = ids_of(env, "番茄炒蛋", "红烧肉", "干煸豆角")
    message = "番茄这道换一个"
    out = revise(
        env,
        message,
        [
            proposal("named", remove=[(egg, "番茄这道", "replace")], add=[pork]),
            proposal("named", remove=[(egg, "番茄这道", "replace")], add=[beans]),
        ],
    )
    assert out["current_plan"]["passed"] is True and out["retries"] == 1
    retry = [m for m in reviser_requests(env)[1] if m.type == "human"][-1].content
    assert "To fix the dish types:" in retry and "same dish type" in retry
    assert "Replace one of these" not in retry


def test_unjustified_drop_of_every_dish_is_retried(env):
    """R9 with a quote problem is not the user's wish to clear the plan: the attempt is
    retried as usual instead of asking whether to plan a new meal."""
    before = first_plan(env, "鱼香茄子", "番茄炒蛋", "清炒小白菜")
    eggplant, egg, cabbage = recipe_ids(before["current_plan"])
    message = "番茄这道不要了"
    out = revise(
        env,
        message,
        [
            proposal(
                "named",
                remove=[
                    (eggplant, "茄子这道", "drop"),
                    (egg, "番茄这道", "drop"),
                    (cabbage, "小白菜这道", "drop"),
                ],
            ),
            proposal("named", remove=[(egg, "番茄这道", "drop")]),
        ],
    )
    assert out["retries"] == 1
    assert out["messages"][-1].content != EMPTY_PLAN_QUESTION_ZH
    assert recipe_ids(out["current_plan"]) == [eggplant, cabbage]
    validations = [e for e in last_record(env).events if e["kind"] == "validation"]
    codes = [p["code"] for p in validations[0]["problems"]]
    assert "R1" in codes and "R9" in codes


# ---- plan 3b-1b Task 5: one-meal avoidances (spec 5.4) ----

MEAL_AVOID_MEAT = {"meal_avoid": ["肉"], "counts_stated_now": False}


def meat_heavy_plan(env: Env) -> dict:
    """2 meat dishes, 1 vegetable dish and 1 soup, with no eggplant dish."""
    env.dishes["紫菜蛋花汤"] = make_recipe(
        env.session, "紫菜蛋花汤", "soup", [L("紫菜", "main"), L("鸡蛋")]
    )
    env.dishes["冬瓜汤"] = make_recipe(
        env.session, "冬瓜汤", "soup", [L("冬瓜", "main", linked=False)]
    )
    return first_plan(
        env,
        "红烧肉",
        "土豆炖牛肉",
        "番茄炒蛋",
        "紫菜蛋花汤",
        parse={"dishes": 3, "soups": 1, "mode": "shopping"},
    )


def opening_of(env: Env) -> str:
    return [m for m in reviser_requests(env)[-1] if m.type == "human"][0].content


def test_meal_avoid_replaces_meat_dishes_keeping_the_size(env):
    meat_heavy_plan(env)
    pork, beef, cabbage, melon = ids_of(env, "红烧肉", "土豆炖牛肉", "清炒小白菜", "冬瓜汤")
    out = revise(
        env,
        "今天不想吃肉",
        [
            proposal(
                "general",
                remove=[(pork, "", "replace"), (beef, "", "replace")],
                add=[cabbage, melon],
            )
        ],
        parse=MEAL_AVOID_MEAT,
    )
    plan = out["current_plan"]
    assert plan["passed"] is True and out["retries"] == 0
    # The size stays; the counts by type follow the replacements (spec 5.4).
    assert counts_of(plan) == (4, 2)
    assert not {pork, beef} & set(recipe_ids(plan))
    assert [r["dish_type"] for r in plan["recipes"]].count("meat dish") == 0
    # The reviser saw which dishes break this meal's wish, and the wish itself.
    opening = opening_of(env)
    lines = {line.split(" ")[2]: line for line in opening.splitlines() if line.startswith("- ")
             and len(line.split(" ")) > 2}
    assert lines["红烧肉"].endswith("BREAKS THIS MEAL'S WISH")
    assert lines["土豆炖牛肉"].endswith("BREAKS THIS MEAL'S WISH")
    assert "BREAKS" not in lines["番茄炒蛋"] and "BREAKS" not in lines["紫菜蛋花汤"]
    assert "This meal only, not a diner's rule: leave out 肉" in opening
    assert "Diner 2" not in opening


def test_meal_avoid_changes_a_locked_dish_and_says_so(env):
    first_plan(env, "番茄炒蛋", "清炒小白菜", parse={"dishes": 2, "mode": "shopping"})
    pork, pepper = ids_of(env, "红烧肉", "青椒炒蛋")
    hand_edit(env, Add(recipe_id=pork))
    out = revise(
        env,
        "今天不想吃肉",
        [proposal("general", remove=[(pork, "", "replace")], add=[pepper])],
        parse=MEAL_AVOID_MEAT,
    )
    plan = out["current_plan"]
    assert plan["passed"] is True and out["retries"] == 0
    assert pork not in recipe_ids(plan) and pepper in recipe_ids(plan)
    text = out["messages"][-1].content
    assert text.startswith("红烧肉是你手动加的，因为这顿不吃肉，换成了青椒炒蛋。")
    assert "把红烧肉换成了" not in text


def test_meal_avoid_dish_left_in_the_plan_fails(env):
    """A revision that keeps a dish breaking this meal's wish fails H2; the plan stays and
    the state goes back to the plan's own rules, so a hand edit is not checked against a wish
    the plan never met."""
    before = meat_heavy_plan(env)
    out = revise(
        env,
        "今天不想吃肉",
        [proposal("general", note="没有合适的菜。")] * (MAX_RETRIES + 1),
        parse=MEAL_AVOID_MEAT,
    )
    assert out["current_plan"] == before["current_plan"]
    validations = [e for e in last_record(env).events if e["kind"] == "validation"]
    h2 = [v for v in validations[0]["result"]["violations"] if v["code"] == "H2"]
    assert {v["detail"]["diner_key"] for v in h2} == {"meal"}
    assert out["constraints"]["guest_rules"] == before["constraints"]["guest_rules"]
    assert not out["constraints"].get("meal_avoid")
    # The card says the meal's wish could not be met, never a diner's rule (fix round 1).
    texts = [u["text"] for u in reply_plan(out)["unmet"] if u["code"] == "H2"]
    assert texts and set(texts) == {"这顿不吃肉，菜谱库里没有足够不含它的菜"}


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


def test_meal_avoid_carries_into_the_next_revision(env):
    """Spec 5.4 with 5.2's rule for conditions: a later parse that leaves the field empty
    never clears the plan's one-meal wish."""
    meat_heavy_plan(env)
    pork, beef, cabbage, melon, beans = ids_of(
        env, "红烧肉", "土豆炖牛肉", "清炒小白菜", "冬瓜汤", "干煸豆角"
    )
    revise(
        env,
        "今天不想吃肉",
        [
            proposal(
                "general",
                remove=[(pork, "", "replace"), (beef, "", "replace")],
                add=[cabbage, melon],
            )
        ],
        parse=MEAL_AVOID_MEAT,
    )
    assert reply_plan(env.graph.get_state(env.config).values)["meal_avoid"] == ["肉"]
    egg = ids_of(env, "番茄炒蛋")[0]
    out = revise(
        env,
        "番茄这道换一个",
        [proposal("named", remove=[(egg, "番茄这道", "replace")], add=[beans])],
        parse={"counts_stated_now": False},
    )
    assert out["current_plan"]["passed"] is True
    assert out["current_plan"]["meal_avoid"] == ["肉"]
    assert {r["diner_key"] for r in out["constraints"]["guest_rules"]} == {"meal"}
