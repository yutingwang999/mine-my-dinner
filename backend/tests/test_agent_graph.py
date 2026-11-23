import json
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import delete, select

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.agent.graph import (
    MAX_RETRIES,
    CLOSEST_NOTE_EN,
    CLOSEST_NOTE_ZH,
    NO_PLAN_NOTE_BARE_EN,
    NO_PLAN_NOTE_BARE_ZH,
    NO_PLAN_NOTE_EN,
    OTHER_REPLY_EN,
    OTHER_REPLY_ZH,
    GraphDeps,
    EARLY_STOP_NOTE_EN,
    EARLY_STOP_NOTE_ZH,
    EMPTY_PLAN_NOTE_ZH,
    FALLBACK_NOTE_EN,
    WISH_UNMET_NOTE_ZH,
    build_graph,
)
from minemydinner.agent.actions import apply_action
from minemydinner.agent.card_text import next_actions, reason_text
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import (
    PROMPT_VERSION,
    ACTION_MESSAGES,
    REPLAN_MESSAGE,
    get_request_style,
    set_request_style,
)
from minemydinner.planning.feasibility import Shortfall
from minemydinner.models import AppPreference, LlmCall, PlanningRun
from minemydinner.agent.planner import MODEL_CALL_LIMIT, TOOLS_VERSION
from tests.agentfakes import (
    FakeEmbeddings,
    ScriptedChatModel,
    final_proposal,
    make_llm_client,
    tool_call,
)
from tests.planningfakes import (
    L,
    make_recipe,
    rollback_session_factory,
    rule,
    self_diner,
    stock,
)
from tests.recipefakes import seeded  # noqa: F401


def thread(name: str) -> dict:
    return {"configurable": {"thread_id": name}}


@pytest.fixture
def deps(session, fake_planning_llm) -> GraphDeps:
    factory = rollback_session_factory(session)
    return GraphDeps(factory, fake_planning_llm, PlanningRecorder(factory))


def test_graph_routes_other_intent_to_fixed_reply(deps, fake_planning_llm):
    graph = build_graph(deps, InMemorySaver())
    out = graph.invoke({"messages": [HumanMessage("hi")], "locale": "en"}, thread("t1"))
    assert out["intent"] == "other" and out["messages"][-1].content == OTHER_REPLY_EN
    task, messages = fake_planning_llm.calls[0]
    assert task == "planning"
    assert messages[0].type == "system" and messages[-1].content == "hi"


def test_other_reply_follows_locale_and_defaults_to_english(deps):
    graph = build_graph(deps, InMemorySaver())
    zh = graph.invoke({"messages": [HumanMessage("hi")], "locale": "zh"}, thread("t2"))
    other = graph.invoke({"messages": [HumanMessage("hi")], "locale": "fr"}, thread("t3"))
    assert zh["messages"][-1].content == OTHER_REPLY_ZH
    assert other["messages"][-1].content == OTHER_REPLY_EN


def test_conversation_resumes_from_postgres_checkpointer(test_database_url, deps):
    first_message = {"messages": [HumanMessage("hi")], "locale": "en"}
    with open_checkpointer(test_database_url) as saver:
        build_graph(deps, saver).invoke(first_message, thread("resume-test"))
    with open_checkpointer(test_database_url) as saver:
        try:
            state = build_graph(deps, saver).get_state(thread("resume-test"))
            assert len(state.values["messages"]) == 2
        finally:
            saver.delete_thread("resume-test")


# ---- the planning path ----


@dataclass
class GraphEnv:
    graph: Any
    model: ScriptedChatModel
    session: Any
    config: dict


@pytest.fixture
def graph_env(tmp_path, session, seeded):
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            # A follow-up on a plan is read, not routed: by default it asks for a new meal, as
            # the route's "plan" answer did before plan 3b-2.
            "FollowUpReading": [{"kind": "new_meal"}],
            "ParsedRequest": [{}],
        }
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, model)
    deps = GraphDeps(factory, client, PlanningRecorder(factory))
    return GraphEnv(build_graph(deps, InMemorySaver()), model, session, thread("plan-1"))


@pytest.fixture
def veg(session, seeded):
    stock(session, "小白菜", level="plenty")
    return make_recipe(session, "清炒白菜", "vegetable dish", [L("小白菜")], servings=2)


@pytest.fixture
def meat(session, seeded):
    stock(session, "土豆", count=3)
    stock(session, "猪肉", level="plenty")
    return make_recipe(session, "土豆炖肉", "meat dish", [L("猪肉"), L("土豆")])


def plan(env: GraphEnv, text: str, parse: dict | None = None, replies=None, locale="en") -> dict:
    env.model.structured["ParsedRequest"] = [parse or {}]
    if replies is not None:
        env.model.replies = list(replies)
    return env.graph.invoke({"messages": [HumanMessage(text)], "locale": locale}, env.config)


def resume(env: GraphEnv, value, parse: dict | None = None) -> dict:
    if parse is not None:
        env.model.structured["ParsedRequest"] = [parse]
    return env.graph.invoke(Command(resume=value), env.config)


def planning_events(record: PlanningRun) -> list[dict]:
    """The run's events after the route and parse calls recorded at its start."""
    return [e for e in record.events if e.get("step") not in ("route", "parse")]


def run_record(env: GraphEnv) -> PlanningRun:
    env.session.expire_all()
    return env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()


def planner_requests(env: GraphEnv) -> list[str]:
    """The last human message of each planner model call."""
    return [
        [m for m in sent if m.type == "human"][-1].content
        for sent in env.model.sent
        if sent and sent[0].type == "system" and "PlanProposal" in sent[0].content
    ]


def test_first_use_asks_request_style_once(graph_env, veg):
    set_request_style(graph_env.session, None)
    out = plan(graph_env, "dinner?", replies=[final_proposal([veg.id])])
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "request_style"}]
    out = resume(graph_env, "plan_then_adjust")
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert get_request_style(graph_env.session) == "plan_then_adjust"
    out = plan(graph_env, "and tomorrow?")
    assert "__interrupt__" not in out and len(out["plan_versions"]) == 2


def test_skipped_style_question_saves_plan_then_adjust(graph_env, veg):
    set_request_style(graph_env.session, None)
    out = plan(graph_env, "dinner?", replies=[final_proposal([veg.id])])
    (question,) = out["__interrupt__"]
    out = resume(graph_env, {question.id: None})
    assert out["current_plan"]["defaults_used"] == ["headcount", "dishes", "soups", "mode"]
    assert out["current_plan"]["passed"]
    assert get_request_style(graph_env.session) == "plan_then_adjust"
    # The question is asked once.
    out = plan(graph_env, "again?")
    assert "__interrupt__" not in out and len(out["plan_versions"]) == 2


def test_ask_first_interrupts_for_unstated_mode(graph_env, veg):
    set_request_style(graph_env.session, "ask_first")
    one_dish = {"dishes": 1, "soups": 0}
    out = plan(graph_env, "one dish", parse=one_dish, replies=[final_proposal([veg.id])])
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "question", "fields": ["mode"]}]
    out = resume(graph_env, "only what is in the fridge", parse={**one_dish, "mode": "fridge_only"})
    assert "__interrupt__" not in out
    assert out["messages"][-2].content == "only what is in the fridge"
    assert out["current_plan"]["defaults_used"] == ["headcount"]
    assert out["current_plan"]["spec"]["mode"] == "fridge_only"


def test_ask_first_does_not_ask_twice_for_one_request(graph_env, veg):
    set_request_style(graph_env.session, "ask_first")
    out = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    assert out["__interrupt__"][0].value["fields"] == ["mode", "composition"]
    out = resume(graph_env, "whatever")
    assert "__interrupt__" not in out and out["current_plan"]["passed"]


def test_parse_request_sends_questions(graph_env, veg):
    from minemydinner.agent.questions import question_text
    from minemydinner.agent.request import load_prompt

    set_request_style(graph_env.session, "ask_first")
    out = plan(graph_env, "今晚做饭", parse={"mode": "shopping"}, locale="zh")
    asked = out["__interrupt__"][0].value
    assert asked == {"kind": "question", "fields": ["composition"]}
    graph_env.model.replies = [final_proposal([veg.id])]
    resume(graph_env, "两个", parse={"mode": "shopping", "dishes": 2})
    sent = [s for s in graph_env.model.sent if s and s[0].type == "system"]
    parse_calls = [s for s in sent if s[0].content == load_prompt("parse")]
    route_calls = [s for s in sent if s[0].content == load_prompt("route")]
    assert len(parse_calls) == 2 and route_calls
    last = parse_calls[-1]
    assert [m.type for m in last] == ["system", "human", "ai", "human"]
    assert [m.content for m in last[1:]] == ["今晚做饭", question_text(asked, "zh"), "两个"]
    # The route input is unchanged: no question in it.
    assert all(m.type != "ai" for s in route_calls for m in s)


def test_contradiction_interrupts_with_its_problem(graph_env, veg):
    # The fixture's style is plan_then_adjust: contradictions are asked regardless of style.
    contradiction = {"dishes": 2, "meat_dishes": 3}
    out = plan(graph_env, "两个菜，三个荤菜", parse=contradiction)
    expected = {"kind": "question", "fields": ["composition"], "problem": "split_conflicts_total"}
    assert [i.value for i in out["__interrupt__"]] == [expected]
    # Answered with another contradiction: asked again, once for the new message.
    out = resume(graph_env, "三个荤菜", parse=contradiction)
    assert [i.value for i in out["__interrupt__"]] == [expected]
    fixed = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}
    graph_env.model.replies = [final_proposal([veg.id])]
    out = resume(graph_env, "一荤一素", parse=fixed)
    assert "__interrupt__" not in out
    assert out["current_plan"]["spec"]["meat_dishes"] == 1


def test_ask_first_still_asks_the_mode_after_a_contradiction_is_fixed(graph_env, veg):
    set_request_style(graph_env.session, "ask_first")
    out = plan(graph_env, "两个菜，三个荤菜", parse={"dishes": 2, "meat_dishes": 3})
    assert out["__interrupt__"][0].value["problem"] == "split_conflicts_total"
    fixed = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}
    out = resume(graph_env, "两个菜，一荤一素", parse=fixed)
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "question", "fields": ["mode"]}]


def test_contradiction_with_an_empty_answer_does_not_loop(graph_env):
    out = plan(graph_env, "thirteen dishes", parse={"dishes": 13})
    assert out["__interrupt__"][0].value["problem"] == "too_many"
    out = resume(graph_env, "")
    assert "__interrupt__" not in out
    assert out["messages"][-1].type == "ai"


def _spec(mode: str, **kwargs) -> dict:
    return {
        "mode": mode, "dishes": 0, "soups": 0, "staples": 0, "desserts": 0, "snacks": 0,
        "time_limit_min": None, "meat_dishes": None, "vegetable_dishes": None,
        "must_use": [], "all_use": [], **kwargs,
    }


FRIDGE_2 = _spec("fridge_only", dishes=2)
FRIDGE_1 = _spec("fridge_only", dishes=1)
SHOP_2 = _spec("shopping", dishes=2)
SPLIT_1_1 = _spec("shopping", dishes=2, meat_dishes=1, vegetable_dishes=1)
TIME_2 = _spec("shopping", dishes=2, time_limit_min=30)
FRIDGE_ALL_EGG_3 = _spec("fridge_only", dishes=3, all_use=["鸡蛋"])
SHOP_MUST_EGG_2 = _spec("shopping", dishes=2, must_use=["鸡蛋"])


@pytest.mark.parametrize("spec, reasons, unmet, expected", [
    (FRIDGE_2, ["empty_inventory"], [], ["switch_to_shopping", "fewer_dishes", "update_inventory"]),
    (SHOP_2, ["no_cookware"], [], ["fewer_dishes", "register_cookware"]),
    (FRIDGE_1, ["no_cookware"], [], ["register_cookware"]),
    (SPLIT_1_1, ["not_enough_in_library"], [], ["fewer_dishes", "relax_split", "import_recipes"]),
    (SHOP_2, [], ["H5"], []),
    (FRIDGE_2, [], ["H5"], []),
    (SHOP_2, [], ["H2"], ["fewer_dishes"]),
    (FRIDGE_2, [], ["H2"], ["switch_to_shopping", "fewer_dishes"]),
    (FRIDGE_1, [], ["H2"], ["switch_to_shopping"]),
    (SHOP_2, [], ["H2", "H5"], ["fewer_dishes"]),
    (TIME_2, ["time_limit"], [], ["fewer_dishes", "drop_time_limit"]),
    (FRIDGE_ALL_EGG_3, ["named_ingredient"], [], [
        "switch_to_shopping", "fewer_dishes", "relax_named", "update_inventory", "import_recipes",
    ]),
    (SHOP_MUST_EGG_2, [], ["H7"], ["fewer_dishes", "relax_named", "import_recipes"]),
    (SHOP_MUST_EGG_2, ["named_ingredient"], [], ["fewer_dishes", "relax_named", "import_recipes"]),
    (FRIDGE_2, [], ["H1"], ["switch_to_shopping", "fewer_dishes", "update_inventory"]),
    (SPLIT_1_1, [], ["H4"], ["fewer_dishes", "relax_split", "import_recipes"]),
    (SHOP_2, [], ["H3"], ["fewer_dishes", "register_cookware"]),
    (TIME_2, [], ["H6"], ["fewer_dishes", "drop_time_limit"]),
    (SHOP_2, [], ["H4"], ["fewer_dishes", "import_recipes"]),
    (FRIDGE_2, ["not_enough_dishes"], [], ["switch_to_shopping", "fewer_dishes", "update_inventory"]),
    (SPLIT_1_1, ["empty_inventory", "time_limit"], [], ["fewer_dishes", "drop_time_limit", "update_inventory"]),
])
def test_next_actions_by_reason(spec, reasons, unmet, expected):
    assert next_actions(spec, reasons, unmet) == expected


def test_relax_split_needs_a_short_meat_or_vegetable_category():
    reasons = ["not_enough_in_library"]
    assert "relax_split" not in next_actions(SPLIT_1_1, reasons, [], ["soups"])
    assert "relax_split" in next_actions(SPLIT_1_1, reasons, [], ["meat_dishes"])
    # No named split: nothing to relax.
    assert "relax_split" not in next_actions(SHOP_2, reasons, [], ["dishes"])


def test_fewer_dishes_counts_every_category():
    spec = _spec("shopping", staples=1, desserts=1)
    assert next_actions(spec, ["time_limit"], []) == ["fewer_dishes", "drop_time_limit"]
    assert next_actions({**spec, "desserts": 0}, ["no_cookware"], []) == ["register_cookware"]


def _early_stop_env(graph_env, session):
    # A recipe nobody can make from an empty fridge: the request stops before the planner.
    make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])


def test_infeasible_request_skips_the_planner(graph_env, session):
    _early_stop_env(graph_env, session)
    out = plan(graph_env, "dinner")
    current = out["current_plan"]
    assert planner_requests(graph_env) == []
    assert current["recipes"] == [] and current["passed"] is False
    assert [r["reason"] for r in current["reasons"]] == ["empty_inventory"]
    assert current["next_actions"] == ["switch_to_shopping", "update_inventory"]
    assert out["messages"][-1].content == EARLY_STOP_NOTE_EN


def test_feasibility_event_is_recorded_and_run_finished(graph_env, session):
    _early_stop_env(graph_env, session)
    out = plan(graph_env, "dinner")
    record = run_record(graph_env)
    assert [e["kind"] for e in planning_events(record)] == ["feasibility"]
    assert planning_events(record)[0]["shortfalls"] == [
        {"reason": "empty_inventory", "category": None, "needed": None, "available": None,
         "ingredient": None, "minimum": False, "absent_from_stock": False}
    ]
    assert record.final_passed is False and record.first_passed is False and record.retries == 0
    assert record.final_plan == out["current_plan"]


def test_reason_text_states_needed_and_available_in_locale(graph_env, session):
    stock(session, "鸡蛋", count=3)
    make_recipe(session, "炒青菜", "vegetable dish", [L("鸡蛋")])
    zh = plan(graph_env, "晚饭", parse={"dishes": 1, "meat_dishes": 1}, locale="zh")
    en = plan(
        graph_env, "dinner", parse={"dishes": 1, "meat_dishes": 1}, locale="en",
    )
    (zh_reason,) = zh["current_plan"]["reasons"]
    (en_reason,) = en["current_plan"]["reasons"]
    assert (zh_reason["reason"], zh_reason["category"], zh_reason["needed"]) == (
        "not_enough_in_library", "meat_dishes", 1,
    )
    assert zh_reason["available"] == 0
    assert en_reason["text"] == "Your library has 0 meat dishes you can eat; this meal needs 1 meat dish"
    assert zh_reason["text"] == "你的菜谱库里你能吃的只有 0 道荤菜，这餐需要 1 道荤菜"
    assert zh["messages"][-1].content == EARLY_STOP_NOTE_ZH
    assert "relax_split" in en["current_plan"]["next_actions"]


def test_reason_text_for_named_ingredient_and_plain_reasons():
    def shortfall(reason, category=None, needed=None, available=None, ingredient=None):
        return {"reason": reason, "category": category, "needed": needed,
                "available": available, "ingredient": ingredient}

    assert reason_text(shortfall("named_ingredient", ingredient="鸡蛋"), "shopping", "en") == "No dish in your library uses 鸡蛋"
    assert reason_text(
        shortfall("named_ingredient", "vegetable_dishes", 3, 1, "鸡蛋"), "fridge_only", "en"
    ) == "Only 1 vegetable dish with 鸡蛋 can be made; this meal needs 3 vegetable dishes"
    assert reason_text(
        shortfall("not_enough_dishes", "dishes", 2, 1), "fridge_only", "en"
    ) == "By the current fridge you can make 1 dish; this meal needs 2 dishes"
    for reason in ("empty_inventory", "no_cookware", "time_limit"):
        for locale in ("en", "zh"):
            assert reason_text(shortfall(reason), "fridge_only", locale)


def test_reason_text_says_at_least_for_minimums():
    short = {"reason": "not_enough_in_library", "category": "meat_dishes", "needed": 2,
             "available": 1, "ingredient": None, "minimum": True}
    assert reason_text(short, "shopping", "en") == (
        "Your library has 1 meat dish you can eat; this meal needs at least 2 meat dishes"
    )
    assert reason_text(short, "shopping", "zh") == (
        "你的菜谱库里你能吃的只有 1 道荤菜，这餐需要 至少 2 道荤菜"
    )
    fridge = {**short, "reason": "not_enough_dishes"}
    assert "at least 2 meat dishes" in reason_text(fridge, "fridge_only", "en")
    exact = {**short, "minimum": False}
    assert "at least" not in reason_text(exact, "shopping", "en")


def test_named_ingredient_text_tells_absent_from_stocked_but_unmakeable():
    base = {"reason": "named_ingredient", "category": None, "needed": None,
            "available": None, "ingredient": "鸡蛋"}
    absent = {**base, "absent_from_stock": True}
    assert reason_text(absent, "fridge_only", "en") == "No 鸡蛋 in the fridge"
    assert reason_text(absent, "fridge_only", "zh") == "冰箱里没有鸡蛋"
    stocked = {**base, "absent_from_stock": False}
    assert reason_text(stocked, "fridge_only", "en") == (
        "No dish that can be made from the fridge uses 鸡蛋"
    )
    assert reason_text(stocked, "fridge_only", "zh") == "用现有的食材做不出用到鸡蛋的菜"
    assert reason_text(absent, "shopping", "en") == "No dish in your library uses 鸡蛋"


def test_named_ingredient_name_goes_through_prepare_text_in_the_event(
    tmp_path, session, seeded, veg
):
    set_request_style(session, "plan_then_adjust")
    model = ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            "ParsedRequest": [{"dishes": 1, "must_use": ["小李酱"]}],
        },
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(
        tmp_path, factory, model,
        pre_hooks=[lambda t: t.replace("小李", "Diner 2")],
        post_hooks=[lambda t: t.replace("Diner 2", "小李")],
    )
    graph = build_graph(GraphDeps(factory, client, PlanningRecorder(factory)), InMemorySaver())
    out = graph.invoke({"messages": [HumanMessage("dinner")], "locale": "en"}, thread("hook-f"))
    session.expire_all()
    record = session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    (event,) = [e for e in record.events if e["kind"] == "feasibility"]
    assert [s["ingredient"] for s in event["shortfalls"]] == ["Diner 2酱"]
    assert "小李" not in json.dumps(record.events, ensure_ascii=False)
    # The state keeps the restored name.
    assert out["current_plan"]["reasons"][0]["text"].count("小李酱") == 1


def test_feasibility_is_rechecked_before_a_retry(graph_env, veg, monkeypatch):
    calls = []

    def fake_check(session, ctx, spec):
        calls.append(1)
        return [] if len(calls) == 1 else [Shortfall("time_limit", None, None, None)]

    monkeypatch.setattr("minemydinner.agent.graph.check_feasibility", fake_check)
    out = plan(graph_env, "two dishes", parse={"dishes": 2}, replies=[final_proposal([veg.id])])
    assert len(calls) == 2 and len(planner_requests(graph_env)) == 1
    current = out["current_plan"]
    assert current["recipes"] == [] and current["passed"] is False
    assert [r["reason"] for r in current["reasons"]] == ["time_limit"]
    assert out["retries"] == 1
    record = run_record(graph_env)
    assert [e["kind"] for e in planning_events(record)] == [
        "model_call", "validation", "feasibility",
    ]
    assert record.final_passed is False and record.first_passed is False


def test_feasible_request_reaches_the_planner(graph_env, veg):
    out = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    assert len(planner_requests(graph_env)) == 1
    assert out["current_plan"]["passed"] is True and out["current_plan"]["reasons"] == []
    assert [e["kind"] for e in planning_events(run_record(graph_env))] == ["model_call", "validation"]


def test_failed_validation_retries_with_errors(graph_env, veg, meat):
    replies = [final_proposal([veg.id]), final_proposal([veg.id, meat.id])]
    out = plan(graph_env, "two dishes please", parse={"dishes": 2}, replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    record = run_record(graph_env)
    assert [e["kind"] for e in planning_events(record)] == [
        "model_call",
        "validation",
        "model_call",
        "validation",
    ]
    assert "H4" in planning_events(record)[1]["result"]["violations"][0]["code"]
    assert record.retries == 1 and record.first_passed is False and record.final_passed is True
    retry_request = planner_requests(graph_env)[1]
    assert "the user wants 2 dishes" in retry_request and str(veg.id) in retry_request
    # The category hint is the revise step's; a new plan's retry is unchanged.
    assert "same dish type" not in retry_request


def test_h2_failure_after_retries_is_never_shown(graph_env, session, veg):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    stock(session, "鸡蛋", count=6)
    egg = make_recipe(session, "番茄炒蛋", "meat dish", [L("鸡蛋")])
    out = plan(graph_env, "dinner", replies=[final_proposal([egg.id])])
    current = out["current_plan"]
    assert current["recipes"] == [] and current["passed"] is False
    assert out["retries"] == MAX_RETRIES
    assert [u["code"] for u in current["unmet"]] == ["H2"]
    # Controller ruling: H2 offers switch_to_shopping in fridge-only mode (one dish: no fewer).
    assert current["next_actions"] == ["switch_to_shopping"]
    assert out["messages"][-1].content == NO_PLAN_NOTE_EN
    assert "Diner 1: allergy egg" in planner_requests(graph_env)[0]
    assert run_record(graph_env).final_passed is False


def test_no_plan_note_follows_locale(graph_env, veg):
    out = plan(graph_env, "晚饭", replies=[final_proposal([999999])], locale="zh")
    assert out["messages"][-1].content == NO_PLAN_NOTE_BARE_ZH
    assert out["current_plan"]["unmet"][0]["text"] == "提议的菜并不都来自你的菜谱库"


def test_unknown_recipe_ids_are_never_shown(graph_env, veg):
    out = plan(graph_env, "dinner", replies=[final_proposal([999999])])
    assert out["current_plan"]["recipes"] == []
    assert [u["code"] for u in out["current_plan"]["unmet"]] == ["H5"]
    # H5 alone offers no action, so the note must not promise things to change.
    assert out["current_plan"]["next_actions"] == []
    assert out["messages"][-1].content == NO_PLAN_NOTE_BARE_EN


def test_no_action_note_follows_locale(graph_env, veg):
    out = plan(graph_env, "晚饭", replies=[final_proposal([999999])], locale="zh")
    assert out["messages"][-1].content == NO_PLAN_NOTE_BARE_ZH
    assert "调整" not in NO_PLAN_NOTE_BARE_ZH and "change" not in NO_PLAN_NOTE_BARE_EN


def test_fridge_only_shortfall_shows_closest_with_unmet(graph_env, session, veg):
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    out = plan(graph_env, "dinner", replies=[final_proposal([pork.id])])
    current = out["current_plan"]
    assert current["passed"] is False and [r["id"] for r in current["recipes"]] == [pork.id]
    assert current["missing"] == {str(pork.id): ["五花肉"]}
    (unmet,) = current["unmet"]
    assert unmet["code"] == "H1" and unmet["recipe_id"] == pork.id and "五花肉" in unmet["text"]
    assert current["next_actions"] == ["switch_to_shopping", "update_inventory"]
    # The fixed closest note leads; the model's note follows and is never shown alone.
    assert out["messages"][-1].content == CLOSEST_NOTE_EN + "\n\n" + "Here is your dinner."


def test_closest_note_is_fixed_text_in_the_locale_even_without_a_model_note(
    graph_env, session, veg
):
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    out = plan(graph_env, "晚饭", replies=[final_proposal([pork.id], note="")], locale="zh")
    assert out["current_plan"]["passed"] is False
    assert out["messages"][-1].content == CLOSEST_NOTE_ZH


def test_servings_note_when_headcount_differs(graph_env, veg):
    parse = {"others_mentioned": True, "headcount": 3, "dishes": 1, "soups": 0, "mode": "shopping"}
    out = plan(graph_env, "dinner for three", parse=parse, replies=[final_proposal([veg.id])])
    # The diner card is skipped: the user plans alone and the rest stay unconfirmed.
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    out = resume(graph_env, {card.id: None})
    current = out["current_plan"]
    assert current["servings_notes"] == [{"recipe_id": veg.id, "servings": 2, "headcount": 3}]
    assert current["headcount"] == 3 and current["passed"]
    assert {"code": "S8", "recipe_id": None, "detail": {"unconfirmed_diners": 2}} in current[
        "warnings"
    ]
    assert current["recipes"][0]["servings"] == 2


def test_record_marks_defaults_and_pass_flags(graph_env, veg):
    out = plan(graph_env, "dinner?", replies=[final_proposal([veg.id], note="Enjoy.")])
    record = run_record(graph_env)
    assert record.defaults_used == ["headcount", "dishes", "soups", "mode"]
    assert (record.retries, record.first_passed, record.final_passed) == (0, True, True)
    assert record.prompt_version == PROMPT_VERSION and record.tools_version == TOOLS_VERSION
    assert record.thread_id == "plan-1" and record.has_temporary_guests is False
    assert record.final_plan == out["current_plan"]
    assert out["current_plan"]["recipes"] == [
        {
            "id": veg.id,
            "title": "清炒白菜",
            "dish_type": "vegetable dish",
            "time_min": None,
            "time_source": None,
            "servings": 2,
        }
    ]
    assert out["plan_versions"] == [out["current_plan"]]
    assert isinstance(out["messages"][-1], AIMessage) and out["messages"][-1].content == "Enjoy."


def test_task_name_is_configurable(tmp_path, session, seeded, veg):
    set_request_style(session, "plan_then_adjust")
    session.execute(delete(LlmCall))  # rolled back; hides rows kept in the test database
    model = ScriptedChatModel(
        replies=[final_proposal([veg.id])],
        structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]},
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, model)
    client.config.tasks["planning_eval"] = client.config.tasks["planning"]
    deps = GraphDeps(factory, client, PlanningRecorder(factory), task="planning_eval")
    build_graph(deps, InMemorySaver()).invoke(
        {"messages": [HumanMessage("dinner?")], "locale": "en"}, thread("t4")
    )
    tasks = {row.task for row in session.scalars(select(LlmCall))}
    assert tasks == {"planning_eval"}


def test_closest_attempt_without_h2_is_shown_when_the_last_breaks_h2(graph_env, session, veg):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    stock(session, "鸡蛋", count=6)
    egg = make_recipe(session, "番茄炒蛋", "meat dish", [L("鸡蛋")])
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    out = plan(graph_env, "dinner", replies=[final_proposal([pork.id]), final_proposal([egg.id])])
    current = out["current_plan"]
    assert out["retries"] == MAX_RETRIES and out["validation"]["violations"][0]["code"] == "H2"
    assert [r["id"] for r in current["recipes"]] == [pork.id] and current["passed"] is False
    assert [u["code"] for u in current["unmet"]] == ["H1"]
    assert current["next_actions"] == ["switch_to_shopping", "update_inventory"]


def test_unknown_allergen_retry_is_worded_for_the_model(graph_env, session, veg):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    mystery = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    replies = [final_proposal([mystery.id]), final_proposal([veg.id])]
    out = plan(graph_env, "dinner", replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    retry = planner_requests(graph_env)[1]
    assert f"Recipe {mystery.id}: ingredients with unknown allergens (某种酱)" in retry


def test_dietary_violation_without_a_line_never_says_none():
    from minemydinner.agent.graph import _violation_for_model

    finding = {"code": "H2", "recipe_id": 5, "detail": {"diner_key": "diner:3", "line": None}}
    text = _violation_for_model(finding, {"diner:3": "Diner 1"})
    assert text == "Recipe 5 breaks a dietary limit of Diner 1." and "None" not in text


def test_record_keeps_replaced_names_and_state_keeps_restored(tmp_path, session, seeded):
    set_request_style(session, "plan_then_adjust")
    stock(session, "小白菜", level="plenty")
    dish = make_recipe(session, "小李的白菜", "vegetable dish", [L("小白菜")])
    model = ScriptedChatModel(
        replies=[final_proposal([dish.id], note="Diner 2 will like it")],
        structured={
            "Intent": [{"intent": "plan"}],
            "ParsedRequest": [{"query": "something Diner 2 likes", "prefer_use": ["Diner 2"]}],
        },
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(
        tmp_path,
        factory,
        model,
        pre_hooks=[lambda t: t.replace("小李", "Diner 2")],
        post_hooks=[lambda t: t.replace("Diner 2", "小李")],
    )
    graph = build_graph(GraphDeps(factory, client, PlanningRecorder(factory)), InMemorySaver())
    out = graph.invoke(
        {"messages": [HumanMessage("dinner 小李 likes")], "locale": "en"}, thread("hooks")
    )
    assert out["request"]["query"] == "something 小李 likes"
    assert out["current_plan"]["recipes"][0]["title"] == "小李的白菜"
    assert out["messages"][-1].content == "小李 will like it"
    session.expire_all()
    record = session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    assert record.request["parsed"]["query"] == "something Diner 2 likes"
    assert record.request["parsed"]["prefer_use"] == ["Diner 2"]
    assert record.request["messages"] == ["dinner Diner 2 likes"]
    assert record.final_plan["recipes"][0]["title"] == "Diner 2的白菜"
    assert "小李" not in json.dumps(record.request, ensure_ascii=False)
    assert "小李" not in json.dumps(record.final_plan, ensure_ascii=False)
    assert "小李" not in json.dumps(record.events, ensure_ascii=False)


def test_closest_attempt_breaks_ties_by_composition_gap(graph_env, session):
    meats = [make_recipe(session, f"肉{i}", "meat dish") for i in range(3)]
    vegs = [make_recipe(session, f"菜{i}", "vegetable dish") for i in range(3)]
    one_meat = [meats[0].id] + [v.id for v in vegs]
    two_meat = [m.id for m in meats[:2]] + [v.id for v in vegs]
    parse = {"mode": "shopping", "dishes": 6, "meat_dishes": 3, "vegetable_dishes": 3}
    replies = [final_proposal(one_meat), final_proposal(two_meat), final_proposal(one_meat)]
    out = plan(graph_env, "three meat and three vegetable dishes", parse=parse, replies=replies)
    assert out["retries"] == MAX_RETRIES
    current = out["current_plan"]
    assert [r["id"] for r in current["recipes"]] == two_meat
    assert [u["code"] for u in current["unmet"]] == ["H4"]


def test_h4_and_h7_wording_for_the_model_and_the_user():
    from minemydinner.agent.card_text import unmet_for_user
    from minemydinner.agent.graph import _violation_for_model

    # 3 dishes, at least 2 meat dishes (meat [2, 3], vegetable [0, 1]).
    h4 = {
        "code": "H4",
        "recipe_id": None,
        "detail": {
            "required": {"dishes": 3, "soups": 0},
            "present": {"dishes": 2, "soups": 1, "meat_dishes": 0, "vegetable_dishes": 2},
            "ranges": {"meat_dishes": [2, 3], "vegetable_dishes": [0, 1]},
        },
    }
    assert _violation_for_model(h4, {}) == (
        "The plan has 2 dishes, 1 soup, 0 meat dishes, 2 vegetable dishes; the user wants "
        "3 dishes, 0 soups, at least 2 meat dishes, at most 1 vegetable dish."
    )
    assert unmet_for_user(h4, {}, "zh") == (
        "方案有 2 道菜，1 道汤，0 道荤菜，2 道素菜，要求是 3 道菜，0 道汤，至少 2 道荤菜，最多 1 道素菜"
    )
    h7 = {
        "code": "H7",
        "recipe_id": None,
        "detail": {"name": "鸡蛋", "rule": "all", "recipes_without": [7]},
    }
    assert unmet_for_user(h7, {7: "白菜"}, "en") == 'These dishes do not use 鸡蛋: "白菜"'
    assert unmet_for_user(h7, {7: "白菜"}, "zh") == "这些菜没有用到鸡蛋：“白菜”"
    must = {**h7, "detail": {**h7["detail"], "rule": "must"}}
    assert unmet_for_user(must, {}, "en") == "No dish in the plan uses 鸡蛋"
    assert "7" in _violation_for_model(h7, {}) and "鸡蛋" in _violation_for_model(must, {})


def test_counts_text_uses_singular_and_omits_unasked_zeros():
    from minemydinner.agent.card_text import unmet_for_user

    h4 = {
        "code": "H4",
        "recipe_id": None,
        "detail": {
            "required": {"dishes": 1, "soups": 1, "staples": 0, "desserts": 0, "snacks": 0},
            "present": {"dishes": 2, "soups": 0, "staples": 0, "desserts": 0, "snacks": 0},
            "minimum": False,
        },
    }
    assert unmet_for_user(h4, {}, "en") == "The plan has 2 dishes, 0 soups; 1 dish, 1 soup were asked for"
    assert unmet_for_user(h4, {}, "zh") == "方案有 2 道菜，0 道汤，要求是 1 道菜，1 道汤"


# ---- the planner's opening, retries, and the shortlist (Task 7) ----


def make_graph_env(tmp_path, session, model=None, embeddings=None, **deps_kwargs):
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = model or ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            "FollowUpReading": [{"kind": "new_meal"}],
            "ParsedRequest": [{}],
        }
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, model, embeddings=embeddings)
    deps = GraphDeps(factory, client, PlanningRecorder(factory), **deps_kwargs)
    env = GraphEnv(build_graph(deps, InMemorySaver()), model, session, thread("plan-7"))
    env.deps = deps
    return env


def planner_calls(env: GraphEnv) -> list[list]:
    """The messages of each planner model call, as the model received them."""
    return [
        sent
        for sent in env.model.sent
        if sent and sent[0].type == "system" and "PlanProposal" in sent[0].content
    ]


def recorded_model_calls(env: GraphEnv) -> list[dict]:
    return [e for e in run_record(env).events if e.get("step") == "planner"]


def shortlist_line(opening: str) -> str:
    (line,) = [line for line in opening.splitlines() if line.startswith("{")]
    return line


def shortlist_of(opening: str) -> dict:
    return json.loads(shortlist_line(opening))


ENTRY_KEYS = [
    "id", "title", "time_min", "time_source", "servings",
    "missing", "missing_cookware", "uses_expiring", "dislikes",
]


def test_first_message_is_shortlist_then_summary(graph_env, veg, meat):
    replies = [final_proposal([veg.id, meat.id])]
    out = plan(graph_env, "two dishes", parse={"dishes": 2}, replies=replies)
    (sent,) = planner_calls(graph_env)
    # The system prompt, then one human message: the opening.
    assert [m.type for m in sent] == ["system", "human"]
    opening = sent[1].content
    assert opening == out["planner_opening"]
    shortlist = shortlist_of(opening)
    assert list(shortlist) == ["meat dish", "vegetable dish"]
    assert all(list(group) == ["complete", "candidates"] for group in shortlist.values())
    assert [e["id"] for e in shortlist["meat dish"]["candidates"]] == [meat.id]
    # Each type holds every fitting recipe, so both are marked complete.
    assert [group["complete"] for group in shortlist.values()] == [True, True]
    (entry,) = shortlist["vegetable dish"]["candidates"]
    assert list(entry) == ENTRY_KEYS
    assert entry == {
        "id": veg.id, "title": "清炒白菜", "time_min": None, "time_source": None, "servings": 2,
        "missing": [], "missing_cookware": [], "uses_expiring": [], "dislikes": [],
    }
    # Compact JSON, before the request summary.
    line = shortlist_line(opening)
    assert ", " not in line and '": ' not in line
    assert opening.index(line) < opening.index("Plan this meal.")
    assert "Reply locale: en" in opening and "Dishes (meat plus vegetable): 2" in opening
    assert out["current_plan"]["passed"]


def test_opening_marks_a_cut_type_incomplete(
    tmp_path, session, seeded, veg
):
    for i in range(7):
        make_recipe(session, f"蛋{i}", "vegetable dish", [L("鸡蛋", role="main")])
    stock(session, "鸡蛋", count=6)
    env = make_graph_env(tmp_path, session)
    parse = {"dishes": 1, "vegetable_dishes": 1, "meat_dishes": 0}
    out = plan(env, "dinner", parse=parse, replies=[final_proposal([veg.id])])
    shortlist = shortlist_of(out["planner_opening"])
    assert list(shortlist) == ["vegetable dish"]
    assert shortlist["vegetable dish"]["complete"] is False
    assert len(shortlist["vegetable dish"]["candidates"]) == 5
    assert '"complete":false' in out["planner_opening"]


def test_summary_states_counts_split_minimum_and_other_categories(
    tmp_path, session, seeded, veg, meat
):
    make_recipe(session, "蛋花汤", "soup", [L("鸡蛋", role="main")])
    make_recipe(session, "米饭", "staple food", [L("大米", linked=False)])
    make_recipe(session, "凉拌黄瓜", "vegetable dish", [L("黄瓜")])
    env = make_graph_env(tmp_path, session)
    parse = {
        "mode": "shopping", "dishes": 3, "meat_dishes": 1, "vegetable_dishes": 1,
        "soups": 1, "staples": 1, "must_use": ["猪肉"], "prefer_use": ["土豆"],
    }
    out = plan(env, "dinner", parse=parse, replies=[final_proposal([veg.id])])
    opening = out["planner_opening"]
    assert "Dishes (meat plus vegetable): 3" in opening
    assert "Split: 1 to 2 meat dishes, 1 to 2 vegetable dishes" in opening
    assert "Every count is exact unless it says at least, at most, or a range." in opening
    assert "Soups: 1" in opening
    assert "Staple foods: 1, desserts: 0, snacks: 0" in opening
    assert "Must use in at least one dish: 猪肉" in opening
    assert "Prefer using: 土豆" in opening


def test_retry_reuses_the_identical_opening(graph_env, veg, meat):
    replies = [final_proposal([veg.id]), final_proposal([veg.id, meat.id])]
    out = plan(graph_env, "two dishes please", parse={"dishes": 2}, replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    sent = planner_calls(graph_env)
    assert len(sent) == 2
    # The system prompt and the opening are byte-identical; one retry message is appended.
    assert [m.content for m in sent[1][:2]] == [m.content for m in sent[0][:2]]
    assert len(sent[1]) == 3 and sent[1][2].type == "human"
    # The complete markers are part of that opening, kept as built the first time.
    assert sent[1][1].content == out["planner_opening"]
    assert shortlist_of(sent[1][1].content)["vegetable dish"]["complete"] is True
    calls = recorded_model_calls(graph_env)
    opening = calls[0]["messages"][:2]
    retry_index = 1
    assert calls[retry_index]["messages"][: len(opening)] == calls[0]["messages"][: len(opening)]


def test_retry_message_summarizes_the_previous_attempt(graph_env, session, veg):
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    replies = [final_proposal([pork.id]), final_proposal([veg.id])]
    out = plan(graph_env, "dinner", parse={"dishes": 1}, replies=replies)
    assert out["retries"] == 1
    retry = planner_calls(graph_env)[1][-1].content
    shortlist = shortlist_of(out["planner_opening"])
    offered = [e["id"] for group in shortlist.values() for e in group["candidates"]]
    # Fridge-only mode shortlists makeable recipes only, so the pork dish (missing 五花肉)
    # is not offered; the scripted model proposes it anyway and fails H1.
    assert offered == [veg.id]
    assert f"Your proposal [{pork.id}] failed these checks." in retry
    assert "not in stock: 五花肉" in retry
    assert "Shortlist ids offered: " + ", ".join(str(i) for i in offered) in retry
    assert f"Ids proposed: {pork.id}" in retry
    assert f"Missing items per proposed recipe: {pork.id}: 五花肉" in retry


def test_no_shortlist_switch_sends_summary_only(tmp_path, session, seeded, veg):
    env = make_graph_env(tmp_path, session, use_shortlist=False)
    replies = [final_proposal([999999]), final_proposal([veg.id])]
    out = plan(env, "dinner", parse={"dishes": 1}, replies=replies)
    sent = planner_calls(env)
    opening = sent[0][1].content
    assert opening.startswith("Plan this meal.") and "{" not in opening
    assert opening == out["planner_opening"]
    assert sent[1][1].content == opening
    assert "Shortlist ids offered: none" in sent[1][2].content


class ShortlistPicker(ScriptedChatModel):
    """Answers at once from the shortlist in the opening: the top entry of each needed type."""

    def invoke(self, messages, config=None, **kwargs):
        first = messages[0] if messages else None
        if not (first is not None and first.type == "system" and "PlanProposal" in first.content):
            return super().invoke(messages, config, **kwargs)
        self.sent.append(list(messages))
        shortlist = shortlist_of(messages[1].content)
        ids = [group["candidates"][0]["id"] for group in shortlist.values() if group["candidates"]]
        call = {**final_proposal(ids).tool_calls[0], "id": "pick"}
        return AIMessage(content="", tool_calls=[call], usage_metadata=self.usage)


@pytest.fixture
def graph_env_shortlist_fits(tmp_path, session, seeded, veg, meat):
    stock(session, "鸡蛋", count=6)
    make_recipe(session, "蛋花汤", "soup", [L("鸡蛋", role="main")])
    model = ShortlistPicker(structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]})
    return make_graph_env(tmp_path, session, model=model)


def test_typical_request_needs_one_planner_call(graph_env_shortlist_fits):
    env = graph_env_shortlist_fits
    parse = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1, "soups": 1}
    out = plan(env, "一荤一素一汤", parse=parse)
    assert out["current_plan"]["passed"] and out["retries"] == 0
    assert len(planner_calls(env)) == 1
    assert [e["kind"] for e in planning_events(run_record(env))] == ["model_call", "validation"]


def test_one_query_vector_cache_per_run(tmp_path, session, seeded, veg):
    embeddings = FakeEmbeddings()
    env = make_graph_env(tmp_path, session, embeddings=embeddings)
    replies = [
        tool_call("search_recipes", {"query": "清淡"}),
        final_proposal([999999]),
        tool_call("search_recipes", {"query": "清淡"}),
        final_proposal([veg.id]),
    ]
    out = plan(env, "清淡的", parse={"dishes": 1, "query": "清淡"}, replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    # The shortlist and both attempts' searches share one embedding of the query.
    assert embeddings.embedded == ["清淡"]
    # The run's cache is dropped once the run ends.
    assert env.deps.run_query_vectors == {}


def test_shortlist_search_notes_reach_the_plan_card(tmp_path, session, seeded, veg):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    env = make_graph_env(tmp_path, session, embeddings=FakeEmbeddings(error=RuntimeError("down")))
    parse = {"dishes": 1, "query": "清淡"}
    out = plan(env, "清淡的", parse=parse, replies=[final_proposal([veg.id])])
    current = out["current_plan"]
    # The planner never searched; the notes come from the shortlist.
    assert current["semantic_paused"] is True
    assert current["left_out_unknown_allergens"] == ["神秘菜"]


def test_planner_search_respects_the_request_filters(tmp_path, session, seeded):
    quick = make_recipe(session, "快手菜", lines=[L("小白菜")], time_min=15, time_source="stated")
    slow = make_recipe(session, "慢炖菜", lines=[L("小白菜")], time_min=90, time_source="stated")
    embeddings = FakeEmbeddings()
    env = make_graph_env(tmp_path, session, embeddings=embeddings)
    parse = {"mode": "shopping", "dishes": 1, "time_limit_min": 30, "query": "清淡"}
    replies = [tool_call("search_recipes", {"max_time": 120}), final_proposal([quick.id])]
    out = plan(env, "半小时内，清淡的", parse=parse, replies=replies)
    assert out["current_plan"]["passed"]
    (search,) = [e for e in run_record(env).events if e["kind"] == "tool_call"]
    hit_ids = [hit["id"] for hit in search["result"]["hits"]]
    assert quick.id in hit_ids and slow.id not in hit_ids
    # The request's query ranked the search too, reusing the shortlist's embedding.
    assert embeddings.embedded == ["清淡"]


class FailingPlanner(ScriptedChatModel):
    """Answers the router and the parser, then fails every planner call."""

    def invoke(self, messages, config=None, **kwargs):
        if messages and messages[0].type == "system" and "PlanProposal" in messages[0].content:
            raise RuntimeError("provider down")
        return super().invoke(messages, config, **kwargs)


def test_query_vectors_are_dropped_when_the_planner_fails(tmp_path, session, seeded, veg):
    model = FailingPlanner(structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]})
    env = make_graph_env(tmp_path, session, model=model)
    with pytest.raises(Exception):
        plan(env, "清淡的", parse={"dishes": 1, "query": "清淡"})
    assert env.deps.run_query_vectors == {}


def test_search_tool_filters_and_ranks_with_the_named_ingredients(tmp_path, session, seeded):
    egg = make_recipe(session, "番茄炒蛋", lines=[L("番茄", role="main"), L("鸡蛋", role="main")])
    make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])
    env = make_graph_env(tmp_path, session)
    parse = {"mode": "shopping", "dishes": 1, "all_use": ["鸡蛋"]}
    replies = [tool_call("search_recipes", {}), final_proposal([egg.id])]
    out = plan(env, "全做鸡蛋", parse=parse, replies=replies)
    assert out["current_plan"]["passed"]
    (search,) = [e for e in run_record(env).events if e["kind"] == "tool_call"]
    assert [hit["id"] for hit in search["result"]["hits"]] == [egg.id]


# ---- split counts wording and recorded route/parse calls (Task 10 fix 3) ----


def _h4(required: dict, present: dict, minimum: bool = False) -> dict:
    """An H4 finding as saved before ranges existed: named sides in `required`."""
    return {
        "code": "H4",
        "recipe_id": None,
        "detail": {"required": required, "present": present, "minimum": minimum},
    }


def _h4_ranges(dishes: int, present: dict, meat: list[int], vegetable: list[int]) -> dict:
    return {
        "code": "H4",
        "recipe_id": None,
        "detail": {
            "required": {"dishes": dishes, "soups": 0, "staples": 0, "desserts": 0, "snacks": 0},
            "present": present,
            "ranges": {"meat_dishes": meat, "vegetable_dishes": vegetable},
        },
    }


def test_exact_one_sided_split_states_the_implied_side():
    from minemydinner.agent.card_text import unmet_for_user
    from minemydinner.agent.graph import _violation_for_model

    h4 = _h4(
        {"dishes": 8, "soups": 0, "meat_dishes": 2},
        {"dishes": 8, "soups": 0, "meat_dishes": 3, "vegetable_dishes": 5},
    )
    text = _violation_for_model(h4, {})
    assert text == (
        "The plan has 8 dishes, 3 meat dishes, 5 vegetable dishes; "
        "the user wants 8 dishes, 2 meat dishes, 6 vegetable dishes."
    )
    assert "0 vegetable dishes" not in text
    assert "6 道素菜" in unmet_for_user(h4, {}, "zh")
    assert "要求是 8 道菜，2 道荤菜，6 道素菜" in unmet_for_user(h4, {}, "zh")
    assert "8 dishes, 2 meat dishes, 6 vegetable dishes were asked for" in unmet_for_user(
        h4, {}, "en"
    )


def test_exact_vegetable_only_split_implies_the_meat_side():
    from minemydinner.agent.graph import _violation_for_model

    h4 = _h4({"dishes": 5, "vegetable_dishes": 2}, {"dishes": 5, "meat_dishes": 5})
    assert "the user wants 5 dishes, 3 meat dishes, 2 vegetable dishes." in _violation_for_model(
        h4, {}
    )


def test_old_minimum_finding_states_the_tightened_ranges():
    from minemydinner.agent.card_text import unmet_for_user
    from minemydinner.agent.graph import _violation_for_model

    h4 = _h4(
        {"dishes": 3, "meat_dishes": 2},
        {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 2},
        minimum=True,
    )
    text = _violation_for_model(h4, {})
    assert text.endswith(
        "the user wants 3 dishes, at least 2 meat dishes, at most 1 vegetable dish."
    )
    zh = unmet_for_user(h4, {}, "zh")
    assert zh.endswith("要求是 3 道菜，至少 2 道荤菜，最多 1 道素菜")


def test_an_unconstrained_side_is_never_printed():
    from minemydinner.agent.card_text import unmet_for_user
    from minemydinner.agent.graph import _violation_for_model

    # 8 dishes, at least 2 meat: the vegetable side is [0, 6], stated as at most 6.
    h4 = _h4_ranges(8, {"dishes": 7, "meat_dishes": 1, "vegetable_dishes": 6}, [2, 8], [0, 6])
    assert _violation_for_model(h4, {}).endswith(
        "the user wants 8 dishes, at least 2 meat dishes, at most 6 vegetable dishes."
    )
    # No split at all: neither side is printed, as wanted or as present 0.
    plain = _h4_ranges(2, {"dishes": 1, "meat_dishes": 1, "vegetable_dishes": 0}, [0, 2], [0, 2])
    assert _violation_for_model(plain, {}) == (
        "The plan has 1 dish, 1 meat dish; the user wants 2 dishes."
    )
    assert unmet_for_user(plain, {}, "en") == (
        "The plan has 1 dish, 1 meat dish; 2 dishes were asked for"
    )
    assert "0" not in unmet_for_user(plain, {}, "zh")


def test_range_wording_in_retry_and_card():
    from minemydinner.agent.card_text import unmet_for_user
    from minemydinner.agent.graph import _violation_for_model

    # 4 dishes, at least 1 meat, at most 2 vegetable: meat [2, 4], vegetable [0, 2].
    h4 = _h4_ranges(4, {"dishes": 4, "meat_dishes": 1, "vegetable_dishes": 3}, [2, 4], [0, 2])
    assert _violation_for_model(h4, {}) == (
        "The plan has 4 dishes, 1 meat dish, 3 vegetable dishes; the user wants 4 dishes, "
        "at least 2 meat dishes, at most 2 vegetable dishes."
    )
    assert unmet_for_user(h4, {}, "zh") == (
        "方案有 4 道菜，1 道荤菜，3 道素菜，要求是 4 道菜，至少 2 道荤菜，最多 2 道素菜"
    )
    # Both named below a total of 4: 1 to 3 of each.
    both = _h4_ranges(4, {"dishes": 4, "meat_dishes": 0, "vegetable_dishes": 4}, [1, 3], [1, 3])
    assert unmet_for_user(both, {}, "en").endswith(
        "4 dishes, 1 to 3 meat dishes, 1 to 3 vegetable dishes were asked for"
    )


@pytest.mark.parametrize(
    "low, high, total, category, en, zh",
    [
        (6, 6, 8, "vegetable_dishes", "6 vegetable dishes", "6 道素菜"),
        (1, 1, 2, "meat_dishes", "1 meat dish", "1 道荤菜"),
        (0, 0, 3, "vegetable_dishes", "0 vegetable dishes", "0 道素菜"),
        (2, 3, 4, "meat_dishes", "2 to 3 meat dishes", "2 到 3 道荤菜"),
        (2, 8, 8, "meat_dishes", "at least 2 meat dishes", "至少 2 道荤菜"),
        (1, 3, 3, "meat_dishes", "at least 1 meat dish", "至少 1 道荤菜"),
        (0, 1, 3, "vegetable_dishes", "at most 1 vegetable dish", "最多 1 道素菜"),
        (0, 2, 4, "vegetable_dishes", "at most 2 vegetable dishes", "最多 2 道素菜"),
        (0, 5, 5, "meat_dishes", None, None),
        (0, 0, 0, "meat_dishes", None, None),
    ],
)
def test_side_phrase(low, high, total, category, en, zh):
    from minemydinner.agent.card_text import side_phrase

    assert side_phrase(low, high, total, category, False) == en
    assert side_phrase(low, high, total, category, True) == zh


def test_both_sides_named_keeps_its_wording():
    from minemydinner.agent.graph import _violation_for_model

    h4 = _h4(
        {"dishes": 4, "meat_dishes": 1, "vegetable_dishes": 3},
        {"dishes": 4, "meat_dishes": 2, "vegetable_dishes": 2},
    )
    assert _violation_for_model(h4, {}).endswith(
        "the user wants 4 dishes, 1 meat dish, 3 vegetable dishes."
    )


def test_request_summary_split_line():
    from minemydinner.agent.graph import _split_text

    def split(**values):
        return _split_text({"mode": "shopping", "soups": 0, **values})

    assert split(dishes=8, meat_dishes=2) == "2 meat dishes, 6 vegetable dishes"
    assert split(dishes=4, vegetable_dishes=1) == "3 meat dishes, 1 vegetable dish"
    assert split(dishes=8, meat_dishes=2, meat_bound="at_least") == (
        "at least 2 meat dishes, at most 6 vegetable dishes"
    )
    # A spec saved before bounds existed.
    assert split(dishes=8, meat_dishes=2, split_is_minimum=True) == (
        "at least 2 meat dishes, at most 6 vegetable dishes"
    )
    assert split(dishes=4, meat_dishes=1, vegetable_dishes=3) == "1 meat dish, 3 vegetable dishes"
    assert split(dishes=3, vegetable_dishes=1, vegetable_bound="at_most") == (
        "at least 2 meat dishes, at most 1 vegetable dish"
    )
    assert split(
        dishes=4, meat_dishes=1, meat_bound="at_least", vegetable_dishes=2, vegetable_bound="at_most"
    ) == "at least 2 meat dishes, at most 2 vegetable dishes"
    assert split(dishes=3, vegetable_dishes=0, vegetable_bound="exact") == (
        "3 meat dishes, 0 vegetable dishes"
    )
    assert split(dishes=4) == "none (any mix of meat and vegetable dishes)"
    assert split(dishes=0, soups=1) == "none (any mix of meat and vegetable dishes)"


def test_summary_for_at_most_one_vegetable(graph_env, session, veg, meat):
    for i in range(2):
        make_recipe(session, f"肉{i}", "meat dish")
    parse = {"mode": "shopping", "dishes": 3, "vegetable_dishes": 1, "vegetable_bound": "at_most"}
    plan(graph_env, "三个菜，最多一个素菜", parse=parse, replies=[final_proposal([veg.id])])
    opening = planner_requests(graph_env)[0]
    assert "Split: at least 2 meat dishes, at most 1 vegetable dish" in opening
    assert "Every count is exact unless it says at least, at most, or a range" in opening


def test_reason_text_for_an_implied_side():
    exact = {"reason": "not_enough_dishes", "category": "vegetable_dishes", "needed": 6,
             "available": 5, "ingredient": None, "minimum": False}
    assert reason_text(exact, "fridge_only", "en") == (
        "By the current fridge you can make 5 vegetable dishes; this meal needs 6 vegetable dishes"
    )
    at_least = {**exact, "category": "meat_dishes", "needed": 2, "available": 1, "minimum": True}
    assert reason_text(at_least, "fridge_only", "zh") == (
        "按冰箱现有的食材只能做 1 道荤菜，这餐需要 至少 2 道荤菜"
    )


def test_relax_split_for_a_short_implied_side():
    spec = {"mode": "fridge_only", "dishes": 8, "soups": 0, "meat_dishes": 2}
    actions = next_actions(spec, ["not_enough_dishes"], [], ["vegetable_dishes"])
    assert "relax_split" in actions
    # Only the dish total short: relaxing the split would not help.
    assert "relax_split" not in next_actions(spec, ["not_enough_dishes"], [], ["dishes"])
    # Nothing named: no split to relax.
    plain = {"mode": "fridge_only", "dishes": 8, "soups": 0}
    assert "relax_split" not in next_actions(plain, ["not_enough_dishes"], [], ["vegetable_dishes"])


def test_one_sided_split_retry_names_the_implied_side(graph_env, veg, meat):
    replies = [final_proposal([veg.id]), final_proposal([veg.id, meat.id])]
    plan(
        graph_env,
        "2 dishes, 1 of them meat",
        parse={"dishes": 2, "meat_dishes": 1},
        replies=replies,
    )
    retry = planner_requests(graph_env)[1]
    assert "the user wants 2 dishes, 1 meat dish, 1 vegetable dish" in retry
    assert "0 vegetable dishes" not in retry
    assert "Split: 1 meat dish, 1 vegetable dish" in planner_requests(graph_env)[0]


def test_planning_records_route_parse_then_planner_events(graph_env, veg):
    plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    events = run_record(graph_env).events
    calls = [e for e in events if e["kind"] == "model_call"]
    assert [e["step"] for e in calls] == ["route", "parse", "planner"]
    assert [e["kind"] for e in events[:3]] == ["model_call"] * 3
    route, parse, _ = calls
    assert route["messages"][0]["type"] == "system" and route["messages"][-1]["content"] == "dinner"
    assert route["response"] == {"intent": "plan"}
    assert parse["messages"][-1]["content"] == "dinner" and parse["response"]["mode"] is None
    assert route["model_name"] and route["fallback_used"] is False
    assert route["provider"] == "agent_primary" and route["served_model"] is None


def test_question_then_answer_records_both_parse_calls(graph_env, veg):
    set_request_style(graph_env.session, "ask_first")
    plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    resume(graph_env, "whatever")
    steps = [e["step"] for e in run_record(graph_env).events if e["kind"] == "model_call"]
    assert steps == ["route", "parse", "parse", "planner"]


def test_non_planning_message_records_nothing(graph_env):
    graph_env.model.structured["Intent"] = [{"intent": "other"}]
    before = len(graph_env.session.scalars(select(PlanningRun)).all())
    out = graph_env.graph.invoke(
        {"messages": [HumanMessage("hi")], "locale": "en"}, graph_env.config
    )
    assert out["messages"][-1].content == OTHER_REPLY_EN
    graph_env.session.expire_all()
    assert len(graph_env.session.scalars(select(PlanningRun)).all()) == before


def test_nickname_is_replaced_in_recorded_route_and_parse(tmp_path, session, seeded):
    set_request_style(session, "plan_then_adjust")
    stock(session, "小白菜", level="plenty")
    dish = make_recipe(session, "清炒白菜", "vegetable dish", [L("小白菜")])
    model = ScriptedChatModel(
        replies=[final_proposal([dish.id])],
        structured={
            "Intent": [{"intent": "plan"}],
            "ParsedRequest": [{"query": "what 小李 likes", "prefer_use": ["小李酱"]}],
        },
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(
        tmp_path, factory, model,
        pre_hooks=[lambda t: t.replace("小李", "Diner 2")],
        post_hooks=[lambda t: t.replace("Diner 2", "小李")],
    )
    graph = build_graph(GraphDeps(factory, client, PlanningRecorder(factory)), InMemorySaver())
    graph.invoke({"messages": [HumanMessage("dinner 小李 likes")], "locale": "en"}, thread("nick"))
    session.expire_all()
    record = session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    early = [e for e in record.events if e.get("step") in ("route", "parse")]
    assert len(early) == 2
    dumped = json.dumps(early, ensure_ascii=False)
    assert "小李" not in dumped and "dinner Diner 2 likes" in dumped
    assert early[1]["response"]["query"] == "what Diner 2 likes"


def test_buffered_calls_are_plain_and_cleared_after_the_run_starts(graph_env, veg):
    out = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])
    assert out["model_calls"] == []
    json.dumps(graph_env.graph.get_state(graph_env.config).values["model_calls"])


def test_non_planning_turn_does_not_leak_calls_into_the_next_run(graph_env, veg):
    graph_env.model.structured["Intent"] = [{"intent": "other"}, {"intent": "plan"}]
    out = graph_env.graph.invoke(
        {"messages": [HumanMessage("hi")], "locale": "en"}, graph_env.config
    )
    assert out["model_calls"] == []
    graph_env.model.replies = [final_proposal([veg.id])]
    graph_env.graph.invoke(
        {"messages": [HumanMessage("dinner")], "locale": "en"}, graph_env.config
    )
    steps = [e["step"] for e in run_record(graph_env).events if e["kind"] == "model_call"]
    assert steps == ["route", "parse", "planner"]


@pytest.mark.parametrize(
    "reason, category, en, zh",
    [
        ("no_cookware", "vegetable_dishes",
         "None of your vegetable dishes can be made with the cookware you have",
         "你的素菜都用不了现有的厨具"),
        ("no_cookware", "soups",
         "None of your soups can be made with the cookware you have", "你的汤都用不了现有的厨具"),
        ("time_limit", "meat_dishes", "None of your meat dishes fits the time limit",
         "你的荤菜都不符合时间限制"),
        ("time_limit", "staples", "None of your staple foods fits the time limit",
         "你的主食都不符合时间限制"),
        ("no_cookware", "dishes", "None of your recipes can be made with the cookware you have",
         "你的菜谱里没有一道能用你现有的厨具做出来"),
        ("no_cookware", None, "None of your recipes can be made with the cookware you have",
         "你的菜谱里没有一道能用你现有的厨具做出来"),
        ("time_limit", None, "None of your recipes fits the time limit",
         "你的菜谱里没有一道符合时间限制"),
    ],
)
def test_cookware_and_time_reasons_name_the_dish_type(reason, category, en, zh):
    short = {"reason": reason, "category": category, "needed": 1, "available": 0,
             "ingredient": None}
    assert reason_text(short, "shopping", "en") == en
    assert reason_text(short, "shopping", "zh") == zh


class ParseFailsOnce(ScriptedChatModel):
    """Answers the router, then raises on the first parse call."""

    fail_parse = True

    def with_structured_output(self, schema: type, include_raw: bool = False):
        if schema.__name__ == "ParsedRequest" and self.fail_parse:
            self.fail_parse = False
            raise RuntimeError("parse down")
        return super().with_structured_output(schema, include_raw)


def test_failed_turn_does_not_leak_calls_into_the_next_run(tmp_path, session, seeded, veg):
    model = ParseFailsOnce(structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]})
    env = make_graph_env(tmp_path, session, model=model)
    with pytest.raises(Exception):
        env.graph.invoke({"messages": [HumanMessage("dinner")], "locale": "en"}, env.config)
    env.model.replies = [final_proposal([veg.id])]
    env.graph.invoke({"messages": [HumanMessage("dinner again")], "locale": "en"}, env.config)
    steps = [e["step"] for e in run_record(env).events if e["kind"] == "model_call"]
    assert steps == ["route", "parse", "planner"]


def test_query_vectors_of_a_failed_run_are_dropped_by_the_next_run(
    tmp_path, session, seeded, veg, monkeypatch
):
    from minemydinner.agent import graph as graph_module

    env = make_graph_env(tmp_path, session, embeddings=FakeEmbeddings())
    real = graph_module.check_feasibility
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("feasibility re-check down")
        return real(*args, **kwargs)

    monkeypatch.setattr(graph_module, "check_feasibility", flaky)
    replies = [tool_call("search_recipes", {"query": "清淡"}), final_proposal([999999])]
    with pytest.raises(RuntimeError):
        plan(env, "清淡的", parse={"dishes": 1, "query": "清淡"}, replies=replies)
    assert len(env.deps.run_query_vectors) == 1
    plan(env, "清淡的", parse={"dishes": 1, "query": "清淡"}, replies=[final_proposal([veg.id])])
    assert env.deps.run_query_vectors == {}


# ---- the diner card and the label table (plan 2b) ----


def labelled_env(tmp_path, session, model=None, embeddings=None) -> GraphEnv:
    """A graph whose client has the app's nickname hooks."""
    from minemydinner.agent.labels import replace_nicknames, restore_labels

    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = model or ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            "FollowUpReading": [{"kind": "new_meal"}],
            "ParsedRequest": [{}],
        }
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(
        tmp_path,
        factory,
        model,
        embeddings=embeddings,
        pre_hooks=(replace_nicknames,),
        post_hooks=(restore_labels,),
    )
    deps = GraphDeps(factory, client, PlanningRecorder(factory))
    env = GraphEnv(build_graph(deps, InMemorySaver()), model, session, thread("labels-1"))
    env.deps = deps
    return env


def add_friend(session, name: str):
    from minemydinner.models import Diner

    diner = Diner(display_name=name, is_self=False)
    session.add(diner)
    session.flush()
    return diner


def sent_text(env: GraphEnv) -> str:
    """Every message the model received, system prompts left out (they hold no user data)."""
    return json.dumps(
        [[m.content for m in sent if m.type != "system"] for sent in env.model.sent],
        ensure_ascii=False,
    )


def test_graph_solo_request_has_no_card(tmp_path, session, seeded, veg):
    from minemydinner.agent.request import load_prompt

    add_friend(session, "鱼")
    make_recipe(session, "鱼香肉丝", "meat dish")
    env = labelled_env(tmp_path, session)
    out = plan(env, "想吃鱼香肉丝", parse={"query": "鱼香肉丝"}, replies=[final_proposal([veg.id])])
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    parse_calls = [
        s for s in env.model.sent if s and s[0].type == "system"
        and s[0].content == load_prompt("parse")
    ]
    assert parse_calls and parse_calls[-1][-1].content == "想吃鱼香肉丝"
    assert "Diner 2" not in sent_text(env)


def test_graph_card_after_style_and_question_gates(tmp_path, session, seeded, veg):
    env = labelled_env(tmp_path, session)
    set_request_style(session, None)
    guest = {"guests": [{"name": "Alex"}], "others_mentioned": True, "dishes": 2}
    out = plan(env, "我和 Alex 吃两个菜", parse=guest)
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "request_style"}]
    out = resume(env, "ask_first")
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "question", "fields": ["mode"]}]
    out = resume(env, "都行", parse={**guest, "mode": "shopping"})
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    assert [g["name"] for g in card.value["guests"]] == ["Alex"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [], "guests": [{"name": "Alex"}]}})
    assert "__interrupt__" not in out
    assert out["settled_diners"]["guest_names"] == {"guest:1": "Alex"}
    assert out["current_plan"] is not None


def test_graph_old_thread_without_labels_plans(tmp_path, session, seeded, veg):
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    # A thread saved before plan 2b: no diner_labels, no settled_diners.
    env.graph.update_state(
        env.config,
        {
            "messages": [HumanMessage("dinner"), AIMessage("Here is your dinner.")],
            "locale": "en",
            "plan_versions": [],
            "questions": [],
        },
        as_node="present_plan",
    )
    values = env.graph.get_state(env.config).values
    assert "diner_labels" not in values and "settled_diners" not in values
    out = plan(env, "dinner again", replies=[final_proposal([veg.id])])
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["diner_labels"] == {"labels": {f"diner:{me.id}": "Diner 1"}, "names": {}}


def test_graph_nickname_never_sent(tmp_path, session, seeded, veg):
    add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    parse = {
        "headcount": 2,
        "dishes": 1,
        "others_mentioned": True,
        "friend_rules": [{"code": "Diner 2", "allergens": ["peanut"]}],
    }
    out = plan(env, "我和小王吃饭，小王对花生过敏", parse=parse)
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    state = env.graph.get_state(env.config).values
    assert state["request"]["friend_rules"][0]["code"] == "Diner 2"
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: None})
    assert "__interrupt__" not in out
    record = run_record(env)
    model_calls = [e for e in record.events if e["kind"] == "model_call"]
    assert [e["step"] for e in model_calls] == ["route", "parse", "planner"]
    assert "小王" not in json.dumps(record.events, ensure_ascii=False)
    assert "小王" not in json.dumps(record.request, ensure_ascii=False)
    assert "小王" not in sent_text(env)
    assert "Diner 2" in json.dumps(model_calls[1]["messages"], ensure_ascii=False)
    assert out["request"]["friend_rules"][0]["code"] == "Diner 2"


def test_graph_planner_call_sees_the_label_context(tmp_path, session, seeded, veg):
    # The parse is scripted to return the nickname in its query, so the planner's opening
    # carries it: the middleware's call must still replace it.
    friend = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    parse = {"headcount": 2, "dishes": 1, "query": "小王爱吃的清淡菜"}
    (card,) = plan(env, "两个人吃", parse=parse)["__interrupt__"]
    env.model.replies = [final_proposal([veg.id], note="Diner 2 会喜欢这道菜")]
    out = resume(env, {card.id: None})
    (call,) = planner_calls(env)
    opening = [m for m in call if m.type == "human"][0].content
    assert "Diner 2爱吃的清淡菜" in opening and "小王" not in opening
    # The planner's note is restored for the user.
    assert out["messages"][-1].content == "小王 会喜欢这道菜"
    assert out["diner_labels"]["labels"][f"diner:{friend.id}"] == "Diner 2"


def test_graph_planner_tool_calls_see_the_label_context(tmp_path, session, seeded, veg):
    # The tool node runs each tool call in a worker thread; the search query the tool
    # embeds there still has the nickname replaced.
    import threading

    add_friend(session, "小王")
    threads = []

    class ThreadRecordingEmbeddings(FakeEmbeddings):
        def embed_documents(self, texts):
            threads.append(threading.current_thread())
            return super().embed_documents(texts)

    embeddings = ThreadRecordingEmbeddings()
    env = labelled_env(tmp_path, session, embeddings=embeddings)
    replies = [tool_call("search_recipes", {"query": "小王爱吃的"}), final_proposal([veg.id])]
    plan(env, "dinner", parse={"dishes": 1}, replies=replies)
    assert embeddings.embedded == ["Diner 2爱吃的"]
    assert threads and threads[0] is not threading.main_thread()


def test_graph_restored_tool_call_args_are_replaced_again(tmp_path, session, seeded, veg):
    # The planner's tool call is restored to the nickname in the agent's history; the next
    # planner call must send it as the label again, and record it so.
    add_friend(session, "小王")
    env = labelled_env(tmp_path, session, embeddings=FakeEmbeddings())
    # The parse's query names the friend, so the planner's opening gives them "Diner 2".
    parse = {"headcount": 2, "dishes": 1, "query": "小王爱吃的清淡菜"}
    (card,) = plan(env, "两个人吃", parse=parse)["__interrupt__"]
    env.model.replies = [
        tool_call("search_recipes", {"query": "Diner 2爱吃的"}),
        final_proposal([veg.id]),
    ]
    out = resume(env, {card.id: None})
    assert out["current_plan"]["passed"]
    calls = planner_calls(env)
    assert len(calls) == 2
    (sent_call,) = [m for m in calls[1] if m.type == "ai"]
    assert sent_call.tool_calls[0]["args"] == {"query": "Diner 2爱吃的"}
    assert "小王" not in json.dumps(
        [[m.model_dump() for m in call] for call in calls], ensure_ascii=False, default=str
    )
    assert "小王" not in json.dumps(run_record(env).events, ensure_ascii=False)


def test_graph_parse_fields_are_restored_but_friend_codes_stay(tmp_path, session, seeded):
    add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    # "小王酱" is no food term, so the model sees "Diner 2酱" and echoes it.
    parse = {
        "must_use": ["Diner 2酱"],
        "query": "Diner 2爱吃的",
        "friend_rules": [{"code": "Diner 2", "allergens": ["peanut"]}],
    }
    out = plan(env, "要用小王酱，小王对花生过敏", parse=parse)
    assert out["__interrupt__"][0].value["kind"] == "diners"
    request = env.graph.get_state(env.config).values["request"]
    assert request["must_use"] == ["小王酱"] and request["query"] == "小王爱吃的"
    assert request["friend_rules"][0]["code"] == "Diner 2"
    # The parse event, recorded later with the run, keeps the replaced form.
    (event,) = [e for e in env.graph.get_state(env.config).values["model_calls"]
                if e["step"] == "parse"]
    assert event["response"]["must_use"] == ["Diner 2酱"]
    assert "小王" not in json.dumps(event, ensure_ascii=False)


def test_graph_solo_self_rule_card_shows_only_the_user(tmp_path, session, seeded):
    me = self_diner(session)
    add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    out = plan(env, "我对花生过敏", parse={"self_rules": {"allergens": ["peanut"]}})
    (card,) = out["__interrupt__"]
    assert [row["diner_id"] for row in card.value["rows"]] == [me.id]


# ---- applying the diner card's answer (plan 2b, spec 5.1, 5.3) ----

PEANUT_RULE = {"rule_type": "allergy", "allergen": "peanut"}
ALEX_PARSE = {
    "headcount": 2,
    "others_mentioned": True,
    "dishes": 1,
    "mode": "shopping",
    "guests": [{"name": "Alex", "allergens": ["peanut"]}],
}


def plan_with_alex(tmp_path, session, veg):
    """"我和 Alex，他对花生过敏" planned after the card accepts Alex with peanut."""
    me = self_diner(session)
    stock(session, "花生", level="plenty")
    nuts = make_recipe(session, "凉拌花生", "vegetable dish", [L("花生")])
    env = labelled_env(tmp_path, session)
    out = plan(env, "我和 Alex，他对花生过敏", parse=ALEX_PARSE)
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [me.id], "guests": [{"name": "Alex", "rules": [PEANUT_RULE]}]}
    out = resume(env, {card.id: chosen})
    return env, me, nuts, out


GUEST_PEANUT = {
    "diner_key": "guest:1",
    "rule_type": "allergy",
    "allergen": "peanut",
    "ingredient_id": None,
    "text": None,
}


def test_graph_card_answer_plans_with_guest_rule(tmp_path, session, seeded, veg):
    env, me, nuts, out = plan_with_alex(tmp_path, session, veg)
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    constraints = out["constraints"]
    assert constraints["diner_ids"] == [me.id] and constraints["guest_rules"] == [GUEST_PEANUT]
    assert constraints["headcount"] == 2 and constraints["unconfirmed_diners"] == 0
    assert run_record(env).has_temporary_guests is True
    shortlist = shortlist_of(out["planner_opening"])
    offered = [e["id"] for group in shortlist.values() for e in group["candidates"]]
    assert veg.id in offered and nuts.id not in offered
    assert "Diner 2: allergy peanut" in out["planner_opening"]
    assert "Alex" not in out["planner_opening"]
    assert out["diner_labels"]["names"] == {"guest:1": "Alex"}
    (question,) = out["questions"]
    assert question["value"] == {
        "kind": "diners",
        "summary": {"self": True, "names": ["Alex"], "others": 0},
    }


def test_graph_follow_up_reuses_settled(tmp_path, session, seeded, veg):
    env, me, _, first = plan_with_alex(tmp_path, session, veg)
    # The parse reads the conversation, so the follow-up still has others mentioned.
    parse = {"dishes": 1, "mode": "shopping", "others_mentioned": True}
    out = plan(env, "荤素都行", parse=parse)
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["constraints"] == first["constraints"]
    assert run_record(env).has_temporary_guests is True


def test_graph_settled_friend_deleted_asks_again(tmp_path, session, seeded, veg):
    me = self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    out = plan(env, "我和小王吃饭", parse={"headcount": 2, "dishes": 1})
    (card,) = out["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id]}})
    assert out["settled_diners"]["diner_ids"] == [me.id, wang.id]
    session.delete(wang)
    session.flush()
    out = plan(env, "再来一个", parse={"dishes": 1})
    assert out["__interrupt__"][0].value["kind"] == "diners"


def test_graph_alone_after_a_shared_set_asks_again(tmp_path, session, seeded, veg):
    # "就我自己吃" changes who eats: the card shows with only the user, and confirming it
    # replaces the set (decided by the user, 2026-11-18; nothing is cleared silently).
    env, me, _, _ = plan_with_alex(tmp_path, session, veg)
    out = plan(env, "就我自己吃", parse={"headcount": 1, "dishes": 1, "mode": "shopping"})
    (card,) = out["__interrupt__"]
    assert [(row["diner_id"], row["selected"]) for row in card.value["rows"]] == [(me.id, True)]
    assert card.value["guests"] == [] and card.value["headcount"] == 1
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id], "headcount": 1}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["constraints"]["diner_ids"] == [me.id]
    assert out["constraints"]["guest_rules"] == []
    assert out["constraints"]["unconfirmed_diners"] == 0
    assert out["constraints"]["headcount"] == 1
    assert out["settled_diners"]["diner_ids"] == [me.id]
    assert out["settled_diners"]["guest_names"] == {}
    assert run_record(env).has_temporary_guests is False


SELF_PEANUT_PARSE = {
    "headcount": 1,
    "dishes": 1,
    "mode": "shopping",
    "self_rules": {"allergens": ["peanut"]},
}


def plan_solo_with_peanut(tmp_path, session, veg):
    """"我一个人吃，我对花生过敏" planned after the solo card keeps peanut for this meal."""
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    out = plan(env, "我一个人吃，我对花生过敏", parse=SELF_PEANUT_PARSE, locale="zh")
    (card,) = out["__interrupt__"]
    assert [row["diner_id"] for row in card.value["rows"]] == [me.id]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [me.id], "stated": {me.id: [PEANUT_RULE]}}
    out = resume(env, {card.id: chosen})
    self_peanut = {**GUEST_PEANUT, "diner_key": f"diner:{me.id}"}
    assert out["constraints"]["guest_rules"] == [self_peanut]
    return env, me, self_peanut, out


def test_graph_solo_meal_rule_survives_plan_again(tmp_path, session, seeded, veg):
    env, _, self_peanut, first = plan_solo_with_peanut(tmp_path, session, veg)
    out = plan(env, REPLAN_MESSAGE["zh"], replies=[final_proposal([veg.id])], locale="zh")
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["constraints"]["guest_rules"] == [self_peanut]
    assert out["settled_diners"] == first["settled_diners"]


def test_graph_solo_meal_rule_survives_a_solo_follow_up(tmp_path, session, seeded, veg):
    env, _, self_peanut, _ = plan_solo_with_peanut(tmp_path, session, veg)
    out = plan(
        env,
        "今晚做两个菜",
        parse={"headcount": 1, "dishes": 2, "mode": "shopping"},
        replies=[final_proposal([veg.id])],
        locale="zh",
    )
    assert "__interrupt__" not in out
    assert out["constraints"]["guest_rules"] == [self_peanut]
    assert out["constraints"]["headcount"] == 1


def test_graph_headcount_one_with_a_named_friend_asks(tmp_path, session, seeded, veg):
    # "给小王做饭" may parse as headcount 1: the friend's saved allergy still needs the card.
    me = self_diner(session)
    wang = add_friend(session, "小王")
    rule(session, wang.id, "allergy", allergen="peanut")
    env = labelled_env(tmp_path, session)
    out = plan(env, "给小王做饭", parse={"headcount": 1, "dishes": 1, "mode": "shopping"})
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    rows = {row["diner_id"]: row for row in card.value["rows"]}
    assert rows[me.id]["selected"] and rows[wang.id]["selected"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id]}})
    assert out["constraints"]["diner_ids"] == [me.id, wang.id]
    assert out["constraints"]["headcount"] == 2


def test_graph_friend_only_set_survives_a_follow_up_naming_the_friend(
    tmp_path, session, seeded, veg
):
    # "给小王做饭" with the user unticked settles {小王}, headcount 1; "再给小王做个汤" (also
    # parsed as headcount 1) keeps cooking for 小王, whose allergy still applies.
    me = self_diner(session)
    wang = add_friend(session, "小王")
    rule(session, wang.id, "allergy", allergen="peanut")
    env = labelled_env(tmp_path, session)
    one = {"headcount": 1, "dishes": 1, "mode": "shopping"}
    (card,) = plan(env, "给小王做饭", parse=one, locale="zh")["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [wang.id]}})
    assert out["settled_diners"]["diner_ids"] == [wang.id]
    out = plan(env, "再给小王做个汤", parse=one, replies=[final_proposal([veg.id])], locale="zh")
    assert "__interrupt__" not in out
    assert out["settled_diners"]["diner_ids"] == [wang.id]
    assert out["constraints"]["diner_ids"] == [wang.id]
    assert "allergy peanut" in out["planner_opening"]
    # A follow-up that names nobody says nobody else eats: the card asks (decided by the
    # user, 2026-11-19), preselecting only the user.
    out = plan(env, "再做个汤", parse=one, locale="zh")
    (card,) = out["__interrupt__"]
    assert [row["diner_id"] for row in card.value["rows"] if row["selected"]] == [me.id]


def test_graph_guest_only_set_survives_a_follow_up_naming_the_guest(
    tmp_path, session, seeded, veg
):
    self_diner(session)
    env = labelled_env(tmp_path, session)
    first = {"headcount": 1, "dishes": 1, "mode": "shopping",
             "guests": [{"name": "Alex", "allergens": ["peanut"]}]}
    (card,) = plan(env, "给 Alex 做饭，他对花生过敏", parse=first)["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [], "guests": [{"name": "Alex", "rules": [PEANUT_RULE]}]}
    out = resume(env, {card.id: chosen})
    assert out["constraints"]["guest_rules"] == [GUEST_PEANUT]
    again = {"headcount": 1, "dishes": 1, "mode": "shopping", "guests": [{"name": "Alex"}]}
    out = plan(env, "再给 Alex 做个汤", parse=again, replies=[final_proposal([veg.id])])
    assert "__interrupt__" not in out
    assert out["constraints"]["guest_rules"] == [GUEST_PEANUT]
    assert out["settled_diners"]["guest_names"] == {"guest:1": "Alex"}


def settle_me_and_wang(tmp_path, session, veg):
    """"我和小王吃饭" (headcount 2) confirmed with the user and 小王."""
    me = self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    (card,) = plan(env, "我和小王吃饭", parse={"headcount": 2, "dishes": 1})["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id], "headcount": 2}})
    assert out["settled_diners"]["stated_headcount"] == 2
    return env, me, wang, out


def test_graph_headcount_one_naming_nobody_asks_again(tmp_path, session, seeded, veg):
    # A follow-up whose parse says headcount 1 after {me, 小王} is a change: the card asks.
    env, me, wang, first = settle_me_and_wang(tmp_path, session, veg)
    out = plan(
        env, "换个辣的", parse={"headcount": 1, "dishes": 1, "query": "辣"}, locale="zh"
    )
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    assert env.graph.get_state(env.config).values["settled_diners"] == first["settled_diners"]


def test_graph_plan_again_asks_when_a_settled_friend_was_deleted(tmp_path, session, seeded, veg):
    # "Plan again" skips the card, except when a friend in the settled set was deleted since
    # (design 7.6: never drop a person silently).
    env, me, wang, _ = settle_me_and_wang(tmp_path, session, veg)
    session.delete(wang)
    session.flush()
    out = plan(env, REPLAN_MESSAGE["zh"], locale="zh")
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    assert [row["diner_id"] for row in card.value["rows"]] == [me.id]


def test_graph_plan_again_skips_the_card_otherwise(tmp_path, session, seeded, veg):
    env, me, wang, first = settle_me_and_wang(tmp_path, session, veg)
    out = plan(env, REPLAN_MESSAGE["zh"], replies=[final_proposal([veg.id])], locale="zh")
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["settled_diners"] == first["settled_diners"]


def test_graph_follow_up_repeating_the_headcount_keeps_the_set(tmp_path, session, seeded, veg):
    env, me, wang, first = settle_me_and_wang(tmp_path, session, veg)
    out = plan(
        env, "换个辣的", parse={"headcount": 2, "dishes": 1, "query": "辣"},
        replies=[final_proposal([veg.id])], locale="zh",
    )
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["settled_diners"] == first["settled_diners"]
    assert out["constraints"]["diner_ids"] == [me.id, wang.id]


def test_graph_edited_total_compares_with_the_stated_headcount(tmp_path, session, seeded, veg):
    # "我和朋友吃饭" (2) confirmed with the total raised to 3; "换个辣的" parsed as 2 again
    # is no change, so no card, and the confirmed 3 stays.
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    parse = {"headcount": 2, "others_mentioned": True, "dishes": 1}
    (card,) = plan(env, "我和朋友吃饭", parse=parse)["__interrupt__"]
    assert card.value["headcount"] == 2
    env.model.replies = [final_proposal([veg.id])]
    answer = {"selected": [me.id], "headcount": 3, "others_answer": "none"}
    out = resume(env, {card.id: answer})
    assert out["settled_diners"]["headcount"] == 3
    assert out["settled_diners"]["stated_headcount"] == 2
    out = plan(
        env, "换个辣的", parse={**parse, "query": "辣"},
        replies=[final_proposal([veg.id])], locale="zh",
    )
    assert "__interrupt__" not in out
    assert out["constraints"]["headcount"] == 3


def test_graph_old_set_compares_with_its_confirmed_headcount(tmp_path, session, seeded, veg):
    env, me, wang, first = settle_me_and_wang(tmp_path, session, veg)
    old = {k: v for k, v in first["settled_diners"].items() if k != "stated_headcount"}
    env.graph.update_state(env.config, {"settled_diners": old}, as_node="present_plan")
    out = plan(
        env, "换个辣的", parse={"headcount": 2, "dishes": 1, "query": "辣"},
        replies=[final_proposal([veg.id])], locale="zh",
    )
    assert "__interrupt__" not in out
    assert out["constraints"]["diner_ids"] == [me.id, wang.id]
    out = plan(env, "三个人吃", parse={"headcount": 3, "dishes": 1}, locale="zh")
    assert out["__interrupt__"][0].value["kind"] == "diners"


def test_graph_solo_card_with_an_added_guest_keeps_the_guest(tmp_path, session, seeded, veg):
    # "我一个人吃，我对花生过敏": on the solo card the user adds a guest with a peanut rule;
    # a follow-up naming nobody (headcount 1, as first stated) keeps the guest's rule.
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    (card,) = plan(env, "我一个人吃，我对花生过敏", parse=SELF_PEANUT_PARSE, locale="zh")[
        "__interrupt__"
    ]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {
        "selected": [me.id],
        "stated": {me.id: [PEANUT_RULE]},
        "guests": [{"name": None, "rules": [PEANUT_RULE]}],
        "headcount": 2,
    }
    out = resume(env, {card.id: chosen})
    guest_rule = {**GUEST_PEANUT, "diner_key": "guest:1"}
    assert guest_rule in out["constraints"]["guest_rules"]
    # A follow-up whose parse still mentions the others keeps the set and the guest's rule.
    out = plan(
        env, "今晚做两个菜",
        parse={"dishes": 2, "mode": "shopping", "others_mentioned": True},
        replies=[final_proposal([veg.id])], locale="zh",
    )
    assert "__interrupt__" not in out
    assert guest_rule in out["constraints"]["guest_rules"]
    assert out["constraints"]["headcount"] == 2
    # One whose parse says nobody else eats shows the card (decided by the user, 2026-11-19).
    out = plan(
        env, "就我自己吃", parse={"headcount": 1, "dishes": 2, "mode": "shopping"}, locale="zh"
    )
    assert out["__interrupt__"][0].value["kind"] == "diners"


def test_graph_alone_without_a_headcount_asks_after_a_shared_set(
    tmp_path, session, seeded, veg
):
    # The browser-check bug (2026-11-19): "今晚我和朋友吃饭，做三个菜" settled {me, 小王} with
    # no stated number; "就我自己吃" parsed with no headcount, no guest and no one else
    # mentioned must show the card with only the user preselected.
    me = self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    first = {"others_mentioned": True, "dishes": 3, "guests": [{"name": "朋友"}]}
    (card,) = plan(env, "今晚我和朋友吃饭，做三个菜", parse=first, locale="zh")["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id], "headcount": 2}})
    assert out["settled_diners"]["diner_ids"] == [me.id, wang.id]
    assert out["settled_diners"]["stated_headcount"] is None
    out = plan(env, "就我自己吃", parse={"dishes": 3}, locale="zh")
    (card,) = out["__interrupt__"]
    assert [row["diner_id"] for row in card.value["rows"]] == [me.id]
    assert card.value["rows"][0]["selected"] and card.value["guests"] == []
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id], "headcount": 1}})
    assert out["constraints"]["diner_ids"] == [me.id]
    assert out["settled_diners"]["diner_ids"] == [me.id]


def test_graph_solo_diet_word_shows_the_card_once(tmp_path, session, seeded, veg):
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    parse = {"dishes": 1, "mode": "shopping", "self_rules": {"diet_words": ["吃素"]}}
    out = plan(env, "我吃素，今晚吃什么", parse=parse, locale="zh")
    (card,) = out["__interrupt__"]
    (row,) = card.value["rows"]
    assert row["diner_id"] == me.id and row["diet_words"] == ["吃素"]
    assert row["proposed"] == []
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id]}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["constraints"]["guest_rules"] == []
    # The reused parse still holds the diet word; the settled set covers it.
    out = plan(env, REPLAN_MESSAGE["zh"], replies=[final_proposal([veg.id])], locale="zh")
    assert "__interrupt__" not in out and out["current_plan"]["passed"]


def test_graph_skipped_card_clears_settled(tmp_path, session, seeded, veg):
    env, me, _, _ = plan_with_alex(tmp_path, session, veg)
    out = plan(env, "再加两个人", parse={"headcount": 4, "dishes": 1, "mode": "shopping"})
    (card,) = out["__interrupt__"]
    out = resume(env, {card.id: None})
    assert out["settled_diners"] is None
    assert out["constraints"]["diner_ids"] == [me.id]
    assert out["constraints"]["guest_rules"] == []
    assert out["constraints"]["unconfirmed_diners"] == 3
    assert out["questions"][-1]["value"] == {"kind": "diners", "summary": None}


def test_graph_reply_never_shows_label(tmp_path, session, seeded, veg):
    me = self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    out = plan(env, "我和小王吃饭，小王对花生过敏", parse={"headcount": 2, "dishes": 1})
    (card,) = out["__interrupt__"]
    env.model.replies = [final_proposal([veg.id], note="Diner 2 不吃花生，宫保鸡丁已避开")]
    chosen = {"selected": [me.id, wang.id], "stated": {wang.id: [PEANUT_RULE]}}
    out = resume(env, {card.id: chosen})
    assert out["messages"][-1].content == "小王 不吃花生，宫保鸡丁已避开"
    assert out["constraints"]["guest_rules"] == [
        {**GUEST_PEANUT, "diner_key": f"diner:{wang.id}"}
    ]


def test_graph_saved_guest_still_marks_temporary_guests(tmp_path, session, seeded, veg):
    # Alex's name reached the provider in the route and parse calls, though Alex is saved
    # as a friend on the card.
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    (card,) = plan(env, "我和 Alex，他对花生过敏", parse=ALEX_PARSE)["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {
        "selected": [me.id],
        "guests": [{"name": "Alex", "rules": [PEANUT_RULE], "save": True}],
    }
    out = resume(env, {card.id: chosen})
    assert out["settled_diners"]["guest_names"] == {}
    assert len(out["constraints"]["diner_ids"]) == 2
    assert run_record(env).has_temporary_guests is True


def test_graph_skipped_card_with_a_guest_marks_temporary_guests(tmp_path, session, seeded, veg):
    self_diner(session)
    env = labelled_env(tmp_path, session)
    (card,) = plan(env, "我和 Alex，他对花生过敏", parse=ALEX_PARSE)["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: None})
    assert out["settled_diners"] is None
    assert run_record(env).has_temporary_guests is True


def test_graph_card_summary_is_read_before_the_commit(
    tmp_path, session, seeded, veg, monkeypatch
):
    from minemydinner.agent import graph as graph_module
    from minemydinner.models import Diner

    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    (card,) = plan(env, "我和 Alex", parse=ALEX_PARSE)["__interrupt__"]

    def broken(*args, **kwargs):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(graph_module, "answer_summary", broken)
    chosen = {"selected": [me.id], "guests": [{"name": "Alex", "save": True}]}
    with pytest.raises(RuntimeError):
        resume(env, {card.id: chosen})
    # The failure came before apply_answer, so no friend was created.
    session.expire_all()
    assert session.scalars(select(Diner).where(Diner.display_name == "Alex")).first() is None


def _unknown_dish(session):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    return make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])


def test_feasibility_stop_carries_left_out(graph_env, session):
    _early_stop_env(graph_env, session)
    odd = _unknown_dish(session)
    current = plan(graph_env, "dinner")["current_plan"]
    assert current["reasons"]
    assert current["left_out_unknown_allergens"] == ["神秘菜"]
    assert current["left_out_details"] == [{"id": odd.id, "title": "神秘菜", "lines": ["某种酱"]}]


def test_stop_ignores_unknown_dishes_of_unrequested_types(graph_env, session):
    _early_stop_env(graph_env, session)
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    make_recipe(session, "神秘甜点", "dessert", [L("某种酱", linked=False)])
    current = plan(graph_env, "dinner")["current_plan"]
    assert current["reasons"] and current["left_out_unknown_allergens"] == []


def test_successful_plan_lists_only_what_search_reached(graph_env, session, veg):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    make_recipe(session, "神秘甜点", "dessert", [L("某种酱", linked=False)])
    current = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])["current_plan"]
    assert current["passed"] is True
    assert current["left_out_unknown_allergens"] == []
    assert current["left_out_details"] == []


def test_no_valid_plan_carries_left_out(graph_env, session, veg):
    odd = _unknown_dish(session)
    stock(session, "鸡蛋", count=6)
    egg = make_recipe(session, "番茄炒蛋", "meat dish", [L("鸡蛋")])
    current = plan(graph_env, "dinner", replies=[final_proposal([egg.id])])["current_plan"]
    assert current["recipes"] == [] and [u["code"] for u in current["unmet"]] == ["H2"]
    assert current["left_out_unknown_allergens"] == ["神秘菜"]
    assert current["left_out_details"] == [{"id": odd.id, "title": "神秘菜", "lines": ["某种酱"]}]


def test_plan_without_a_card_lists_the_user_alone(graph_env, veg):
    current = plan(graph_env, "dinner", replies=[final_proposal([veg.id])])["current_plan"]
    assert current["diners"] == [{"name": None, "is_self": True, "guest": False}]
    assert current["others"] == 0
    assert current["left_out_details"] == []


def test_plan_diners_line(tmp_path, session, seeded, veg):
    me = self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    parse = {"headcount": 3, "dishes": 1, "mode": "shopping", "guests": [{"name": "Alex"}]}
    out = plan(env, "我、小王和 Alex 吃饭", parse=parse)
    (card,) = out["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [me.id, wang.id], "guests": [{"name": "Alex", "rules": []}]}
    out = resume(env, {card.id: chosen})
    assert out["current_plan"]["diners"] == [
        {"name": None, "is_self": True, "guest": False},
        {"name": "小王", "is_self": False, "guest": False},
        {"name": "Alex", "is_self": False, "guest": True},
    ]
    assert out["current_plan"]["others"] == 0
    # The record keeps the replaced form of the names.
    assert "小王" not in json.dumps(run_record(env).final_plan, ensure_ascii=False)


def test_plan_diners_line_without_the_user(tmp_path, session, seeded, veg):
    """A user who deselected themselves on the card is not on the diners line."""
    self_diner(session)
    wang = add_friend(session, "小王")
    env = labelled_env(tmp_path, session)
    parse = {"headcount": 2, "dishes": 1, "mode": "shopping", "guests": [{"name": "Alex"}]}
    out = plan(env, "给小王和 Alex 做饭", parse=parse)
    (card,) = out["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [wang.id], "guests": [{"name": "Alex", "rules": []}]}
    out = resume(env, {card.id: chosen})
    assert out["current_plan"]["diners"] == [
        {"name": "小王", "is_self": False, "guest": False},
        {"name": "Alex", "is_self": False, "guest": True},
    ]


# ---- planning again with the fixed message (spec 5.7) ----


def route_and_parse_calls(env: GraphEnv, since: int) -> list[str]:
    """The route, reader and parse model calls made after the first `since` calls, by step."""
    from minemydinner.agent.request import load_prompt

    steps = {
        load_prompt("route"): "route",
        load_prompt("follow_up"): "read_follow_up",
        load_prompt("parse"): "parse",
    }
    return [steps[s[0].content] for s in env.model.sent[since:] if s[0].content in steps]


@pytest.mark.parametrize(
    ("text", "locale"),
    [(REPLAN_MESSAGE["zh"], "zh"), (f"  {REPLAN_MESSAGE['en']} ", "en")],
)
def test_replan_message_reuses_the_previous_parse(tmp_path, session, seeded, veg, text, locale):
    env, _, _, first = plan_with_alex(tmp_path, session, veg)
    first_parsed = run_record(env).request["parsed"]
    since = len(env.model.sent)
    # A parse call would return a different request.
    out = plan(env, text, parse={"dishes": 5, "query": "红烧肉"}, locale=locale)
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert route_and_parse_calls(env, since) == []
    assert len(planner_calls(env)) == 2
    assert out["request"] == first["request"]
    assert out["constraints"] == first["constraints"]
    record = run_record(env)
    assert record.request["parsed"] == first_parsed
    assert record.request["locale"] == locale
    assert [e for e in record.events if e.get("step") in ("route", "parse")] == []
    assert record.events[0] == {"kind": "parse_reused"}


def test_replan_message_as_first_message_routes_and_parses(graph_env, veg):
    out = plan(
        graph_env,
        REPLAN_MESSAGE["zh"],
        parse={"dishes": 1, "mode": "shopping"},
        replies=[final_proposal([veg.id])],
        locale="zh",
    )
    assert out["current_plan"]["passed"]
    assert route_and_parse_calls(graph_env, 0) == ["route", "parse"]
    steps = [e.get("step") for e in run_record(graph_env).events]
    assert steps[:2] == ["route", "parse"]
    assert {"kind": "parse_reused"} not in run_record(graph_env).events


def test_replan_message_with_more_words_is_read_and_parsed(tmp_path, session, seeded, veg):
    env, _, _, _ = plan_with_alex(tmp_path, session, veg)
    since = len(env.model.sent)
    out = plan(
        env,
        f"{REPLAN_MESSAGE['zh']}，换个辣的",
        parse={"dishes": 1, "mode": "shopping", "query": "辣"},
        locale="zh",
    )
    assert out["current_plan"]["passed"]
    # Not the fixed text: a follow-up on the plan, read (here as a new meal) and parsed.
    assert route_and_parse_calls(env, since) == ["read_follow_up", "parse"]
    assert out["request"]["query"] == "辣"
    assert {"kind": "parse_reused"} not in run_record(env).events


# ---- routing a follow-up (plan 3b-1 spec 5.1, 5.3) ----


@dataclass
class ReviseEnv:
    graph: Any
    model: ScriptedChatModel
    session: Any
    config: dict
    dishes: dict


@pytest.fixture
def revise_env(tmp_path, session, seeded):
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = ScriptedChatModel(
        structured={
            "Intent": [{"intent": "plan"}],
            "FollowUpReading": [{"kind": "change"}],
            "ParsedRequest": [{"dishes": 2, "soups": 1, "mode": "shopping"}],
        }
    )
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, model)
    deps = GraphDeps(factory, client, PlanningRecorder(factory))
    dishes = {
        "土豆炖牛肉": make_recipe(session, "土豆炖牛肉", "meat dish", [L("牛肉", "main")]),
        "清炒小白菜": make_recipe(session, "清炒小白菜", "vegetable dish", [L("小白菜")]),
        "紫菜蛋花汤": make_recipe(session, "紫菜蛋花汤", "soup", [L("紫菜")]),
        "干煸豆角": make_recipe(session, "干煸豆角", "vegetable dish", [L("豆角"), L("辣椒")]),
        "青椒炒肉": make_recipe(session, "青椒炒肉", "meat dish", [L("青椒"), L("猪肉")]),
        "凉拌黄瓜": make_recipe(session, "凉拌黄瓜", "vegetable dish", [L("黄瓜"), L("辣椒")]),
    }
    return ReviseEnv(build_graph(deps, InMemorySaver()), model, session, thread("route-3"), dishes)


def dish_ids(env: ReviseEnv, *titles: str) -> list[int]:
    return [env.dishes[t].id for t in titles]


# The reading that stands for a route answer once the thread has a plan (plan 3b-2).
READ_AS = {"plan": "new_meal", "revise": "change", "other": "other"}


def say(env, text: str, intent: str, parse: dict | None = None, replies=None, locale="zh"):
    """A message whose route call answers `intent`; on a thread with a plan the reader answers
    the matching kind instead (`READ_AS`)."""
    env.model.structured["Intent"] = [{"intent": intent}]
    if intent in READ_AS:
        env.model.structured["FollowUpReading"] = [{"kind": READ_AS[intent]}]
    if parse is not None:
        env.model.structured["ParsedRequest"] = [parse]
    if replies is not None:
        env.model.replies = list(replies)
    return env.graph.invoke({"messages": [HumanMessage(text)], "locale": locale}, env.config)


MEAL = ("土豆炖牛肉", "清炒小白菜", "紫菜蛋花汤")


def first_meal(env: ReviseEnv, titles=MEAL, parse: dict | None = None) -> dict:
    ids = dish_ids(env, *titles)
    out = say(
        env,
        "今晚吃什么",
        "plan",
        parse=parse or {"dishes": 2, "soups": 1, "mode": "shopping"},
        replies=[final_proposal(ids)],
    )
    assert out["current_plan"]["passed"]
    assert [r["id"] for r in out["current_plan"]["recipes"]] == ids
    return out


def revision(target: str, remove=(), add=(), note: str = ""):
    removed = [{"id": i, "quote": q} for i, q in remove]
    args = {"target": target, "remove": removed, "add": list(add), "note": note}
    return tool_call("RevisionProposal", args)


def model_calls_by_prompt(env, kind: str) -> list[list]:
    from minemydinner.agent.request import load_prompt

    prompt = load_prompt(kind)
    return [s for s in env.model.sent if s and s[0].type == "system" and s[0].content == prompt]


def reviser_calls(env) -> list[list]:
    return [
        s for s in env.model.sent
        if s and s[0].type == "system" and "RevisionProposal" in s[0].content
    ]


def planner_runs(env) -> list[list]:
    return [
        s for s in env.model.sent
        if s and s[0].type == "system" and "PlanProposal" in s[0].content
    ]


def latest_record(env) -> PlanningRun:
    env.session.expire_all()
    return env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()


PLAN_NOTE = "[App note, not written by the user] Current plan: "


def test_current_plan_line():
    from minemydinner.agent.graph import current_plan_line

    def dish(title, dish_type):
        return {"id": 1, "title": title, "dish_type": dish_type}

    plan = {
        "recipes": [
            dish("土豆炖牛肉", "meat dish"),
            dish("清炒小白菜", "vegetable dish"),
            dish("紫菜蛋花汤", "soup"),
        ]
    }
    assert current_plan_line(plan) == PLAN_NOTE + (
        "2 dishes (1 meat, 1 vegetable), 1 soup: 土豆炖牛肉, 清炒小白菜, 紫菜蛋花汤"
    )
    many = {
        "recipes": [
            dish("清炒小白菜", "vegetable dish"),
            dish("米饭", "staple food"),
            dish("布丁", "dessert"),
            dish("薯片", "snack"),
            dish("汤A", "soup"),
            dish("汤B", "soup"),
        ]
    }
    assert current_plan_line(many) == PLAN_NOTE + (
        "1 dish (0 meat, 1 vegetable), 2 soups, 1 staple food, 1 dessert, "
        "1 snack: 清炒小白菜, 米饭, 布丁, 薯片, 汤A, 汤B"
    )
    assert current_plan_line({"recipes": [dish("汤A", "soup")]}) == PLAN_NOTE + "1 soup: 汤A"
    assert current_plan_line({"recipes": []}) == PLAN_NOTE + "no dishes"


def test_revise_without_a_plan_plans(revise_env):
    env = revise_env
    ids = dish_ids(env, *MEAL)
    out = say(env, "想要辣一些的", "revise", replies=[final_proposal(ids)])
    assert out["intent"] == "plan"
    assert out["current_plan"]["passed"] and "revision" not in out["current_plan"]
    assert len(planner_runs(env)) == 1 and reviser_calls(env) == []
    # No plan yet: the parse input has no plan line.
    assert model_calls_by_prompt(env, "parse")[-1][-1].content == "想要辣一些的"


def test_unsure_without_a_plan_plans(revise_env):
    env = revise_env
    out = say(env, "今晚吃什么", "unsure", replies=[final_proposal(dish_ids(env, *MEAL))])
    assert "__interrupt__" not in out and out["intent"] == "plan"
    assert out["current_plan"]["passed"] and reviser_calls(env) == []


def test_revise_goes_to_the_change_path(revise_env):
    """End to end: a scripted reader answers "change" after a plan, and the turn becomes a
    revised model version through the frame and the picker, with no parse call (plan 3b-2)."""
    env = revise_env
    first = first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [beans] = dish_ids(env, "干煸豆角")
    planned = len(planner_runs(env))
    parses = len(model_calls_by_prompt(env, "parse"))
    out = read(
        env,
        "想要辣一些的",
        {"kind": "change", "wish": "辣一些的", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)], note="加了点辣。")],
    )
    assert out["intent"] == "revise"
    # No load_context and no parse: the planner did not run again and the run is a revision.
    assert len(planner_runs(env)) == planned and reviser_calls(env) == []
    assert len(model_calls_by_prompt(env, "parse")) == parses
    record = latest_record(env)
    assert record.request["revision"] == {"from_version": 0}
    assert [e.get("step") for e in record.events[:1]] == ["read_follow_up"]
    plan = out["current_plan"]
    assert [r["id"] for r in plan["recipes"]] == [beef, beans, soup]
    assert plan["passed"] and plan["revision"] == {"from_version": 0}
    assert len(out["plan_versions"]) == len(first["plan_versions"]) + 1
    assert out["messages"][-1].content.startswith("把清炒小白菜换成了干煸豆角。")
    # The follow-up made no route call: the route ran once, for the first meal.
    assert len(model_calls_by_prompt(env, "route")) == 1
    assert out["previous_request"] == first["request"]
    # The plan was built from the first request; the passed revision is built from this one.
    assert first["plan_request"] == first["request"]
    assert out["plan_request"] == out["request"] and out["request"]["query"] == "辣一些的"


def read(env, text: str, reading: dict, parse=None, replies=None, locale="zh") -> dict:
    """A follow-up message on a plan, which the reader answers with `reading`."""
    env.model.structured["FollowUpReading"] = [reading]
    if parse is not None:
        env.model.structured["ParsedRequest"] = [parse]
    if replies is not None:
        env.model.replies = list(replies)
    return env.graph.invoke({"messages": [HumanMessage(text)], "locale": locale}, env.config)


def reader_calls(env) -> list[list]:
    return model_calls_by_prompt(env, "follow_up")


def test_a_change_reading_goes_to_the_change_path(revise_env):
    """Plan 3b-2: a follow-up on a plan is read once, with no route and no parse call; a
    change reading goes straight to the frame."""
    env = revise_env
    first_meal(env)
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    routes = len(model_calls_by_prompt(env, "route"))
    reading = {"kind": "change", "wish": "辣一些的", "wish_general": True}
    out = read(
        env, "想要辣一些的", reading,
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)])],
    )
    assert "__interrupt__" not in out and out["intent"] == "revise"
    assert len(reader_calls(env)) == 1
    assert len(model_calls_by_prompt(env, "route")) == routes
    assert len(picker_calls(env)) == 1 and out["current_plan"]["passed"]
    assert out["reading"]["kind"] == "change" and out["reading"]["wish"] == "辣一些的"
    record = latest_record(env)
    assert record.request["revision"] == {"from_version": 0}
    # The reading is recorded on the run as its first event, then the frame.
    assert [e.get("step") for e in record.events[:1]] == ["read_follow_up"]
    assert record.events[0]["kind"] == "model_call"
    assert record.events[0]["response"]["kind"] == "change"
    assert record.events[1]["kind"] == "frame"


def test_a_new_meal_reading_plans_a_new_meal(revise_env):
    env = revise_env
    first_meal(env)
    routes = len(model_calls_by_prompt(env, "route"))
    planned = len(planner_runs(env))
    ids = dish_ids(env, "青椒炒肉", "清炒小白菜", "紫菜蛋花汤")
    out = read(
        env,
        "明天中午也帮我配一顿",
        {"kind": "new_meal"},
        parse={"dishes": 2, "soups": 1, "mode": "shopping"},
        replies=[final_proposal(ids)],
    )
    assert "__interrupt__" not in out and out["intent"] == "plan"
    assert len(reader_calls(env)) == 1
    assert len(model_calls_by_prompt(env, "route")) == routes
    assert len(planner_runs(env)) == planned + 1 and reviser_calls(env) == []
    assert "revision" not in out["current_plan"]
    assert [r["id"] for r in out["current_plan"]["recipes"]] == ids
    # Parsed as a new request: the parse input has no plan line.
    assert model_calls_by_prompt(env, "parse")[-1][-1].content == "明天中午也帮我配一顿"
    record = latest_record(env)
    assert "revision" not in record.request
    assert [e.get("step") for e in record.events[:2]] == ["read_follow_up", "parse"]


def test_a_people_reading_shows_the_diner_card_and_plans_anew(tmp_path, session, seeded, veg):
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    out = plan(
        env, "今晚吃什么", parse={"dishes": 1, "mode": "shopping"},
        replies=[final_proposal([veg.id])], locale="zh",
    )
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    routes = len(model_calls_by_prompt(env, "route"))
    env.model.structured["FollowUpReading"] = [{"kind": "people"}]
    # The parse states nothing about who eats: the reading alone shows the card.
    out = plan(env, "我以后都不吃香菜", parse={"dishes": 1}, locale="zh")
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    assert len(model_calls_by_prompt(env, "follow_up")) == 1
    assert len(model_calls_by_prompt(env, "route")) == routes
    # Parsed as a follow-up on the plan: the parse input ends with the plan line.
    assert model_calls_by_prompt(env, "parse")[-1][-1].content.startswith(PLAN_NOTE)
    env.model.replies = [final_proposal([veg.id])]
    planned = len(planner_requests(env))
    out = resume(env, {card.id: {"selected": [me.id], "headcount": 1}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert len(planner_requests(env)) == planned + 1
    assert not any("RevisionProposal" in s[0].content for s in env.model.sent if s)
    assert "revision" not in out["current_plan"]
    assert [e.get("step") for e in run_record(env).events[:2]] == ["read_follow_up", "parse"]


def test_an_other_reading_leaves_the_plan_alone(revise_env):
    env = revise_env
    first = first_meal(env)
    parses = len(model_calls_by_prompt(env, "parse"))
    routes = len(model_calls_by_prompt(env, "route"))
    runs = latest_record(env).id
    out = read(env, "谢谢", {"kind": "other"})
    assert out["intent"] == "other" and out["messages"][-1].content == OTHER_REPLY_ZH
    assert len(reader_calls(env)) == 1
    assert len(model_calls_by_prompt(env, "parse")) == parses
    assert len(model_calls_by_prompt(env, "route")) == routes
    for key in ("current_plan", "request", "plan_request", "reply_plans", "plan_versions"):
        assert out[key] == first[key], key
    # No planning run: the turn ends with the fixed reply.
    assert latest_record(env).id == runs
    # The next message is read against the same plan.
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    swap = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(env, "白菜换掉", swap, replies=[pick(slots=[(0, beans)])])
    assert len(reader_calls(env)) == 2 and len(picker_calls(env)) == 1
    assert out["current_plan"]["passed"] and beans in ids_of_plan(out["current_plan"])
    assert latest_record(env).request["revision"] == {"from_version": 0}


def test_fixed_messages_after_a_plan_are_never_read(revise_env):
    """"Plan again" and the next-action buttons keep their own paths: no reader call."""
    env = revise_env
    first = first_meal(env)
    parses = len(model_calls_by_prompt(env, "parse"))
    routes = len(model_calls_by_prompt(env, "route"))
    planned = len(planner_runs(env))
    out = env.graph.invoke(
        {"messages": [HumanMessage(REPLAN_MESSAGE["zh"])], "locale": "zh"}, env.config
    )
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert out["request"] == first["request"]
    assert len(planner_runs(env)) == planned + 1
    out = env.graph.invoke(
        {"messages": [HumanMessage(ACTION_MESSAGES["zh"]["fewer_dishes"])], "locale": "zh"},
        env.config,
    )
    assert out["button"] == "fewer_dishes"
    assert reader_calls(env) == []
    assert len(model_calls_by_prompt(env, "parse")) == parses
    assert len(model_calls_by_prompt(env, "route")) == routes


def test_a_follow_up_after_an_early_stop_is_routed(graph_env, session):
    """An early stop has no dishes: a follow-up takes the route as before plan 3b-2, and an
    unsure answer plans (the choice card is no longer shown)."""
    _early_stop_env(graph_env, session)
    out = plan(graph_env, "dinner")
    assert out["current_plan"]["recipes"] == []
    graph_env.model.structured["Intent"] = [{"intent": "other"}]
    out = plan(graph_env, "hello")
    assert out["intent"] == "other" and out["messages"][-1].content == OTHER_REPLY_EN
    graph_env.model.structured["Intent"] = [{"intent": "unsure"}]
    out = plan(graph_env, "something else then")
    assert "__interrupt__" not in out and out["intent"] == "plan"
    assert reader_calls(graph_env) == []
    assert len(model_calls_by_prompt(graph_env, "route")) == 3


def test_an_old_pending_choice_card_still_resumes(revise_env):
    """A thread saved before plan 3b-2, paused on the choice card after its route call, is
    resumed as before: the answer "revise" revises with no reader call."""
    env = revise_env
    first = first_meal(env)
    env.graph.update_state(
        env.config,
        {
            "messages": [HumanMessage("来点辣的")],
            "intent": "unsure",
            "previous_request": first["request"],
            "asked_question": False,
            "button": None,
            "model_calls": [],
        },
        as_node="route_intent",
    )
    out = env.graph.invoke(None, env.config)
    (card,) = out["__interrupt__"]
    assert card.value == {"kind": "revise_or_plan", "message": "来点辣的"}
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    env.model.structured["ParsedRequest"] = [{"query": "辣"}]
    env.model.replies = [revision("general", remove=[(cabbage, "")], add=[beans])]
    out = env.graph.invoke(Command(resume="revise"), env.config)
    assert "__interrupt__" not in out and out["intent"] == "revise"
    assert out["current_plan"]["passed"] and len(reviser_calls(env)) == 1
    assert out["questions"][-1]["value"]["choice"] == "revise"
    assert reader_calls(env) == []
    # The thread goes on: the next follow-up is read and takes the change path, with the
    # old revise step's state left behind by the resumed turn.
    beef, beans_now = dish_ids(env, "土豆炖牛肉", "干煸豆角")
    pepper = dish_ids(env, "青椒炒肉")[0]
    swap = {"kind": "change", "remove": [{"id": beef, "mode": "replace", "quote": "牛肉"}]}
    after = read(env, "牛肉换掉", swap, replies=[pick(slots=[(0, pepper)])])
    assert "__interrupt__" not in after and pepper in ids_of_plan(after["current_plan"])
    assert after["revision"] is None and len(reader_calls(env)) == 1


def test_an_old_thread_state_is_read_and_revised(revise_env):
    """A thread saved before plan 3b-2 (a revision dict without the later fields, a card
    without its turn's words, no reading) is read and revised without error."""
    env = revise_env
    first_meal(env)
    env.graph.update_state(
        env.config, {"revision": {"from_version": 0, "message": "旧的", "recipe_ids": []}}
    )
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    swap = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(env, "白菜换掉", swap, replies=[pick(slots=[(0, beans)])])
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert len(reader_calls(env)) == 1 and len(picker_calls(env)) == 1


def test_the_reader_sees_the_plan_the_diners_and_this_meals_avoidance(
    tmp_path, session, seeded, veg
):
    """Spec 5.3 (Review Focus 3): the reader's input has the plan lines, the diners' rules
    as anonymous labels (never a saved nickname), and the current conditions with this meal's
    one-meal avoidance, so a message about something else can restate it."""
    me = self_diner(session)
    wang = add_friend(session, "小王")
    rule(session, wang.id, "allergy", allergen="peanut")
    rule(session, me.id, "dislike", ingredient="猪肉")
    env = labelled_env(tmp_path, session)
    parse = {
        "headcount": 2,
        "others_mentioned": True,
        "dishes": 1,
        "mode": "shopping",
        "time_limit_min": 40,
        "meal_avoid": ["香菜"],
    }
    (card,) = plan(env, "我和小王吃饭，不要香菜", parse=parse, locale="zh")["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id], "headcount": 2}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    env.model.structured["FollowUpReading"] = [{"kind": "other", "meal_avoid": ["香菜"]}]
    out = plan(env, "再加一道", locale="zh")
    (sent,) = model_calls_by_prompt(env, "follow_up")
    assert len(sent) == 2
    text = sent[1].content
    assert f"- {veg.id} 清炒白菜 (vegetable dish; 小白菜)" in text
    assert "Diner 1: dislikes 猪肉" in text
    assert "Diner 2: allergy peanut" in text
    assert "Leave out of this meal: 香菜" in text
    assert "Time limit: 40 minutes" in text and "Mode: shopping" in text
    assert "再加一道" in text and "Reply locale: zh" in text
    # A saved nickname never reaches the reader, in the diner lines or the conversation.
    assert "小王" not in text
    assert out["reading"]["meal_avoid"] == ["香菜"]


def test_the_reader_sees_which_dishes_break_this_meals_avoidance(revise_env):
    """The plan lines keep the revise step's marks: a dish that breaks this meal's one-meal
    avoidance (the plan card's own list) is marked, and the avoidance is a condition."""
    env = revise_env
    first = first_meal(env)
    env.graph.update_state(
        env.config, {"current_plan": {**first["current_plan"], "meal_avoid": ["牛肉"]}}
    )
    read(env, "谢谢", {"kind": "other", "meal_avoid": ["牛肉"]})
    (sent,) = reader_calls(env)
    text = sent[1].content
    beef, cabbage = dish_ids(env, "土豆炖牛肉", "清炒小白菜")
    assert f"- {beef} 土豆炖牛肉 (meat dish; 牛肉) BREAKS THIS MEAL'S WISH" in text
    assert f"- {cabbage} 清炒小白菜 (vegetable dish; 小白菜)\n" in text
    assert "Leave out of this meal: 牛肉" in text


def test_revise_never_asks_the_ask_first_question(revise_env):
    env = revise_env
    set_request_style(env.session, "ask_first")
    first_meal(env)
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    out = read(
        env, "想要辣一些的", {"kind": "change", "wish": "辣一些的", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)])],
    )
    assert "__interrupt__" not in out
    assert out["current_plan"]["passed"] and len(picker_calls(env)) == 1
    # Plan 3b-2 spec 5.6: a contradiction in the stated counts is no longer a question on a
    # change; the plan stays and the reply asks for the counts again.
    from minemydinner.agent.graph import UNCLEAR_REPLY_ZH

    current = out["current_plan"]
    out = read(env, "两个菜，三个荤菜", {"kind": "change", "counts": {"dishes": 2, "meat_dishes": 3}})
    assert "__interrupt__" not in out and out["messages"][-1].content == UNCLEAR_REPLY_ZH
    assert out["current_plan"] == current


def test_revise_with_a_diner_change_plans(tmp_path, session, seeded, veg):
    me = self_diner(session)
    env = labelled_env(tmp_path, session)
    out = plan(
        env, "今晚吃什么", parse={"dishes": 1, "mode": "shopping"},
        replies=[final_proposal([veg.id])],
    )
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    # Plan 3b-2: a change in who eats is read as people (a change reading takes no parse).
    env.model.structured["FollowUpReading"] = [{"kind": "people"}]
    out = plan(env, "再加一道，Alex 也来，他对花生过敏", parse=ALEX_PARSE)
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    # The turn was read as people: its parse input carried the plan line, and the card shows.
    assert model_calls_by_prompt(env, "parse")[-1][-1].content.startswith(PLAN_NOTE)
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [me.id], "guests": [{"name": "Alex", "rules": [PEANUT_RULE]}]}
    planned = len(planner_requests(env))
    out = resume(env, {card.id: chosen})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    # Planned from scratch: the planner ran, the reviser never did.
    assert len(planner_requests(env)) == planned + 1
    assert not any("RevisionProposal" in s[0].content for s in env.model.sent if s)
    assert "revision" not in out["current_plan"]
    assert "revision" not in run_record(env).request
    assert out["constraints"]["guest_rules"] == [GUEST_PEANUT]


# ---- a revise turn's unstated fields come from the plan's request (dev run 3 of 3b-1b) ----


def two_for_shopping(tmp_path, session, veg):
    """"我和小王吃饭" for two with shopping, the card confirming the user and 小王."""
    me = self_diner(session)
    wang = add_friend(session, "小王")
    spinach = make_recipe(session, "清炒菠菜", "vegetable dish", [L("菠菜")])
    env = labelled_env(tmp_path, session)
    parse = {"headcount": 2, "others_mentioned": True, "dishes": 1, "mode": "shopping"}
    (card,) = plan(env, "我和小王吃饭", parse=parse, locale="zh")["__interrupt__"]
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id, wang.id], "headcount": 2}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    env.model.structured["FollowUpReading"] = [{"kind": "change"}]
    return env, me, wang, spinach


def test_revise_with_nothing_said_about_who_eats_revises(tmp_path, session, seeded, veg):
    """Development run 3 (3b-1b), on the change path: a change reading carries everything it
    does not state from the plan's request, so who eats and the shopping mode stay, no card
    shows and the turn revises."""
    env, _, _, spinach = two_for_shopping(tmp_path, session, veg)
    swap = {"kind": "change", "remove": [{"id": veg.id, "mode": "replace", "quote": "白菜那道"}]}
    env.model.structured["FollowUpReading"] = [swap]
    out = plan(env, "白菜那道换个别的", replies=[pick(slots=[(0, spinach.id)])], locale="zh")
    assert "__interrupt__" not in out
    assert len(picker_calls(env)) == 1
    assert out["request"]["headcount"] == 2 and out["request"]["mode"] == "shopping"
    record = run_record(env)
    assert record.request["revision"]["from_version"] == 0
    assert record.request["spec"]["mode"] == "shopping"
    assert "mode" not in (record.defaults_used or [])
    assert [r["id"] for r in out["current_plan"]["recipes"]] == [spinach.id]


def test_revise_with_a_new_friend_rule_plans_anew_with_the_plan_mode(
    tmp_path, session, seeded, veg
):
    """A lasting rule in a follow-up shows the card and plans anew (spec 5.4); the new plan
    keeps the conditions of the plan's request that the parse does not state."""
    env, me, wang, _ = two_for_shopping(tmp_path, session, veg)
    env.model.structured["FollowUpReading"] = [{"kind": "people"}]
    parse = {"dishes": 1, "friend_rules": [{"code": "Diner 2", "allergens": ["peanut"]}]}
    out = plan(env, "小王对花生过敏，白菜那道换个别的", parse=parse, locale="zh")
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    env.model.replies = [final_proposal([veg.id])]
    chosen = {"selected": [me.id, wang.id], "stated": {wang.id: [PEANUT_RULE]}, "headcount": 2}
    out = resume(env, {card.id: chosen})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert reviser_calls(env) == []
    record = run_record(env)
    assert "revision" not in record.request
    assert record.request["spec"]["mode"] == "shopping"
    assert "mode" not in (record.defaults_used or [])
    assert out["constraints"]["headcount"] == 2


def test_revise_saying_only_me_still_shows_the_card(tmp_path, session, seeded, veg):
    """A stated headcount of 1 after a set of two is a change in who eats (decided by the
    user, 2026-11-19): the card shows, and the new plan keeps the plan's shopping mode."""
    env, me, _, _ = two_for_shopping(tmp_path, session, veg)
    env.model.structured["FollowUpReading"] = [{"kind": "people"}]
    out = plan(env, "就我自己吃", parse={"headcount": 1, "dishes": 1}, locale="zh")
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners" and card.value["headcount"] == 1
    env.model.replies = [final_proposal([veg.id])]
    out = resume(env, {card.id: {"selected": [me.id], "headcount": 1}})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert reviser_calls(env) == []
    assert run_record(env).request["spec"]["mode"] == "shopping"
    assert out["constraints"]["headcount"] == 1


def test_plan_again_still_plans_from_scratch(revise_env):
    env = revise_env
    first = first_meal(env)
    since = len(env.model.sent)
    ids = dish_ids(env, "青椒炒肉", "干煸豆角", "紫菜蛋花汤")
    out = say(env, REPLAN_MESSAGE["zh"], "revise", replies=[final_proposal(ids)])
    assert route_and_parse_calls(env, since) == []
    assert reviser_calls(env) == [] and len(planner_runs(env)) == 2
    assert out["request"] == first["request"]
    assert [r["id"] for r in out["current_plan"]["recipes"]] == ids
    assert "revision" not in out["current_plan"]


ACTION_TEXTS = [
    (locale, key, text) for locale, texts in ACTION_MESSAGES.items() for key, text in texts.items()
]


@pytest.mark.parametrize(("locale", "key", "text"), ACTION_TEXTS)
def test_action_message_after_a_no_plan_reply_plans_from_scratch(revise_env, locale, key, text):
    # Plan 3b-1b spec 5.5: the card's own request, changed in code; no route, no parse.
    env = revise_env
    # Nothing in stock, fridge only: the request stops before the planner (a no-plan reply).
    stopped = say(env, "今晚吃什么", "plan", parse={"dishes": 2, "mode": "fridge_only"})
    assert stopped["current_plan"]["recipes"] == [] and not stopped["current_plan"]["passed"]
    card = stopped["reply_plans"][stopped["messages"][-1].id]
    assert card["request"] == stopped["request"]
    since = len(env.model.sent)
    out = say(env, f" {text} ", "revise", replies=[final_proposal([])], locale=locale)
    assert "__interrupt__" not in out
    assert route_and_parse_calls(env, since) == []
    assert reviser_calls(env) == []
    assert out["intent"] == "plan" and "revision" not in out["current_plan"]
    assert out["request"] == apply_action(card["request"], key, card["spec"])
    record = latest_record(env)
    assert record.request["button"] == key and "revision" not in record.request


@pytest.mark.parametrize(("locale", "key", "text"), ACTION_TEXTS)
def test_action_message_after_a_plan_plans_from_scratch(revise_env, locale, key, text):
    env = revise_env
    first = first_meal(env)
    since = len(env.model.sent)
    ids = dish_ids(env, "青椒炒肉", "干煸豆角", "紫菜蛋花汤")
    out = say(env, text, "revise", replies=[final_proposal(ids)], locale=locale)
    assert "__interrupt__" not in out
    assert route_and_parse_calls(env, since) == []
    # Planned anew (fewer_dishes retries the scripted three dishes against its new total).
    assert reviser_calls(env) == [] and len(planner_runs(env)) >= 2
    assert out["request"] == apply_action(first["request"], key, first["current_plan"]["spec"])
    assert "revision" not in out["current_plan"]


def test_action_message_as_first_message_takes_the_normal_path(revise_env):
    env = revise_env
    ids = dish_ids(env, *MEAL)
    out = say(
        env, ACTION_MESSAGES["zh"]["relax_split"], "plan",
        parse={"dishes": 2, "soups": 1, "mode": "shopping"}, replies=[final_proposal(ids)],
    )
    assert out["current_plan"]["passed"]
    assert route_and_parse_calls(env, 0) == ["route", "parse"]


# ---- plan 3b-1b Task 6: next-action buttons in code (spec 5.5) ----


def test_button_revision_quotes_the_offering_turns_words(revise_env):
    """Follow-up 1 to development run 1, on the change path: a locked dish the offering turn
    named goes only with its quote. On the button turn the quote is checked against that
    turn's words (kept on its card), so the unfinished named replacement now passes; the
    record keeps the button and the turn's own message."""
    env = revise_env
    slow = make_recipe(
        env.session, "酱焖羊蝎子", "meat dish", [L("羊肉", "main")],
        time_min=75, time_source="stated",
    )
    limited = {"dishes": 2, "soups": 1, "mode": "shopping", "time_limit_min": 30}
    first_meal(env, parse=limited)
    [pepper] = dish_ids(env, "青椒炒肉")
    _hand_add(env, pepper)
    message = "青椒炒肉换成酱焖羊蝎子"
    named = {"id": pepper, "mode": "replace", "quote": "青椒炒肉", "with_name": "酱焖羊蝎子"}
    failed = read(env, message, {"kind": "change", "remove": [named]})
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["plan_kept"] is True and card["next_actions"] == ["drop_time_limit"]
    assert card["unfinished"]["words"] == message
    out = press(env, "drop_time_limit")
    plan = out["current_plan"]
    ids = ids_of_plan(plan)
    assert plan["passed"] and slow.id in ids and pepper not in ids
    assert "青椒炒肉是你手动加的，按你这次的话换成了酱焖羊蝎子。" in out["messages"][-1].content
    [frame] = change_events(env, "frame")
    assert frame["frame"]["ignored"] == [] and frame["frame"]["placed"] == [slow.id]
    record = latest_record(env)
    assert record.request["button"] == "drop_time_limit"
    assert record.request["messages"] == ["不限时间"]
    assert picker_calls(env) == []


def test_fewer_dishes_after_a_failed_add_shows_the_button_apart(revise_env):
    """Development run 2, item 7, on the change path: after a count wish nothing could fill,
    fewer_dishes re-runs the unfinished adds against its own lower count, and the picker
    reads the earlier wish as context, apart from the button."""
    env = revise_env
    first_meal(env)
    [cucumber] = dish_ids(env, "凉拌黄瓜")
    wish = "冰箱里的东西再多做两个菜"
    failed = read(
        env, wish, {"kind": "change", "counts": {"dishes": 4}},
        replies=[pick(slots=[(0, None), (1, None)])],
    )
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["plan_kept"] is True and card["request"]["counts_stated_now"] is True
    since = len(picker_calls(env))
    out = press(env, "fewer_dishes", replies=[pick(slots=[(0, cucumber)])])
    assert len(picker_calls(env)) == since + 1
    plan = out["current_plan"]
    assert plan["passed"] and cucumber in ids_of_plan(plan) and len(ids_of_plan(plan)) == 4
    assert latest_record(env).request["spec"]["dishes"] == 3
    text = picker_input(env)
    assert f"the user's earlier message: {wish}" in text
    assert "- Slot 0: a vegetable dish, added" in text and "Slot 1" not in text
    assert latest_record(env).request["messages"] == ["少做几道菜"]


def test_over_time_limit_sentence_in_both_languages():
    from minemydinner.agent.revise_step import over_limit_note

    dish = [{"id": 1, "title": "Braised ribs", "time_min": 60}]
    assert over_limit_note(dish, 30, "en") == (
        "Braised ribs is in your library but takes 60 minutes, longer than this meal's "
        "30-minute limit. Drop the time limit to use it."
    )
    assert over_limit_note(dish, 30, "zh") == (
        "Braised ribs在你的菜谱库里，但需要 60 分钟，超过了这次 30 分钟的时间限制。"
        "不限时间就可以用它。"
    )


def test_button_on_failed_revision_revises_again(revise_env):
    env = revise_env
    cabbage, beans, cucumber = _failed_seafood_wish(env)
    failed = env.graph.get_state(env.config).values
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["next_actions"] == ["switch_to_shopping", "import_recipes"]
    assert card["request"]["query"] == "海鲜" and card["plan_kept"] is True
    assert failed["plan_request"]["query"] is None
    out = _press_shopping_on_kept_plan(env, cabbage, beans, cucumber)
    record = latest_record(env)
    assert "from_version" in record.request["revision"]
    assert record.request["spec"]["mode"] == "shopping"
    assert record.request["messages"] == ["允许购物"]
    plan = out["current_plan"]
    assert plan["spec"]["mode"] == "shopping" and len(ids_of_plan(plan)) == 4
    # The wish reached the picker.
    assert "The user's wish: 海鲜" in picker_input(env)


FRIDGE_TWO_AND_SOUP = {"dishes": 2, "soups": 1, "mode": "fridge_only", "counts_stated_now": False}
SHOPPING_PRESSED = "Then the user pressed: 允许购物 (the request above already includes this change)"


def _failed_seafood_wish(env: ReviseEnv) -> tuple[int, int, int]:
    """A fridge-only plan with a dish added by hand, then a wish nothing fits: the plan stays
    and the card offers switch_to_shopping. Returns (cabbage, beans, cucumber)."""
    for name in ("牛肉", "小白菜", "紫菜", "豆角", "辣椒"):
        stock(env.session, name, level="plenty")
    first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "fridge_only"})
    cabbage, beans, cucumber = dish_ids(env, "清炒小白菜", "干煸豆角", "凉拌黄瓜")
    _hand_add(env, beans)
    failed = read(
        env, "想吃点海鲜", {"kind": "change", "wish": "海鲜", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, None)], wish_met=False)],
    )
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["plan_kept"] is True and "switch_to_shopping" in card["next_actions"]
    return cabbage, beans, cucumber


def _press_shopping_on_kept_plan(env: ReviseEnv, cabbage: int, beans: int, cucumber: int):
    """Press 允许购物 and check the turn revised the kept plan through the frame: no reader,
    route or parse call, the hand-added dish kept and still locked, the failed turn's words
    as the picker's wish context."""
    since = len(env.model.sent)
    picks = len(picker_calls(env))
    readers = len(reader_calls(env))
    out = press(
        env, "switch_to_shopping",
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, cucumber)], wish_met=True)],
    )
    assert "__interrupt__" not in out
    assert route_and_parse_calls(env, since) == [] and len(reader_calls(env)) == readers
    assert len(picker_calls(env)) == picks + 1
    assert out["intent"] == "revise"
    assert out["request"]["mode"] == "shopping" and out["request"]["query"] == "海鲜"
    assert latest_record(env).request["button"] == "switch_to_shopping"
    plan = out["current_plan"]
    ids = ids_of_plan(plan)
    assert plan["passed"] and beans in ids and cucumber in ids and cabbage not in ids
    assert beans in plan["locked"]
    assert "the user's earlier message: 想吃点海鲜" in picker_input(env)
    return out


def test_button_on_a_kept_card_after_a_reply_without_a_card(revise_env):
    """Final review I-1: the chat keeps the newest card's buttons after a reply without a card
    (an "other" reply here). The button still revises the kept plan, not a fresh plan."""
    env = revise_env
    dishes = _failed_seafood_wish(env)
    other_turn = say(env, "你好", "other")
    reply = other_turn["messages"][-1]
    assert reply.id not in (other_turn.get("reply_plans") or {})
    _press_shopping_on_kept_plan(env, *dishes)


def test_button_after_the_empty_plan_question_uses_the_failed_turns_wish(revise_env):
    """Final review I-1, on the change path: an empty-plan question (no card) between the
    failed change and the button. The button still re-runs the failed turn's unfinished wish,
    with that turn's words kept on its card."""
    from minemydinner.agent.revise_step import EMPTY_PLAN_QUESTION_ZH

    env = revise_env
    dishes = _failed_seafood_wish(env)
    shown = [r["id"] for r in env.graph.get_state(env.config).values["current_plan"]["recipes"]]
    # The dish added by hand is locked: it goes only with words that point at it.
    message = "全都不要了，豆角也不要"
    beans = dishes[1]
    remove = [
        {"id": i, "mode": "drop", "quote": "豆角" if i == beans else "全都不要了"} for i in shown
    ]
    empty = read(env, message, {"kind": "change", "remove": remove})
    assert empty["messages"][-1].content == EMPTY_PLAN_QUESTION_ZH
    assert empty["messages"][-1].id not in empty["reply_plans"]
    assert empty["change"]["words"] == message
    _press_shopping_on_kept_plan(env, *dishes)


@pytest.mark.parametrize("between", [False, True])
def test_button_on_a_kept_card_saved_before_its_words_were_kept(revise_env, between):
    """A kept card saved before cards kept their unfinished parts and words: the button
    re-runs the card's wish as a general wish, with the user's messages before the card's
    reply as its words."""
    env = revise_env
    dishes = _failed_seafood_wish(env)
    values = env.graph.get_state(env.config).values
    reply_plans = {
        key: {k: v for k, v in card.items() if k not in ("wish_words", "unfinished")}
        for key, card in values["reply_plans"].items()
    }
    env.graph.update_state(env.config, {"reply_plans": reply_plans}, as_node="present_plan")
    if between:
        say(env, "你好", "other")
    _press_shopping_on_kept_plan(env, *dishes)


def test_kept_card_keeps_its_turns_words_for_the_server_only(revise_env):
    from minemydinner.agent.plan_card import SERVER_ONLY_CARD_FIELDS, client_card

    env = revise_env
    _failed_seafood_wish(env)
    values = env.graph.get_state(env.config).values
    card = values["reply_plans"][values["messages"][-1].id]
    assert card["wish_words"] == "想吃点海鲜"
    assert card["unfinished"]["words"] == "想吃点海鲜"
    assert card["unfinished"]["reading"]["wish_general"] is True
    for field in ("wish_words", "meal_avoid", "unfinished"):
        assert field in card and field in SERVER_ONLY_CARD_FIELDS
        assert field not in client_card(card)


def _hand_grown_failed_wish(env: ReviseEnv) -> None:
    """Asked 3 dishes and a soup; a 4th dish added and the soup removed by hand; then a wish
    that states no count and fits nothing (the plan stays, the card keeps that turn's request
    with its stale counts: 3 dishes, 1 soup)."""
    from minemydinner.agent.edits import Remove, apply_edit
    from minemydinner.agent.plan_card import meal_constraints
    from minemydinner.planning.context import load_planning_context

    first_meal(env, (*THREE, "紫菜蛋花汤"), parse={"dishes": 3, "soups": 1, "mode": "shopping"})
    beans, soup, cabbage = dish_ids(env, "干煸豆角", "紫菜蛋花汤", "清炒小白菜")
    _hand_add(env, beans)
    values = env.graph.get_state(env.config).values
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, update = apply_edit(env.session, ctx, values, Remove(recipe_id=soup), "zh")
    assert outcome.plan is not None
    env.graph.update_state(env.config, update, as_node="present_plan")
    failed = read(
        env, "想吃点海鲜", {"kind": "change", "wish": "海鲜", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, None)], wish_met=False)],
    )
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["plan_kept"] and len(failed["current_plan"]["recipes"]) == 4
    assert (card["request"]["dishes"], card["request"]["soups"]) == (3, 1)


def test_fewer_dishes_after_a_failed_wish_counts_from_the_plan_shown(revise_env):
    # Fix round 1: "one fewer" is one fewer than the 4 dishes shown, not the stale request's
    # 3, and the soup removed by hand does not come back. On the change path the count is a
    # drop choice, and the unfinished wish is a replace choice again.
    env = revise_env
    _hand_grown_failed_wish(env)
    beef, cabbage, soup = dish_ids(env, "土豆炖牛肉", "清炒小白菜", "紫菜蛋花汤")
    since = len(env.model.sent)
    out = press(
        env, "fewer_dishes",
        replies=[pick(choices=[(0, [cabbage]), (1, [beef])], slots=[(0, None)])],
    )
    assert "__interrupt__" not in out and route_and_parse_calls(env, since) == []
    record = latest_record(env)
    assert record.request["button"] == "fewer_dishes"
    assert (record.request["spec"]["dishes"], record.request["spec"]["soups"]) == (3, 0)
    plan = out["current_plan"]
    ids = ids_of_plan(plan)
    assert plan["passed"] and len(ids) == 3 and cabbage not in ids and soup not in ids


def test_relax_split_after_a_failed_wish_keeps_the_plan_shown(revise_env):
    env = revise_env
    _hand_grown_failed_wish(env)
    [beef] = dish_ids(env, "土豆炖牛肉")
    out = press(env, "relax_split", replies=[pick(choices=[(0, [beef])], slots=[(0, None)])])
    assert "__interrupt__" not in out
    spec = latest_record(env).request["spec"]
    assert (spec["dishes"], spec["soups"]) == (4, 0)
    assert spec["meat_dishes"] is None and spec["vegetable_dishes"] is None
    assert len(out["current_plan"]["recipes"]) == 4


def test_restored_card_keeps_its_request(revise_env):
    from minemydinner.agent.edits import Restore, apply_edit
    from minemydinner.agent.plan_card import meal_constraints
    from minemydinner.planning.context import load_planning_context

    env = revise_env
    first = first_meal(env)
    _hand_add(env, dish_ids(env, "干煸豆角")[0])
    values = env.graph.get_state(env.config).values
    assert "request" not in values["current_plan"]
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, _ = apply_edit(env.session, ctx, values, Restore(), "zh")
    assert outcome.plan["request"] == first["request"]
    assert "plan_kept" not in outcome.plan


def test_seeded_first_plan_keeps_the_request(revise_env):
    # The revise evaluation's fixed first plan keeps the first turn's request, so a button
    # right after it takes the code path.
    from minemydinner.evaluation.revise_eval import replace_first_plan

    env = revise_env
    first = first_meal(env)
    factory = rollback_session_factory(env.session)
    replace_first_plan(env.graph, factory, env.config, ["青椒炒肉", "清炒小白菜", "紫菜蛋花汤"], "zh")
    values = env.graph.get_state(env.config).values
    assert values["current_plan"]["request"] == first["request"]
    since = len(env.model.sent)
    out = say(env, "允许购物", "plan", replies=[final_proposal(dish_ids(env, *MEAL))])
    assert route_and_parse_calls(env, since) == []
    assert out["request"] == apply_action(first["request"], "switch_to_shopping")


def test_button_on_a_new_plan_card_plans_from_scratch(revise_env):
    env = revise_env
    for name in ("小白菜", "豆角", "辣椒"):
        stock(env.session, name, level="plenty")
    # Two meat dishes from the fridge, with no meat at home: an early stop.
    stopped = say(
        env, "两个荤菜", "plan", parse={"dishes": 2, "meat_dishes": 2, "meat_bound": "exact"}
    )
    card = stopped["reply_plans"][stopped["messages"][-1].id]
    assert stopped["current_plan"]["reasons"] and "relax_split" in card["next_actions"]
    assert "plan_kept" not in card
    # Ask first now: the button's request is complete, so nothing is asked.
    set_request_style(env.session, "ask_first")
    since = len(env.model.sent)
    out = say(
        env, "荤素都行", "plan",
        replies=[final_proposal(dish_ids(env, "清炒小白菜", "干煸豆角"))],
    )
    assert "__interrupt__" not in out
    assert route_and_parse_calls(env, since) == []
    assert reviser_calls(env) == [] and len(planner_runs(env)) == 1
    assert out["request"]["meat_dishes"] is None and out["request"]["meat_bound"] is None
    assert out["request"]["dishes"] == 2 and out["request"]["mode"] is None
    record = latest_record(env)
    assert record.request["button"] == "relax_split" and "revision" not in record.request
    assert record.request["spec"]["meat_dishes"] is None
    assert out["current_plan"]["passed"]


def test_button_on_an_old_card_parses(revise_env):
    env = revise_env
    stopped = say(env, "今晚吃什么", "plan", parse={"dishes": 2, "mode": "fridge_only"})
    # A thread saved before cards kept their request.
    old = {
        reply_id: {k: v for k, v in card.items() if k != "request"}
        for reply_id, card in stopped["reply_plans"].items()
    }
    env.graph.update_state(env.config, {"reply_plans": old}, as_node="present_plan")
    since = len(env.model.sent)
    out = say(
        env, "允许购物", "revise",
        parse={"dishes": 2, "soups": 1, "mode": "shopping"},
        replies=[final_proposal(dish_ids(env, *MEAL))],
    )
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    # 3b-1 behaviour: no route call, but the parse relaxes the request.
    assert route_and_parse_calls(env, since) == ["parse"]
    assert out["request"]["soups"] == 1
    assert "button" not in latest_record(env).request


def _hand_add(env: ReviseEnv, recipe_id: int) -> None:
    from minemydinner.agent.edits import Add, apply_edit
    from minemydinner.agent.plan_card import meal_constraints
    from minemydinner.planning.context import load_planning_context

    values = env.graph.get_state(env.config).values
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, update = apply_edit(env.session, ctx, values, Add(recipe_id=recipe_id), "zh")
    assert outcome.plan is not None
    env.graph.update_state(env.config, update, as_node="present_plan")


THREE = ("土豆炖牛肉", "清炒小白菜", "青椒炒肉")


def test_hand_grown_plan_keeps_its_size_when_the_parse_repeats_the_count(revise_env):
    # Spec 5.2 (3b-1): asked for 3 dishes, a 4th added by hand; "想要辣一些的" keeps 4. On the
    # change path the reading states no count, so the plan's own counts stand.
    env = revise_env
    first_meal(env, THREE, parse={"dishes": 3, "mode": "shopping"})
    cabbage, beans, cucumber = dish_ids(env, "清炒小白菜", "干煸豆角", "凉拌黄瓜")
    _hand_add(env, beans)
    out = read(
        env, "想要辣一些的", {"kind": "change", "wish": "辣一些的", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, cucumber)])],
    )
    assert latest_record(env).request["spec"]["dishes"] == 4
    assert out["current_plan"]["passed"] and len(out["current_plan"]["recipes"]) == 4


def test_add_one_more_takes_the_new_count(revise_env):
    # "再加一道吧" read as dishes 4 on a 3-dish plan: one dish is added.
    env = revise_env
    first_meal(env, THREE, parse={"dishes": 3, "mode": "shopping"})
    beans = dish_ids(env, "干煸豆角")[0]
    out = read(
        env, "再加一道吧", {"kind": "change", "counts": {"dishes": 4}},
        replies=[pick(slots=[(0, beans)])],
    )
    assert latest_record(env).request["spec"]["dishes"] == 4
    assert out["current_plan"]["passed"] and len(out["current_plan"]["recipes"]) == 4


def test_add_one_more_to_a_hand_grown_plan(revise_env):
    # "再加一道吧" on a hand-grown plan of 4 (asked 3): read as 5 on the plan as shown.
    env = revise_env
    first_meal(env, THREE, parse={"dishes": 3, "mode": "shopping"})
    beans, cucumber = dish_ids(env, "干煸豆角", "凉拌黄瓜")
    _hand_add(env, beans)
    out = read(
        env, "再加一道吧", {"kind": "change", "counts": {"dishes": 5}},
        replies=[pick(slots=[(0, cucumber)])],
    )
    assert latest_record(env).request["spec"]["dishes"] == 5
    assert out["current_plan"]["passed"] and len(out["current_plan"]["recipes"]) == 5


def test_add_one_more_to_a_hand_trimmed_plan(revise_env):
    # Asked 3, one removed by hand (plan 2); "再加一道吧" read as 3 on the plan as shown,
    # equal to the first request's 3: the message states it, so the plan grows to 3.
    from minemydinner.agent.edits import Remove, apply_edit
    from minemydinner.agent.plan_card import meal_constraints
    from minemydinner.planning.context import load_planning_context

    env = revise_env
    first_meal(env, THREE, parse={"dishes": 3, "mode": "shopping"})
    pepper, beans = dish_ids(env, "青椒炒肉", "干煸豆角")
    values = env.graph.get_state(env.config).values
    ctx = load_planning_context(env.session, meal_constraints(values))
    outcome, update = apply_edit(env.session, ctx, values, Remove(recipe_id=pepper), "zh")
    assert outcome.plan is not None
    env.graph.update_state(env.config, update, as_node="present_plan")
    parses = len(model_calls_by_prompt(env, "parse"))
    out = read(
        env, "再加一道吧", {"kind": "change", "counts": {"dishes": 3}},
        replies=[pick(slots=[(0, beans)])],
    )
    assert latest_record(env).request["spec"]["dishes"] == 3
    assert out["current_plan"]["passed"] and len(out["current_plan"]["recipes"]) == 3
    # No parse on a change: the reader's counts are the statement.
    assert len(model_calls_by_prompt(env, "parse")) == parses


def test_revise_under_ask_first_keeps_fridge_only(revise_env):
    # Spec 5.3: what a change does not state comes from the current plan, never from
    # defaults, and nothing is asked.
    env = revise_env
    set_request_style(env.session, "ask_first")
    for name in ("牛肉", "小白菜", "紫菜", "豆角", "辣椒"):
        stock(env.session, name, level="plenty")
    first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "fridge_only"})
    assert latest_record(env).request["spec"]["mode"] == "fridge_only"
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    out = read(
        env, "想要辣一些的", {"kind": "change", "wish": "辣一些的", "wish_general": True},
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)])],
    )
    assert "__interrupt__" not in out and len(picker_calls(env)) == 1
    record = latest_record(env)
    assert record.request["revision"] == {"from_version": 0}
    assert record.request["spec"]["mode"] == "fridge_only"
    assert out["current_plan"]["passed"]


# ---- plan 3b-1b Task 5: one-meal wishes versus lasting rules (spec 5.4) ----


def _rule_count(session) -> int:
    from sqlalchemy import func

    from minemydinner.models import DietaryRule

    return session.scalar(select(func.count()).select_from(DietaryRule))


def test_meal_avoid_is_not_a_rule(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    rules = _rule_count(env.session)
    # The beef dish breaks this meal's wish; no meat dish fits it, so it is removed (spec 5.5).
    out = read(env, "今天不想吃肉", {"kind": "change", "meal_avoid": ["肉"]}, replies=[pick(slots=[(0, None)])])
    # No diner card: the change ran on the current plan.
    assert "__interrupt__" not in out
    assert len(picker_calls(env)) == 1 and out["current_plan"]["passed"]
    assert ids_of_plan(out["current_plan"]) == [cabbage, soup]
    assert "土豆炖牛肉去掉了：这顿不吃肉，也没找到可以替换的菜。" in out["messages"][-1].content
    assert out["request"]["meal_avoid"] == ["肉"]
    # The wish is this meal's own rule, never a person's, and never saved.
    # "肉" is a group word: one ingredient rule per meat entry of the dictionary.
    rules_now = out["constraints"]["guest_rules"]
    assert rules_now and {r["diner_key"] for r in rules_now} == {"meal"}
    assert {r["rule_type"] for r in rules_now} == {"dietary restriction"}
    assert all(r["ingredient_id"] is not None and r["text"] is None for r in rules_now)
    assert out["constraints"]["meal_avoid"] == ["肉"]
    assert "meal" not in json.dumps(out.get("diner_labels") or {})
    assert _rule_count(env.session) == rules
    assert out.get("settled_diners") is None


def test_lasting_rule_still_shows_the_card(revise_env):
    env = revise_env
    first_meal(env)
    # Plan 3b-2: a lasting rule about someone is read as people, which shows the card.
    env.model.structured["FollowUpReading"] = [{"kind": "people"}]
    out = plan(
        env,
        "我不吃肉",
        parse={
            "dishes": 2,
            "soups": 1,
            "mode": "shopping",
            "self_rules": {"avoid": [{"name": "肉", "kind": "restriction"}]},
        },
    )
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    planned = len(planner_runs(env))
    env.model.replies = [final_proposal(dish_ids(env, "清炒小白菜", "干煸豆角", "紫菜蛋花汤"))]
    out = resume(env, {card.id: None})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    # Planned anew (3b-1 behaviour), not revised.
    assert len(planner_runs(env)) == planned + 1 and not reviser_calls(env)
    assert "revision" not in out["current_plan"]


def test_meal_avoid_applies_to_a_new_plan(revise_env):
    env = revise_env
    rules = _rule_count(env.session)
    beef, cabbage, soup, pork = dish_ids(env, "土豆炖牛肉", "清炒小白菜", "紫菜蛋花汤", "青椒炒肉")
    out = say(
        env,
        "今天不想吃牛肉，两菜一汤",
        "plan",
        parse={"dishes": 2, "soups": 1, "mode": "shopping", "meal_avoid": ["牛肉"]},
        replies=[final_proposal([beef, cabbage, soup]), final_proposal([pork, cabbage, soup])],
    )
    assert "__interrupt__" not in out
    plan = out["current_plan"]
    assert plan["passed"] and beef not in [r["id"] for r in plan["recipes"]]
    assert plan["meal_avoid"] == ["牛肉"]
    # The beef dish failed as this meal's wish, named as the meal's, never as a diner's.
    record = latest_record(env)
    validations = [e for e in record.events if e["kind"] == "validation"]
    h2 = [v for v in validations[0]["result"]["violations"] if v["code"] == "H2"]
    assert h2 and {v["detail"]["diner_key"] for v in h2} == {"meal"}
    [rule_ref] = out["constraints"]["guest_rules"]
    assert rule_ref["diner_key"] == "meal" and rule_ref["ingredient_id"] is not None
    assert rule_ref["text"] is None
    opening, retry = [
        [m for m in s if m.type == "human"] for s in planner_runs(env)
    ][-1][:2]
    assert "This meal only, not a diner's rule: leave out 牛肉" in opening.content
    assert "left out of this meal" in retry.content
    assert "dietary limit of" not in retry.content
    assert _rule_count(env.session) == rules
    assert out.get("settled_diners") is None


def test_meal_avoid_block_is_worded_as_the_meals_wish(revise_env):
    """Every attempt breaks only this meal's wish: no plan is shown, and the reason names the
    wish, never a diner's allergy or restriction (fix round 1)."""
    env = revise_env
    beef, cabbage, soup = dish_ids(env, "土豆炖牛肉", "清炒小白菜", "紫菜蛋花汤")
    out = say(
        env,
        "今天不想吃牛肉，两菜一汤",
        "plan",
        parse={"dishes": 2, "soups": 1, "mode": "shopping", "meal_avoid": ["牛肉"]},
        replies=[final_proposal([beef, cabbage, soup])] * (MAX_RETRIES + 1),
    )
    plan = out["current_plan"]
    assert not plan["passed"] and plan["recipes"] == []
    assert plan["unmet"] == [
        {"code": "H2", "recipe_id": None, "text": "这顿不吃牛肉，菜谱库里没有足够不含它的菜"}
    ]


def test_meal_avoid_survives_a_diner_card_in_the_same_turn(revise_env):
    """The confirmed card replaces the constraints; the meal's wish is added after it."""
    env = revise_env
    me = self_diner(env.session)
    beef, cabbage, soup, pork = dish_ids(env, "土豆炖牛肉", "清炒小白菜", "紫菜蛋花汤", "青椒炒肉")
    parse = {**ALEX_PARSE, "dishes": 2, "soups": 1, "meal_avoid": ["牛肉"]}
    out = say(env, "我和 Alex，今天不想吃牛肉", "plan", parse=parse)
    (card,) = out["__interrupt__"]
    assert card.value["kind"] == "diners"
    env.model.replies = [final_proposal([beef, cabbage, soup]), final_proposal([pork, cabbage, soup])]
    chosen = {"selected": [me.id], "guests": [{"name": "Alex", "rules": [PEANUT_RULE]}]}
    out = resume(env, {card.id: chosen})
    assert "__interrupt__" not in out and out["current_plan"]["passed"]
    assert beef not in [r["id"] for r in out["current_plan"]["recipes"]]
    rules_now = out["constraints"]["guest_rules"]
    assert GUEST_PEANUT in rules_now
    assert {r["diner_key"] for r in rules_now} == {"guest:1", "meal"}
    assert out["constraints"]["meal_avoid"] == ["牛肉"]
    assert out["current_plan"]["meal_avoid"] == ["牛肉"]


# ---- a new plan with a taste wish the library cannot meet (plan 3b-1b spec 5.6) ----


def _unmet_proposal(ids: list[int], note: str) -> AIMessage:
    return tool_call(
        "PlanProposal",
        {
            "recipe_ids": ids,
            "note": note,
            "wish_met": False,
            "wish_unmet_reason": "菜谱库里没有辣的菜",
        },
    )


def test_unmet_wish_plans_and_says_so(graph_env, veg, meat):
    out = plan(
        graph_env,
        "两个菜，想吃辣的",
        parse={"dishes": 2, "query": "辣的"},
        replies=[_unmet_proposal([meat.id, veg.id], "先炖土豆炖肉，再炒白菜。")],
        locale="zh",
    )
    current = out["current_plan"]
    assert current["passed"] is True
    assert {r["id"] for r in current["recipes"]} == {meat.id, veg.id}
    reply = out["messages"][-1].content
    assert reply == WISH_UNMET_NOTE_ZH.format(wish="辣的") + "\n\n" + "先炖土豆炖肉，再炒白菜。"
    assert [u["code"] for u in current["unmet"]] == ["wish_unmet"]
    # Fridge-only mode (the default): allow shopping, and import recipes.
    assert current["next_actions"] == ["switch_to_shopping", "import_recipes"]
    assert len(planner_requests(graph_env)) == 1


def test_unmet_flag_without_a_wish_adds_nothing(graph_env, veg, meat):
    # wish_met=False without a wish in the request is ignored.
    out = plan(
        graph_env,
        "two dishes",
        parse={"dishes": 2},
        replies=[_unmet_proposal([meat.id, veg.id], "Start the stew first.")],
    )
    assert out["messages"][-1].content == "Start the stew first."
    assert out["current_plan"]["unmet"] == [] and out["current_plan"]["next_actions"] == []


def test_met_wish_adds_nothing(graph_env, veg, meat):
    met = tool_call(
        "PlanProposal",
        {"recipe_ids": [meat.id, veg.id], "note": "先炖土豆炖肉。", "wish_met": True},
    )
    out = plan(
        graph_env, "两个菜，想吃家常的", parse={"dishes": 2, "query": "家常的"},
        replies=[met], locale="zh",
    )
    assert out["current_plan"]["passed"] is True
    assert out["messages"][-1].content == "先炖土豆炖肉。"
    assert out["current_plan"]["unmet"] == [] and out["current_plan"]["next_actions"] == []
    assert run_record(graph_env).first_passed is True


def test_no_answer_falls_back_to_the_shortlist(graph_env, veg, meat):
    # The planner searches on every call and never answers, so every attempt hits its limit.
    out = plan(
        graph_env,
        "两个菜，想吃辣的",
        parse={"dishes": 2, "query": "辣的"},
        replies=[tool_call("search_recipes", {"query": "辣的"})],
        locale="zh",
    )
    current = out["current_plan"]
    assert current["passed"] is True and out["retries"] == 0
    assert {r["id"] for r in current["recipes"]} == {meat.id, veg.id}
    reply = out["messages"][-1].content
    assert reply.startswith(WISH_UNMET_NOTE_ZH.format(wish="辣的"))
    texts = [reply, *(u["text"] for u in current["unmet"])]
    assert not any("0 道菜" in text for text in texts)
    assert [u["code"] for u in current["unmet"]] == ["wish_unmet"]
    assert "import_recipes" in current["next_actions"]
    (validation,) = [e for e in planning_events(run_record(graph_env)) if e["kind"] == "validation"]
    assert sorted(validation["recipe_ids"]) == sorted([meat.id, veg.id])
    assert validation["fallback"] is True
    # The planner did not pass: a passing code fallback is never a first pass.
    record = run_record(graph_env)
    assert record.first_passed is False and record.final_passed is True


def test_no_answer_without_a_wish_falls_back_with_a_plain_note(graph_env, veg, meat):
    out = plan(
        graph_env,
        "two dishes",
        parse={"dishes": 2},
        replies=[tool_call("search_recipes", {"query": "anything"})],
    )
    current = out["current_plan"]
    assert current["passed"] is True and current["unmet"] == []
    assert out["messages"][-1].content == FALLBACK_NOTE_EN


def test_hard_shortfall_still_stops(graph_env, veg, meat):
    # Three dishes from a library with two: the request stops before the planner, with reasons.
    out = plan(
        graph_env,
        "三个菜，想吃辣的",
        parse={"dishes": 3, "query": "辣的"},
        replies=[tool_call("search_recipes", {"query": "辣的"})],
        locale="zh",
    )
    current = out["current_plan"]
    assert planner_requests(graph_env) == []
    assert current["recipes"] == [] and current["passed"] is False
    assert current["reasons"]
    assert out["messages"][-1].content == EARLY_STOP_NOTE_ZH
    assert "0 道菜" not in out["messages"][-1].content


def test_an_empty_answer_is_never_shown_as_zero_dishes(graph_env, veg):
    out = plan(graph_env, "晚饭", replies=[final_proposal([])], locale="zh")
    current = out["current_plan"]
    assert current["recipes"] == [] and current["passed"] is False
    texts = [out["messages"][-1].content, *(u["text"] for u in current["unmet"])]
    assert not any("0 道菜" in text for text in texts)
    assert out["messages"][-1].content == EMPTY_PLAN_NOTE_ZH
    assert current["unmet"] == [] and current["next_actions"] == []


def test_a_failing_fallback_is_retried_like_any_attempt(graph_env, session, veg, monkeypatch):
    # The fallback is checked like any attempt: here it picks a dish that cannot be made from
    # the fridge (H1), and the retry tells the planner that code picked it.
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    monkeypatch.setattr("minemydinner.agent.graph.fallback_ids", lambda entries, spec: [pork.id])
    replies = [tool_call("search_recipes", {})] * MODEL_CALL_LIMIT + [final_proposal([veg.id])]
    out = plan(graph_env, "dinner", parse={"dishes": 1}, replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"] is True
    assert [r["id"] for r in out["current_plan"]["recipes"]] == [veg.id]
    retry = planner_requests(graph_env)[MODEL_CALL_LIMIT]
    assert f"You gave no final answer, so code picked [{pork.id}] from the shortlist" in retry
    assert "not in stock: 五花肉" in retry
    validations = [e for e in planning_events(run_record(graph_env)) if e["kind"] == "validation"]
    assert [v.get("fallback", False) for v in validations] == [True, False]


def test_a_fallback_failing_every_attempt_ends_as_the_closest_plan(
    graph_env, session, veg, monkeypatch
):
    # The planner never answers, and every fallback picks a dish the fridge cannot make (H1):
    # the closest plan is shown with the wish sentence, then the closest note.
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    monkeypatch.setattr("minemydinner.agent.graph.fallback_ids", lambda entries, spec: [pork.id])
    out = plan(
        graph_env,
        "一个菜，想吃辣的",
        parse={"dishes": 1, "query": "辣的"},
        replies=[tool_call("search_recipes", {"query": "辣的"})],
        locale="zh",
    )
    current = out["current_plan"]
    assert out["retries"] == MAX_RETRIES and current["passed"] is False
    assert [r["id"] for r in current["recipes"]] == [pork.id]
    assert out["messages"][-1].content == (
        WISH_UNMET_NOTE_ZH.format(wish="辣的") + "\n\n" + CLOSEST_NOTE_ZH
    )
    assert [u["code"] for u in current["unmet"]] == ["H1", "wish_unmet"]
    assert current["next_actions"] == ["switch_to_shopping", "update_inventory", "import_recipes"]
    assert run_record(graph_env).first_passed is False


# ---- plan 3b-2 Task 7: the change path (spec 5.4-5.6) ----


def pick(slots=(), choices=(), wish_met=None, note: str = "") -> AIMessage:
    """The picker's answer: `slots` as (index, recipe id or None), `choices` as (index, ids)."""
    return tool_call(
        "PickerAnswer",
        {
            "choices": [{"index": i, "ids": list(ids)} for i, ids in choices],
            "slots": [
                {"index": i, "recipe_id": r, "reason": "" if r else "没有合适的"}
                for i, r in slots
            ],
            "wish_met": wish_met,
            "note": note,
        },
    )


def picker_calls(env) -> list[list]:
    return model_calls_by_prompt(env, "pick")


def picker_input(env, call: int = -1) -> str:
    return [m for m in picker_calls(env)[call] if m.type == "human"][0].content


def ids_of_plan(plan: dict) -> list[int]:
    return [r["id"] for r in plan["recipes"]]


def change_events(env, kind: str) -> list[dict]:
    return [e for e in latest_record(env).events if e.get("kind") == kind]


def press(env, key: str, replies=None) -> dict:
    if replies is not None:
        env.model.replies = list(replies)
    return env.graph.invoke(
        {"messages": [HumanMessage(ACTION_MESSAGES["zh"][key])], "locale": "zh"}, env.config
    )


def test_a_named_drop_makes_one_reader_call_and_no_picker_call(revise_env):
    env = revise_env
    first = first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    reading = {"kind": "change", "remove": [{"id": soup, "mode": "drop", "quote": "汤"}]}
    out = read(env, "汤不要了", reading)
    assert "__interrupt__" not in out and out["intent"] == "revise"
    assert len(reader_calls(env)) == 1 and picker_calls(env) == []
    # No parse, no reviser and no planner on a change.
    assert len(model_calls_by_prompt(env, "parse")) == 1 and reviser_calls(env) == []
    plan = out["current_plan"]
    assert ids_of_plan(plan) == [beef, cabbage]
    assert plan["passed"] and plan["revision"] == {"from_version": 0}
    assert len(out["plan_versions"]) == len(first["plan_versions"]) + 1
    assert out["messages"][-1].content == "去掉了紫菜蛋花汤。"
    assert out["plan_request"] == out["request"]
    record = latest_record(env)
    assert record.request["revision"] == {"from_version": 0}
    assert record.request["reading"]["remove"][0]["id"] == soup
    assert record.final_passed is True
    assert [e.get("step") for e in record.events[:1]] == ["read_follow_up"]
    [frame] = change_events(env, "frame")
    assert frame["frame"]["drops"] == [soup] and frame["frame"]["ignored"] == []
    [outcome] = change_events(env, "change_outcome")
    assert outcome["recipe_ids"] == [beef, cabbage]


def test_a_named_replace_makes_one_reader_and_one_picker_call(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [beans] = dish_ids(env, "干煸豆角")
    reading = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(env, "白菜换掉", reading, replies=[pick(slots=[(0, beans)], note="干煸豆角下饭。")])
    assert len(reader_calls(env)) == 1 and len(picker_calls(env)) == 1
    plan = out["current_plan"]
    # The new dish takes the replaced dish's place.
    assert ids_of_plan(plan) == [beef, beans, soup] and plan["passed"]
    assert out["messages"][-1].content == "把清炒小白菜换成了干煸豆角。\n\n干煸豆角下饭。"
    text = picker_input(env)
    assert f"- Slot 0: a vegetable dish in place of {cabbage}" in text
    assert "Shortlist" in text and "Reply locale: zh" in text
    [attempt] = change_events(env, "pick_attempt")
    assert attempt["attempt"] == 0 and attempt["problems"] == []
    assert any(e.get("step") == "pick" for e in latest_record(env).events)


def test_a_general_wish_replaces_only_unlocked_dishes_within_the_limit(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    pepper, beans = dish_ids(env, "青椒炒肉", "干煸豆角")
    _hand_add(env, pepper)
    reading = {"kind": "change", "wish": "辣一些的", "wish_general": True}
    out = read(
        env, "想要辣一些的", reading,
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)], wish_met=True)],
    )
    # Three unlocked dishes: at most two may change; the locked dish is never a candidate.
    text = picker_input(env)
    assert f"- Choice 0: replace 1 to 2 of {beef}, {cabbage}, {soup} (any type)" in text
    assert "The user's wish: 辣一些的" in text
    plan = out["current_plan"]
    assert ids_of_plan(plan) == [beef, beans, soup, pepper] and pepper in plan["locked"]
    assert out["messages"][-1].content.startswith("把清炒小白菜换成了干煸豆角。")
    assert out["request"]["query"] == "辣一些的"


def test_a_new_one_meal_avoidance_replaces_a_locked_dish_and_says_so(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [pepper] = dish_ids(env, "青椒炒肉")
    chicken = make_recipe(env.session, "红烧鸡块", "meat dish", [L("鸡肉", "main")])
    _hand_add(env, pepper)
    reading = {"kind": "change", "meal_avoid": ["猪肉"]}
    out = read(env, "今天不想吃猪肉", reading, replies=[pick(slots=[(0, chicken.id)])])
    assert f"- Slot 0: a meat dish in place of {pepper}" in picker_input(env)
    plan = out["current_plan"]
    assert ids_of_plan(plan) == [beef, cabbage, soup, chicken.id]
    assert plan["meal_avoid"] == ["猪肉"] and out["request"]["meal_avoid"] == ["猪肉"]
    assert "青椒炒肉是你手动加的，因为这顿不吃猪肉，换成了红烧鸡块。" in out["messages"][-1].content
    [frame] = change_events(env, "frame")
    assert frame["frame"]["slots"][0]["reason"] == "breaks_rule"


def test_drop_the_soup_and_add_a_dish_not_in_the_library(revise_env):
    from minemydinner.agent.revise_step import CHANGE_PARTIAL_ZH

    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    reading = {
        "kind": "change",
        "remove": [{"id": soup, "mode": "drop", "quote": "去掉汤"}],
        "add": [{"name": "鱼香肉丝"}],
    }
    out = read(env, "去掉汤，加个鱼香肉丝", reading)
    assert picker_calls(env) == []
    plan = out["current_plan"]
    # Partial: the drop is done and makes a new version; the missing dish is said.
    assert ids_of_plan(plan) == [beef, cabbage] and plan["revision"] == {"from_version": 0}
    reply = out["messages"][-1].content
    assert reply.startswith("去掉了紫菜蛋花汤。")
    assert CHANGE_PARTIAL_ZH in reply and "菜谱库里没有鱼香肉丝" in reply
    assert plan["next_actions"] == ["import_recipes"]
    assert [u["code"] for u in plan["unmet"]] == ["not_in_library"]


def test_an_unfilled_replace_keeps_the_dish(revise_env):
    env = revise_env
    first = first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    reading = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(env, "白菜换掉", reading, replies=[pick(slots=[(0, None)])])
    assert out["current_plan"] == first["current_plan"]
    assert out["plan_versions"] == first["plan_versions"]
    card = out["reply_plans"][out["messages"][-1].id]
    assert card["plan_kept"] is True and ids_of_plan(card) == [beef, cabbage, soup]
    assert "没找到合适的素菜来换清炒小白菜" in out["messages"][-1].content
    assert card["next_actions"] == ["import_recipes"]
    assert latest_record(env).final_passed is False


def test_a_change_that_leaves_no_dish_asks_the_empty_plan_question(revise_env):
    from minemydinner.agent.revise_step import EMPTY_PLAN_QUESTION_ZH

    env = revise_env
    first = first_meal(env)
    remove = [{"id": i, "mode": "drop", "quote": "都不要"} for i in dish_ids(env, *MEAL)]
    out = read(env, "都不要了", {"kind": "change", "remove": remove})
    assert out["messages"][-1].content == EMPTY_PLAN_QUESTION_ZH
    assert out["messages"][-1].id not in out["reply_plans"]
    assert out["current_plan"] == first["current_plan"] and picker_calls(env) == []


def test_an_ambiguous_reading_says_how_it_was_read(revise_env):
    from minemydinner.agent.revise_step import READING_PREFIX_ZH

    env = revise_env
    first_meal(env)
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    reading = {
        "kind": "change",
        "ambiguous": True,
        "reading": "我理解为把清炒小白菜换成别的素菜。",
        "remove": [{"id": cabbage, "mode": "replace", "quote": "菜"}],
    }
    out = read(env, "那个菜换一下", reading, replies=[pick(slots=[(0, beans)], note="很下饭。")])
    # Spec 5.6: code's change sentence, then the reading sentence, then the picker's note.
    assert out["messages"][-1].content == (
        f"把清炒小白菜换成了干煸豆角。\n\n{READING_PREFIX_ZH}我理解为把清炒小白菜换成别的素菜。"
        "\n\n很下饭。"
    )


def test_an_ambiguous_reference_that_matches_no_dish_asks_which(revise_env):
    from minemydinner.agent.revise_step import READING_PREFIX_ZH, WHICH_DISH_ZH

    env = revise_env
    first = first_meal(env)
    reading = {"kind": "change", "ambiguous": True, "reading": "方案里没有鱼。"}
    out = read(env, "鱼换掉", reading)
    assert out["messages"][-1].content == f"{READING_PREFIX_ZH}方案里没有鱼。{WHICH_DISH_ZH}"
    assert out["messages"][-1].id not in out["reply_plans"]
    assert out["current_plan"] == first["current_plan"]


def test_an_unmet_wish_is_said(revise_env):
    from minemydinner.agent.revise_step import WISH_UNMET

    env = revise_env
    first_meal(env)
    cabbage, beans = dish_ids(env, "清炒小白菜", "干煸豆角")
    reading = {"kind": "change", "wish": "海鲜", "wish_general": True}
    out = read(
        env, "想吃点海鲜", reading,
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)], wish_met=False)],
    )
    plan = out["current_plan"]
    assert beans in ids_of_plan(plan)
    reply = out["messages"][-1].content
    assert reply.startswith("把清炒小白菜换成了干煸豆角。") and "“海鲜”" in reply
    assert [u["code"] for u in plan["unmet"]] == [WISH_UNMET]
    assert "import_recipes" in plan["next_actions"]


def _kept_seafood_wish(env) -> dict:
    """A wish nothing fits: the picker answers no dish, so the plan stays (a kept card)."""
    first_meal(env)
    cabbage = dish_ids(env, "清炒小白菜")[0]
    reading = {"kind": "change", "wish": "海鲜", "wish_general": True}
    out = read(
        env, "想吃点海鲜", reading,
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, None)], wish_met=False)],
    )
    card = out["reply_plans"][out["messages"][-1].id]
    assert card["plan_kept"] is True
    return out


@pytest.mark.parametrize("key", list(ACTION_MESSAGES["zh"]))
def test_each_button_on_a_kept_plan_revises_through_the_frame(revise_env, key):
    env = revise_env
    _kept_seafood_wish(env)
    since = len(env.model.sent)
    out = press(env, key)
    assert "__interrupt__" not in out and out["button"] == key
    # No reader, route, parse or reviser call: the frame comes from the button's request.
    assert len(reader_calls(env)) == 1 and route_and_parse_calls(env, since) == []
    assert reviser_calls(env) == [] and out["intent"] == "revise"
    record = latest_record(env)
    assert record.request["button"] == key and "revision" in record.request
    [frame] = change_events(env, "frame")
    # The offering turn's unfinished wish is re-run: a general choice again.
    assert any(c["mode"] == "replace" for c in frame["frame"]["choices"])


def test_a_button_on_a_kept_plan_re_runs_the_unfinished_wish(revise_env):
    env = revise_env
    _kept_seafood_wish(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [beans] = dish_ids(env, "干煸豆角")
    out = press(
        env, "switch_to_shopping",
        replies=[pick(choices=[(0, [cabbage])], slots=[(0, beans)], wish_met=True)],
    )
    plan = out["current_plan"]
    assert ids_of_plan(plan) == [beef, beans, soup] and plan["spec"]["mode"] == "shopping"
    # The offering turn's words are the picker's wish context.
    assert "想吃点海鲜" in picker_input(env)
    assert out["request"]["query"] == "海鲜"


def test_a_button_that_lifts_the_time_limit_places_the_named_dish(revise_env):
    env = revise_env
    slow = make_recipe(
        env.session, "酱焖羊蝎子", "meat dish", [L("羊肉", "main")],
        time_min=75, time_source="stated",
    )
    limited = {"dishes": 2, "soups": 1, "mode": "shopping", "time_limit_min": 30}
    first = first_meal(env, parse=limited)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    reading = {
        "kind": "change",
        "remove": [
            {"id": beef, "mode": "replace", "quote": "土豆炖牛肉", "with_name": "酱焖羊蝎子"}
        ],
    }
    failed = read(env, "土豆炖牛肉换成酱焖羊蝎子", reading)
    assert failed["current_plan"] == first["current_plan"]
    card = failed["reply_plans"][failed["messages"][-1].id]
    assert card["plan_kept"] is True and card["next_actions"] == ["drop_time_limit"]
    assert card["passed"] is False
    reply = failed["messages"][-1].content
    assert "酱焖羊蝎子在你的菜谱库里，但需要 75 分钟" in reply and "30 分钟" in reply
    # Never "not in the library", and no import offered: the dish is there.
    assert "没有" not in reply and "导入" not in reply
    assert [(u["code"], u["recipe_id"]) for u in card["unmet"]] == [("H6", slow.id)]
    assert card["unmet"][0]["text"] == "酱焖羊蝎子：需要 75 分钟，超过 30 分钟的限制"
    out = press(env, "drop_time_limit")
    plan = out["current_plan"]
    assert ids_of_plan(plan) == [cabbage, soup, slow.id]
    assert plan["spec"]["time_limit_min"] is None
    assert out["messages"][-1].content == "把土豆炖牛肉换成了酱焖羊蝎子。"
    assert picker_calls(env) == [] and len(reader_calls(env)) == 1
    assert latest_record(env).request["button"] == "drop_time_limit"


def test_a_change_reading_keeps_or_withdraws_this_meals_avoidance(revise_env):
    env = revise_env
    first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "shopping", "meal_avoid": ["香菜"]})
    beef, cabbage, soup = dish_ids(env, *MEAL)
    # A change that restates the avoidance keeps it.
    kept = read(
        env, "汤不要了",
        {
            "kind": "change",
            "meal_avoid": ["香菜"],
            "remove": [{"id": soup, "mode": "drop", "quote": "汤"}],
        },
    )
    assert kept["request"]["meal_avoid"] == ["香菜"]
    assert kept["current_plan"]["meal_avoid"] == ["香菜"]
    # A change that drops it withdraws it: the request's list is the reading's.
    out = read(env, "香菜其实放点也行", {"kind": "change", "meal_avoid": []})
    assert out["request"]["meal_avoid"] == []
    assert out["current_plan"]["meal_avoid"] == []
    assert out["constraints"]["meal_avoid"] == []
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage]


def test_a_picker_retry_starts_from_fresh_messages_with_the_problems(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    pepper, beans = dish_ids(env, "青椒炒肉", "干煸豆角")
    reading = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(
        env, "白菜换掉", reading,
        replies=[pick(slots=[(0, pepper)]), pick(slots=[(0, beans)])],
    )
    assert ids_of_plan(out["current_plan"]) == [beef, beans, soup]
    first_try, second_try = picker_calls(env)
    assert [m.type for m in second_try] == ["system", "human", "human"]
    assert not any(m.type == "tool" for m in second_try)
    assert "Slot 0 needs a vegetable dish" in second_try[-1].content
    attempts = change_events(env, "pick_attempt")
    assert [a["attempt"] for a in attempts] == [0, 1]
    assert attempts[0]["problems"] and attempts[1]["problems"] == []
    assert latest_record(env).retries == 1


def test_a_picked_dish_that_breaks_a_rule_is_never_placed(revise_env):
    env = revise_env
    first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "shopping", "meal_avoid": ["辣椒"]})
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [beans] = dish_ids(env, "干煸豆角")
    reading = {
        "kind": "change",
        "meal_avoid": ["辣椒"],
        "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}],
    }
    # Both attempts pick 干煸豆角, which has 辣椒: the slot stays unfilled and the dish stays.
    out = read(env, "白菜换掉", reading, replies=[pick(slots=[(0, beans)])])
    assert len(picker_calls(env)) == 2
    assert "left out of this meal" in picker_calls(env)[1][-1].content
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage, soup]


def test_contradictory_stated_counts_get_the_unclear_reply(revise_env):
    from minemydinner.agent.graph import UNCLEAR_REPLY_ZH

    env = revise_env
    first = first_meal(env)
    reading = {"kind": "change", "counts": {"dishes": 2, "meat_dishes": 3}}
    out = read(env, "两个菜，三个荤菜", reading)
    assert "__interrupt__" not in out and out["messages"][-1].content == UNCLEAR_REPLY_ZH
    assert out["current_plan"] == first["current_plan"] and picker_calls(env) == []


def test_the_change_path_never_shows_a_diner_label(tmp_path, session, seeded, veg):
    from minemydinner.agent.plan_card import client_card

    env, me, wang, spinach = two_for_shopping(tmp_path, session, veg)
    reading = {
        "kind": "change",
        "ambiguous": True,
        "reading": "按 Diner 2 的口味把清炒白菜换掉。",
        "remove": [{"id": veg.id, "mode": "replace", "quote": "白菜"}],
    }
    env.model.structured["FollowUpReading"] = [reading]
    env.model.replies = [pick(slots=[(0, spinach.id)], note="Diner 2 会喜欢清炒菠菜。")]
    out = plan(env, "小王不爱吃白菜，换一个", locale="zh")
    assert "__interrupt__" not in out
    assert ids_of_plan(out["current_plan"]) == [spinach.id]
    reply = out["messages"][-1].content
    assert "Diner" not in reply and "小王" in reply
    card = out["reply_plans"][out["messages"][-1].id]
    assert "Diner " not in json.dumps(client_card(card), ensure_ascii=False)
    assert "小王" not in sent_text(env)


def _one_meat_two_vegetables(env) -> tuple[int, int, int]:
    titles = ("土豆炖牛肉", "清炒小白菜", "凉拌黄瓜")
    first_meal(env, titles, parse={"dishes": 3, "mode": "shopping"})
    return tuple(dish_ids(env, *titles))


def test_a_stated_side_count_never_shrinks_the_plan_when_its_add_fails(revise_env):
    """Fix round 1: "两个荤菜" on 1 meat / 2 vegetables keeps the total, so the frame drops a
    vegetable dish and adds a meat dish. With no meat dish found the drop is withheld: the
    plan stays and the reply says the count could not be met."""
    env = revise_env
    beef, cabbage, cucumber = _one_meat_two_vegetables(env)
    reading = {"kind": "change", "counts": {"meat_dishes": 2}}
    out = read(
        env, "要两个荤菜", reading,
        replies=[pick(choices=[(0, [cucumber])], slots=[(0, None)])],
    )
    [frame] = change_events(env, "frame")
    assert frame["frame"]["choices"][0]["mode"] == "drop"
    assert frame["frame"]["slots"][0]["for_count"] is True
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage, cucumber]
    card = out["reply_plans"][out["messages"][-1].id]
    assert card["plan_kept"] is True
    assert "没能按你说的数量改，原来的菜都留着" in out["messages"][-1].content
    [outcome] = change_events(env, "change_outcome")
    assert outcome["recipe_ids"] == [beef, cabbage, cucumber]


def test_a_stated_side_count_never_shrinks_the_plan_without_a_picker_answer(revise_env):
    env = revise_env
    beef, cabbage, cucumber = _one_meat_two_vegetables(env)
    reading = {"kind": "change", "counts": {"meat_dishes": 2}}
    # The picker only searches and never answers: code's fallback must not drop a dish.
    out = read(
        env, "要两个荤菜", reading, replies=[tool_call("search_recipes", {"query": "荤菜"})]
    )
    assert len(change_events(env, "pick_attempt")) == 2
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage, cucumber]
    assert "没能按你说的数量改" in out["messages"][-1].content


def test_a_dropped_dish_the_user_asked_for_still_goes_when_an_add_fails(revise_env):
    env = revise_env
    first_meal(env)
    beef, cabbage, soup = dish_ids(env, *MEAL)
    reading = {
        "kind": "change",
        "remove": [{"id": soup, "mode": "drop", "quote": "汤"}],
        "add": [{"dish_type": "vegetable dish", "count": 1}],
    }
    out = read(env, "汤不要了，加个素菜", reading, replies=[pick(slots=[(0, None)])])
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage]
    assert "没找到可以加的素菜。" in out["messages"][-1].content


def test_an_earlier_wish_is_never_reported_unmet_on_a_named_change(revise_env):
    """Fix round 1: the request still carries an earlier turn's wish; a named replace whose
    picker says wish_met false must not say that old wish was not met."""
    env = revise_env
    first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "shopping", "query": "辣的"})
    beef, cabbage, soup = dish_ids(env, *MEAL)
    [beans] = dish_ids(env, "干煸豆角")
    reading = {"kind": "change", "remove": [{"id": cabbage, "mode": "replace", "quote": "白菜"}]}
    out = read(env, "白菜换掉", reading, replies=[pick(slots=[(0, beans)], wish_met=False)])
    assert ids_of_plan(out["current_plan"]) == [beef, beans, soup]
    assert "辣的" not in out["messages"][-1].content
    assert out["current_plan"]["unmet"] == []


def test_a_new_kept_card_with_nothing_unfinished_never_re_runs_an_old_wish(revise_env):
    """Fix round 1: only a kept card saved before cards kept their unfinished parts falls
    back to its request's wish; a new one with nothing unfinished re-runs nothing."""
    env = revise_env
    first = first_meal(env, parse={"dishes": 2, "soups": 1, "mode": "shopping", "query": "辣的"})
    out = read(env, "嗯，就这样吧", {"kind": "change"})
    card = out["reply_plans"][out["messages"][-1].id]
    assert card["plan_kept"] is True and card["unfinished"]["reading"] is None
    assert out["current_plan"] == first["current_plan"]
    out = press(env, "switch_to_shopping")
    [frame] = change_events(env, "frame")
    assert frame["frame"]["choices"] == [] and frame["frame"]["slots"] == []
    assert picker_calls(env) == []


def test_a_rule_breaking_dish_a_count_absorbed_stays_removed(revise_env):
    """Fix round 2: the stated counts absorb the replace slot of a dish that breaks the new
    one-meal avoidance, and the count's add finds nothing. The count's drops are withheld,
    but the rule-breaking dish never comes back (spec 5.5); the reply names it."""
    env = revise_env
    titles = ("土豆炖牛肉", "青椒炒肉", "清炒小白菜")
    first_meal(env, titles, parse={"dishes": 3, "mode": "shopping"})
    beef, pepper, cabbage = dish_ids(env, *titles)
    reading = {
        "kind": "change",
        "meal_avoid": ["猪肉"],
        "counts": {"meat_dishes": 1, "vegetable_dishes": 2},
    }
    out = read(env, "今天不吃猪肉，一荤两素", reading, replies=[pick(slots=[(0, None)])])
    [frame] = change_events(env, "frame")
    assert pepper in frame["frame"]["drops"] and pepper not in frame["frame"]["count_drops"]
    assert [s["for_count"] for s in frame["frame"]["slots"]] == [True]
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage]
    reply = out["messages"][-1].content
    assert "青椒炒肉去掉了：这顿不吃猪肉，也没找到可以替换的菜。" in reply
    assert "没能按你说的数量改" in reply
    assert "去掉了青椒炒肉。" not in reply


def test_a_withheld_count_drop_drops_the_pickers_note(revise_env):
    env = revise_env
    beef, cabbage, cucumber = _one_meat_two_vegetables(env)
    out = read(
        env, "要两个荤菜", {"kind": "change", "counts": {"meat_dishes": 2}},
        replies=[pick(choices=[(0, [cucumber])], slots=[(0, None)], note="去掉了凉拌黄瓜。")],
    )
    assert ids_of_plan(out["current_plan"]) == [beef, cabbage, cucumber]
    assert "去掉了凉拌黄瓜" not in out["messages"][-1].content
