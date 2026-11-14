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
    NO_PLAN_NOTE_EN,
    NO_PLAN_NOTE_ZH,
    OTHER_REPLY_EN,
    OTHER_REPLY_ZH,
    GraphDeps,
    _next_actions,
    build_graph,
)
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import PROMPT_VERSION, get_request_style, set_request_style
from minemydinner.models import AppPreference, LlmCall, PlanningRun
from minemydinner.agent.planner import TOOLS_VERSION
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client
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
    expected = {"kind": "question", "fields": ["composition"], "problem": "split_exceeds_total"}
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
    assert out["__interrupt__"][0].value["problem"] == "split_exceeds_total"
    fixed = {"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}
    out = resume(graph_env, "两个菜，一荤一素", parse=fixed)
    assert [i.value for i in out["__interrupt__"]] == [{"kind": "question", "fields": ["mode"]}]


def test_contradiction_with_an_empty_answer_does_not_loop(graph_env):
    out = plan(graph_env, "thirteen dishes", parse={"dishes": 13})
    assert out["__interrupt__"][0].value["problem"] == "too_many"
    out = resume(graph_env, "")
    assert "__interrupt__" not in out
    assert out["messages"][-1].type == "ai"


def test_next_actions_count_every_category():
    spec = {
        "mode": "shopping", "dishes": 0, "soups": 0, "staples": 1, "desserts": 1,
        "snacks": 0, "time_limit_min": None,
    }
    assert _next_actions(spec) == ["fewer_dishes"]
    assert _next_actions({**spec, "desserts": 0}) == []


def test_failed_validation_retries_with_errors(graph_env, veg, meat):
    replies = [final_proposal([veg.id]), final_proposal([veg.id, meat.id])]
    out = plan(graph_env, "two dishes please", parse={"dishes": 2}, replies=replies)
    assert out["retries"] == 1 and out["current_plan"]["passed"]
    record = run_record(graph_env)
    assert [e["kind"] for e in record.events] == [
        "model_call",
        "validation",
        "model_call",
        "validation",
    ]
    assert "H4" in record.events[1]["result"]["violations"][0]["code"]
    assert record.retries == 1 and record.first_passed is False and record.final_passed is True
    retry_request = planner_requests(graph_env)[1]
    assert "wants 2 dishes and 0 soups" in retry_request and str(veg.id) in retry_request


def test_h2_failure_after_retries_is_never_shown(graph_env, session):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    stock(session, "鸡蛋", count=6)
    egg = make_recipe(session, "番茄炒蛋", "meat dish", [L("鸡蛋")])
    out = plan(graph_env, "dinner", replies=[final_proposal([egg.id])])
    current = out["current_plan"]
    assert current["recipes"] == [] and current["passed"] is False
    assert out["retries"] == MAX_RETRIES
    assert [u["code"] for u in current["unmet"]] == ["H2"]
    assert current["next_actions"] == ["switch_to_shopping"]
    assert out["messages"][-1].content == NO_PLAN_NOTE_EN
    assert "Diner 1: allergy egg" in planner_requests(graph_env)[0]
    assert run_record(graph_env).final_passed is False


def test_no_plan_note_follows_locale(graph_env, veg):
    out = plan(graph_env, "晚饭", replies=[final_proposal([999999])], locale="zh")
    assert out["messages"][-1].content == NO_PLAN_NOTE_ZH
    assert out["current_plan"]["unmet"][0]["text"] == "提议的菜并不都来自你的菜谱库"


def test_unknown_recipe_ids_are_never_shown(graph_env, veg):
    out = plan(graph_env, "dinner", replies=[final_proposal([999999])])
    assert out["current_plan"]["recipes"] == []
    assert [u["code"] for u in out["current_plan"]["unmet"]] == ["H5"]


def test_fridge_only_shortfall_shows_closest_with_unmet(graph_env, session):
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    out = plan(graph_env, "dinner", replies=[final_proposal([pork.id])])
    current = out["current_plan"]
    assert current["passed"] is False and [r["id"] for r in current["recipes"]] == [pork.id]
    assert current["missing"] == {str(pork.id): ["五花肉"]}
    (unmet,) = current["unmet"]
    assert unmet["code"] == "H1" and unmet["recipe_id"] == pork.id and "五花肉" in unmet["text"]
    assert current["next_actions"] == ["switch_to_shopping"]
    # The fixed closest note leads; the model's note follows and is never shown alone.
    assert out["messages"][-1].content == CLOSEST_NOTE_EN + "\n\n" + "Here is your dinner."


def test_closest_note_is_fixed_text_in_the_locale_even_without_a_model_note(graph_env, session):
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


def test_closest_attempt_without_h2_is_shown_when_the_last_breaks_h2(graph_env, session):
    rule(session, self_diner(session).id, "allergy", allergen="egg")
    stock(session, "鸡蛋", count=6)
    egg = make_recipe(session, "番茄炒蛋", "meat dish", [L("鸡蛋")])
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    out = plan(graph_env, "dinner", replies=[final_proposal([pork.id]), final_proposal([egg.id])])
    current = out["current_plan"]
    assert out["retries"] == MAX_RETRIES and out["validation"]["violations"][0]["code"] == "H2"
    assert [r["id"] for r in current["recipes"]] == [pork.id] and current["passed"] is False
    assert [u["code"] for u in current["unmet"]] == ["H1"]
    assert current["next_actions"] == ["switch_to_shopping"]


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
            "ParsedRequest": [{"query": "something Diner 2 likes", "must_use": ["Diner 2"]}],
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
    assert record.request["parsed"]["must_use"] == ["Diner 2"]
    assert record.request["messages"] == ["dinner Diner 2 likes"]
    assert record.final_plan["recipes"][0]["title"] == "Diner 2的白菜"
    assert "小李" not in json.dumps(record.request, ensure_ascii=False)
    assert "小李" not in json.dumps(record.final_plan, ensure_ascii=False)
    assert "小李" not in json.dumps(record.events, ensure_ascii=False)
