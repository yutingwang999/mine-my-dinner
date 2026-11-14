from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import delete, select

from minemydinner.agent.graph import GraphDeps
from minemydinner.agent.planner import TOOLS_VERSION, PlanProposal, build_planner
from minemydinner.agent.records import PlanningRecorder, load_run
from minemydinner.agent.request import PROMPT_VERSION
from minemydinner.llm.client import LLMClient
from minemydinner.models import LlmCall
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.tools import PlannerDeps
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import L, make_recipe, rollback_session_factory
from tests.recipefakes import seeded  # noqa: F401


@dataclass
class PlannerEnv:
    planner: Any
    client: LLMClient
    recorder: PlanningRecorder
    factory: Any
    primary: ScriptedChatModel
    fallback: ScriptedChatModel


def make_env(tmp_path, session, primary=None, fallback=None, **hooks) -> PlannerEnv:
    # Kept rows in the test database are hidden by this rolled-back transaction.
    session.execute(delete(LlmCall))
    primary = primary or ScriptedChatModel()
    fallback = fallback or ScriptedChatModel()
    factory = rollback_session_factory(session)
    client = make_llm_client(tmp_path, factory, primary, fallback, **hooks)
    recorder = PlanningRecorder(factory)
    planner = build_planner(GraphDeps(factory, client, recorder))
    return PlannerEnv(planner, client, recorder, factory, primary, fallback)


def run_planner(env: PlannerEnv, script=None, text="Plan dinner for me.") -> tuple[dict, int]:
    model = env.primary if env.primary.error is None else env.fallback
    if script is not None:
        model.replies = list(script)
    run_id = env.recorder.start("t", {}, [], PROMPT_VERSION, TOOLS_VERSION, False)
    context = PlannerDeps(env.factory, env.client, MealConstraints(diner_ids=()), "fridge_only")
    out = env.planner.invoke({"messages": [HumanMessage(text)], "run_id": run_id}, context=context)
    return out, run_id


def calls(session) -> list[LlmCall]:
    return list(session.scalars(select(LlmCall).order_by(LlmCall.id)))


def events(session, run_id: int) -> list[dict]:
    session.expire_all()
    return load_run(session, run_id).events


def to_diner_2(text: str) -> str:
    return text.replace("小李", "Diner 2")


def from_diner_2(text: str) -> str:
    return text.replace("Diner 2", "小李")


@pytest.fixture
def planner_env(tmp_path, session, seeded):
    return make_env(tmp_path, session)


@pytest.fixture
def planner_env_primary_fails(tmp_path, session, seeded):
    env = make_env(tmp_path, session)
    env.primary.error = RuntimeError("provider down")
    env.client.config.models["openai"].tool_call_options = {"reasoning_effort": "none"}
    return env


@pytest.fixture
def planner_env_with_hook(tmp_path, session, seeded):
    return make_env(tmp_path, session, pre_hooks=[to_diner_2], post_hooks=[from_diner_2])


@pytest.fixture
def planner_env_endless_tools(planner_env):
    planner_env.primary.replies = [tool_call("get_inventory", {})]
    return planner_env


@pytest.fixture
def r1(session, seeded):
    return make_recipe(session, "清炒白菜", lines=[L("小白菜")])


def test_planner_calls_go_through_llm_calls(planner_env, session, r1):
    out, run_id = run_planner(
        planner_env, script=[tool_call("search_recipes", {}), final_proposal([r1.id])]
    )
    assert [c.task for c in calls(session)] == ["planning", "planning"]
    assert all(c.success and not c.fallback_used for c in calls(session))
    expected = PlanProposal(recipe_ids=[r1.id], note="Here is your dinner.")
    assert out["structured_response"] == expected
    kinds = [e["kind"] for e in events(session, run_id)]
    assert kinds == ["model_call", "tool_call", "model_call"]
    tool_event = events(session, run_id)[1]
    assert tool_event["name"] == "search_recipes" and "hits" in tool_event["result"]


def test_model_call_event_names_the_model(planner_env, session, r1):
    _, run_id = run_planner(planner_env, script=[final_proposal([r1.id])])
    (event,) = events(session, run_id)
    assert event["model_name"] == "deepseek:deepseek-test" and event["provider"] == "agent_primary"
    assert event["fallback_used"] is False
    assert event["messages"][0]["type"] == "system"
    assert event["response"]["tool_calls"][0]["name"] == "PlanProposal"


def test_primary_failure_fails_over_inside_the_loop(planner_env_primary_fails, session, r1):
    env = planner_env_primary_fails
    out, run_id = run_planner(env, script=[tool_call("get_inventory", {}), final_proposal([r1.id])])
    assert out["structured_response"].recipe_ids == [r1.id]
    rows = calls(session)
    assert [(c.provider, c.success, c.fallback_used) for c in rows] == [
        ("agent_primary", False, False),
        ("agent_fallback", True, True),
        ("agent_primary", False, False),
        ("agent_fallback", True, True),
    ]
    # The fallback's tool-call options reach the bound model.
    assert all(kwargs.get("reasoning_effort") == "none" for kwargs in env.fallback.bound)
    model_events = [e for e in events(session, run_id) if e["kind"] == "model_call"]
    assert [e["fallback_used"] for e in model_events] == [True, True]


def test_record_holds_messages_as_sent_after_pre_hooks(planner_env_with_hook, session, r1):
    env = planner_env_with_hook
    _, run_id = run_planner(env, script=[final_proposal([r1.id])], text="小李 is coming too")
    (event,) = events(session, run_id)
    sent = [m["content"] for m in event["messages"]]
    assert "Diner 2 is coming too" in sent and not any("小李" in text for text in sent)
    # The model itself received the replaced text.
    assert env.primary.sent[0][-1].content == "Diner 2 is coming too"


def test_response_is_restored_before_state(planner_env_with_hook, session, r1):
    env = planner_env_with_hook
    script = [
        tool_call("get_inventory", {}, content="Checking for Diner 2"),
        final_proposal([r1.id], note="Diner 2 will like it"),
    ]
    out, run_id = run_planner(env, script=script, text="小李 is coming too")
    ai_texts = [m.content for m in out["messages"] if isinstance(m, AIMessage)]
    assert "Checking for 小李" in ai_texts
    assert out["structured_response"].note == "小李 will like it"
    final = [m for m in out["messages"] if isinstance(m, AIMessage)][-1]
    assert final.tool_calls[0]["args"]["note"] == "小李 will like it"
    # The record keeps the replaced form of the response.
    responses = [e["response"] for e in events(session, run_id) if e["kind"] == "model_call"]
    assert responses[0]["content"] == "Checking for Diner 2"
    assert responses[1]["tool_calls"][0]["args"]["note"] == "Diner 2 will like it"


def test_invalid_tool_arguments_are_recorded(planner_env, session, r1):
    out, run_id = run_planner(
        planner_env,
        script=[tool_call("compute_missing", {"recipe_ids": "x"}), final_proposal([r1.id])],
    )
    errors = [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(errors) == 1 and errors[0].name == "compute_missing"
    # The loop continued and the model saw the error.
    assert out["structured_response"].recipe_ids == [r1.id]
    assert any(
        isinstance(m, ToolMessage) and m.status == "error" for m in planner_env.primary.sent[1]
    )
    invalid = [e for e in events(session, run_id) if e["kind"] == "invalid_tool_call"]
    assert len(invalid) == 1
    assert invalid[0]["name"] == "compute_missing" and invalid[0]["args"] == {"recipe_ids": "x"}
    assert invalid[0]["error"]


def test_runaway_loop_is_cut_off(planner_env_endless_tools, session):
    out, run_id = run_planner(planner_env_endless_tools)
    assert len(planner_env_endless_tools.primary.sent) == 12
    assert out.get("structured_response") is None
    assert "limit" in out["messages"][-1].content
    kinds = [e["kind"] for e in events(session, run_id)]
    assert kinds.count("model_call") == 12


def test_tool_error_payloads_are_recorded_as_invalid(planner_env, session, r1, monkeypatch):
    from minemydinner.planning import tools

    def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(tools, "inventory_snapshot", broken)
    script = [
        tool_call("get_inventory", {}),
        tool_call("get_recipe", {"recipe_id": 999999}),
        final_proposal([r1.id]),
    ]
    out, run_id = run_planner(planner_env, script=script)
    assert out["structured_response"].recipe_ids == [r1.id]
    invalid = [e for e in events(session, run_id) if e["kind"] == "invalid_tool_call"]
    assert [(e["name"], e["error"]) for e in invalid] == [
        ("get_inventory", "tool_failed"),
        ("get_recipe", "not_found"),
    ]
    assert not [e for e in events(session, run_id) if e["kind"] == "tool_call"]
