"""The revise step of the planning graph (plan 3b-1 Task 4). Until routing sends revise turns
there (Task 5), a test enters it by writing the turn as route_intent's output with intent
"revise" and the follow-up's parse."""

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
from minemydinner.agent.request import PROMPT_VERSION, ParsedRequest, set_request_style
from minemydinner.models import AppPreference, PlanningRun
from minemydinner.planning.context import load_planning_context
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import L, make_recipe, rollback_session_factory, rule, self_diner
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
    removed = [{"id": i, "quote": q} for i, q in remove]
    args = {"target": target, "remove": removed, "add": list(add), "note": note, **fields}
    return tool_call("RevisionProposal", args)


def revise(env: Env, text: str, replies: list, parse: dict | None = None) -> dict:
    """A follow-up turn routed to the revise step."""
    env.model.replies = list(replies)
    env.graph.update_state(
        env.config,
        {
            "messages": [HumanMessage(text)],
            "intent": "revise",
            "model_calls": [],
            "request": ParsedRequest.model_validate(parse or {}).model_dump(),
        },
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
    assert [u["code"] for u in shown["unmet"]] == ["H4"]
    assert "荤菜" in shown["unmet"][0]["text"]
    assert "relax_split" in shown["next_actions"]
    assert out["messages"][-1].content.startswith("没能按这次的要求改好方案")
    record = last_record(env)
    assert (record.final_passed, record.retries) == (False, MAX_RETRIES)
    assert record.request["revision"] == {"from_version": 0}


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
