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
    build_graph,
    next_actions,
    reason_text,
)
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import PROMPT_VERSION, get_request_style, set_request_style
from minemydinner.planning.feasibility import Shortfall
from minemydinner.models import AppPreference, LlmCall, PlanningRun
from minemydinner.agent.planner import TOOLS_VERSION
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
    model = ScriptedChatModel(structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]})
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
    from minemydinner.agent.graph import _unmet_for_user, _violation_for_model

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
    assert _unmet_for_user(h4, {}, "zh") == (
        "方案有 2 道菜，1 道汤，0 道荤菜，2 道素菜，要求是 3 道菜，0 道汤，至少 2 道荤菜，最多 1 道素菜"
    )
    h7 = {
        "code": "H7",
        "recipe_id": None,
        "detail": {"name": "鸡蛋", "rule": "all", "recipes_without": [7]},
    }
    assert _unmet_for_user(h7, {7: "白菜"}, "en") == 'These dishes do not use 鸡蛋: "白菜"'
    assert _unmet_for_user(h7, {7: "白菜"}, "zh") == "这些菜没有用到鸡蛋：“白菜”"
    must = {**h7, "detail": {**h7["detail"], "rule": "must"}}
    assert _unmet_for_user(must, {}, "en") == "No dish in the plan uses 鸡蛋"
    assert "7" in _violation_for_model(h7, {}) and "鸡蛋" in _violation_for_model(must, {})


def test_counts_text_uses_singular_and_omits_unasked_zeros():
    from minemydinner.agent.graph import _unmet_for_user

    h4 = {
        "code": "H4",
        "recipe_id": None,
        "detail": {
            "required": {"dishes": 1, "soups": 1, "staples": 0, "desserts": 0, "snacks": 0},
            "present": {"dishes": 2, "soups": 0, "staples": 0, "desserts": 0, "snacks": 0},
            "minimum": False,
        },
    }
    assert _unmet_for_user(h4, {}, "en") == "The plan has 2 dishes, 0 soups; 1 dish, 1 soup were asked for"
    assert _unmet_for_user(h4, {}, "zh") == "方案有 2 道菜，0 道汤，要求是 1 道菜，1 道汤"


# ---- the planner's opening, retries, and the shortlist (Task 7) ----


def make_graph_env(tmp_path, session, model=None, embeddings=None, **deps_kwargs):
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    model = model or ScriptedChatModel(
        structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]}
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
    from minemydinner.agent.graph import _unmet_for_user, _violation_for_model

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
    assert "6 道素菜" in _unmet_for_user(h4, {}, "zh")
    assert "要求是 8 道菜，2 道荤菜，6 道素菜" in _unmet_for_user(h4, {}, "zh")
    assert "8 dishes, 2 meat dishes, 6 vegetable dishes were asked for" in _unmet_for_user(
        h4, {}, "en"
    )


def test_exact_vegetable_only_split_implies_the_meat_side():
    from minemydinner.agent.graph import _violation_for_model

    h4 = _h4({"dishes": 5, "vegetable_dishes": 2}, {"dishes": 5, "meat_dishes": 5})
    assert "the user wants 5 dishes, 3 meat dishes, 2 vegetable dishes." in _violation_for_model(
        h4, {}
    )


def test_old_minimum_finding_states_the_tightened_ranges():
    from minemydinner.agent.graph import _unmet_for_user, _violation_for_model

    h4 = _h4(
        {"dishes": 3, "meat_dishes": 2},
        {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 2},
        minimum=True,
    )
    text = _violation_for_model(h4, {})
    assert text.endswith(
        "the user wants 3 dishes, at least 2 meat dishes, at most 1 vegetable dish."
    )
    zh = _unmet_for_user(h4, {}, "zh")
    assert zh.endswith("要求是 3 道菜，至少 2 道荤菜，最多 1 道素菜")


def test_an_unconstrained_side_is_never_printed():
    from minemydinner.agent.graph import _unmet_for_user, _violation_for_model

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
    assert _unmet_for_user(plain, {}, "en") == (
        "The plan has 1 dish, 1 meat dish; 2 dishes were asked for"
    )
    assert "0" not in _unmet_for_user(plain, {}, "zh")


def test_range_wording_in_retry_and_card():
    from minemydinner.agent.graph import _unmet_for_user, _violation_for_model

    # 4 dishes, at least 1 meat, at most 2 vegetable: meat [2, 4], vegetable [0, 2].
    h4 = _h4_ranges(4, {"dishes": 4, "meat_dishes": 1, "vegetable_dishes": 3}, [2, 4], [0, 2])
    assert _violation_for_model(h4, {}) == (
        "The plan has 4 dishes, 1 meat dish, 3 vegetable dishes; the user wants 4 dishes, "
        "at least 2 meat dishes, at most 2 vegetable dishes."
    )
    assert _unmet_for_user(h4, {}, "zh") == (
        "方案有 4 道菜，1 道荤菜，3 道素菜，要求是 4 道菜，至少 2 道荤菜，最多 2 道素菜"
    )
    # Both named below a total of 4: 1 to 3 of each.
    both = _h4_ranges(4, {"dishes": 4, "meat_dishes": 0, "vegetable_dishes": 4}, [1, 3], [1, 3])
    assert _unmet_for_user(both, {}, "en").endswith(
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
    from minemydinner.agent.graph import side_phrase

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
