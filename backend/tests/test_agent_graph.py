import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.agent.graph import (
    OTHER_REPLY_EN,
    OTHER_REPLY_ZH,
    PLAN_PLACEHOLDER,
    GraphDeps,
    build_graph,
)
from minemydinner.agent.records import PlanningRecorder
from tests.planningfakes import FakePlanningLLM, rollback_session_factory


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


def test_plan_intent_reaches_the_placeholder_and_task_is_configurable(session):
    factory = rollback_session_factory(session)
    llm = FakePlanningLLM("plan")
    deps = GraphDeps(factory, llm, PlanningRecorder(factory), task="planning_eval")
    graph = build_graph(deps, InMemorySaver())
    out = graph.invoke({"messages": [HumanMessage("dinner?")], "locale": "en"}, thread("t4"))
    assert out["messages"][-1].content == PLAN_PLACEHOLDER
    assert llm.calls[0][0] == "planning_eval"


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
