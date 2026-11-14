from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import delete, select

from minemydinner.agent.graph import GraphDeps
from minemydinner.agent.planner import (
    MODEL_CALL_LIMIT,
    PLANNER_INPUT_TOKEN_BUDGET,
    TOOL_CALL_LIMIT,
    TOOLS_VERSION,
    PlanProposal,
    build_planner,
)
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
    assert len(planner_env_endless_tools.primary.sent) == MODEL_CALL_LIMIT
    assert out.get("structured_response") is None
    assert "limit" in out["messages"][-1].content
    kinds = [e["kind"] for e in events(session, run_id)]
    assert kinds.count("model_call") == MODEL_CALL_LIMIT


def test_planner_limits_and_middleware_order(tmp_path, session, seeded, monkeypatch):
    from minemydinner.agent import planner as planner_module

    assert (MODEL_CALL_LIMIT, TOOL_CALL_LIMIT, PLANNER_INPUT_TOKEN_BUDGET) == (6, 10, 32000)
    seen = {}
    real = planner_module.create_agent

    def capture(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(planner_module, "create_agent", capture)
    make_env(tmp_path, session)
    names = [type(m).__name__ for m in seen["middleware"]]
    assert names == [
        "CallLayerMiddleware",
        "TokenBudgetMiddleware",
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
    ]
    assert seen["middleware"][1].budget == PLANNER_INPUT_TOKEN_BUDGET


def big(input_tokens: int) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": 5, "total_tokens": input_tokens + 5}


@pytest.fixture
def planner_env_big_inputs(tmp_path, session, seeded):
    primary = ScriptedChatModel(usage=big(PLANNER_INPUT_TOKEN_BUDGET))
    return make_env(tmp_path, session, primary=primary)


def test_budget_ends_the_attempt_without_rewriting_history(planner_env_big_inputs, session):
    env = planner_env_big_inputs
    first = tool_call("get_inventory", {})
    first.usage_metadata = big(1000)
    script = [first, tool_call("get_inventory", {}), tool_call("get_inventory", {})]
    out, run_id = run_planner(env, script=script, text="Plan dinner for me.")
    # The second call reached the budget and asked for a tool: no third call, no tool run.
    assert len(env.primary.sent) == 2
    assert out.get("structured_response") is None
    assert [m.type for m in out["messages"]] == ["human", "ai", "tool", "ai"]
    assert out["messages"][-1].tool_calls and out["messages"][-1].content == ""
    # Earlier messages are exactly what the second call was sent: nothing trimmed or rewritten.
    second = env.primary.sent[1]
    assert second[0].type == "system"
    assert [(m.type, m.content) for m in second[1:]] == [
        (m.type, m.content) for m in out["messages"][:3]
    ]
    assert out["messages"][0].content == "Plan dinner for me."
    recorded = events(session, run_id)
    assert [e["kind"] for e in recorded] == ["model_call", "tool_call", "model_call", "budget_stop"]
    # The stop is in the planning record only, never in the message history.
    assert recorded[-1] == {"kind": "budget_stop", "input_tokens": PLANNER_INPUT_TOKEN_BUDGET}
    assert not any("budget" in str(m.content) for m in out["messages"])


def test_no_budget_stop_event_below_the_budget(tmp_path, session, seeded, r1):
    primary = ScriptedChatModel(usage=big(PLANNER_INPUT_TOKEN_BUDGET - 1))
    env = make_env(tmp_path, session, primary=primary)
    _, run_id = run_planner(env, script=[tool_call("get_inventory", {}), final_proposal([r1.id])])
    assert "budget_stop" not in [e["kind"] for e in events(session, run_id)]


def test_budget_below_the_limit_lets_the_run_continue(tmp_path, session, seeded, r1):
    primary = ScriptedChatModel(usage=big(PLANNER_INPUT_TOKEN_BUDGET - 1))
    env = make_env(tmp_path, session, primary=primary)
    out, _ = run_planner(env, script=[tool_call("get_inventory", {}), final_proposal([r1.id])])
    assert len(env.primary.sent) == 2 and out["structured_response"].recipe_ids == [r1.id]


def test_final_answer_at_the_budget_is_kept(planner_env_big_inputs, r1):
    out, _ = run_planner(planner_env_big_inputs, script=[final_proposal([r1.id])])
    assert out["structured_response"].recipe_ids == [r1.id]


def test_budget_middleware_writes_nothing_but_the_jump():
    from minemydinner.agent.budget import TokenBudgetMiddleware

    middleware = TokenBudgetMiddleware(100)
    ask = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1"}])
    over = ask.model_copy(update={"usage_metadata": big(100)})
    under = ask.model_copy(update={"usage_metadata": big(99)})
    answer = AIMessage(content="done", usage_metadata=big(500))
    assert middleware.after_model({"messages": [HumanMessage("x"), over]}, None) == {
        "jump_to": "end"
    }
    assert middleware.after_model({"messages": [HumanMessage("x"), under]}, None) is None
    assert middleware.after_model({"messages": [HumanMessage("x"), answer]}, None) is None
    # No usage reported: nothing to compare, the run goes on.
    assert middleware.after_model({"messages": [HumanMessage("x"), ask]}, None) is None


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
