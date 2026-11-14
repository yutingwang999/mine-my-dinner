import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from minemydinner.agent.graph import OTHER_REPLY_EN, GraphDeps, build_graph
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import get_request_style, set_request_style
from langchain_core.messages import HumanMessage

from minemydinner.agent.checkpoint import open_checkpointer
from minemydinner.api import chat as chat_module
from minemydinner.api.chat import ChatRuntime, get_chat_runtime
from minemydinner.llm.client import AllModelsFailed
from minemydinner.main import app
from minemydinner.models import AppPreference, PlanningRun
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import L, make_recipe, rollback_session_factory, stock
from tests.recipefakes import seeded  # noqa: F401


def sse(response) -> list[tuple[str, Any]]:
    """Parse a text/event-stream body into (event name, data) pairs."""
    assert response.status_code == 200, response.text
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.split("\n")
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


def names(events) -> list[str]:
    return [name for name, _ in events]


def of(events, name: str) -> list[Any]:
    return [data for event, data in events if event == name]


@dataclass
class ChatEnv:
    model: ScriptedChatModel
    session: Any


def install(tmp_path, session, model, style: str | None, saver=None) -> ChatEnv:
    session.execute(delete(AppPreference))
    session.flush()
    if style is not None:
        set_request_style(session, style)
    factory = rollback_session_factory(session)
    graph = build_graph(
        GraphDeps(factory, make_llm_client(tmp_path, factory, model), PlanningRecorder(factory)),
        saver or InMemorySaver(),
    )
    runtime = ChatRuntime(graph, PlanningRecorder(factory))
    app.dependency_overrides[get_chat_runtime] = lambda: runtime
    return ChatEnv(model, session)


@pytest.fixture
def recipe(session, seeded):
    stock(session, "小白菜", level="plenty")
    return make_recipe(session, "清炒白菜", "vegetable dish", [L("小白菜")], servings=2)


def plan_model(recipe) -> ScriptedChatModel:
    return ScriptedChatModel(
        replies=[tool_call("get_recipe", {"recipe_id": recipe.id}), final_proposal([recipe.id])],
        structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]},
    )


@pytest.fixture
def chat_env(client, tmp_path, session, recipe):
    return install(tmp_path, session, plan_model(recipe), "plan_then_adjust")


@pytest.fixture
def chat_env_first_use(client, tmp_path, session, recipe):
    return install(tmp_path, session, plan_model(recipe), None)


def new_thread(client) -> str:
    r = client.post("/api/chat/threads")
    assert r.status_code == 200
    return r.json()["thread_id"]


def post_message(client, tid, text="今晚吃什么", locale="zh"):
    return client.post(f"/api/chat/threads/{tid}/messages", json={"text": text, "locale": locale})


def post_resume(client, tid, value, locale="en"):
    return client.post(
        f"/api/chat/threads/{tid}/resume", json={"value": value, "locale": locale}
    )


def test_create_thread_returns_uuid_and_empty_history(client, chat_env):
    tid = new_thread(client)
    assert str(uuid.UUID(tid)) == tid
    r = client.get(f"/api/chat/threads/{tid}")
    assert r.status_code == 200
    assert r.json() == {"messages": [], "current_plan": None, "pending": None}


def test_message_streams_progress_plan_and_done(client, chat_env):
    tid = new_thread(client)
    r = post_message(client, tid)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache, no-transform"
    assert r.headers["x-accel-buffering"] == "no"
    events = sse(r)
    assert names(events)[-1] == "done" and "plan" in names(events)
    steps = [e["step"] for e in of(events, "progress")]
    assert steps[0] == "parse" and steps[-1] == "present"
    assert {"context", "search", "check"} <= set(steps)
    plan = of(events, "plan")[0]
    assert plan["passed"] is True and "semantic_paused" in plan and "next_actions" in plan
    message = of(events, "message")[0]
    assert message["role"] == "assistant" and message["text"] == "Here is your dinner."
    # Planner internals are never sent.
    assert "token" not in names(events)
    assert "PlanProposal" not in json.dumps(events)


def test_streaming_events_arrive_through_the_origin_middleware(client, chat_env):
    tid = new_thread(client)
    seen = []
    with client.stream(
        "POST",
        f"/api/chat/threads/{tid}/messages",
        json={"text": "hi", "locale": "en"},
        headers={"Origin": "http://localhost:3000"},
    ) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("event: "):
                seen.append(line[7:])
    assert seen[0] == "progress" and seen[-1] == "done"


def test_other_intent_replies_in_the_ui_locale(client, tmp_path, session, recipe):
    model = ScriptedChatModel(structured={"Intent": [{"intent": "other"}]})
    install(tmp_path, session, model, "plan_then_adjust")
    tid = new_thread(client)
    events = sse(post_message(client, tid, "hello", "en"))
    assert of(events, "message")[0]["text"] == OTHER_REPLY_EN
    assert "plan" not in names(events)
    history = client.get(f"/api/chat/threads/{tid}").json()
    assert history["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": OTHER_REPLY_EN},
    ]


def test_interrupt_then_resume(client, chat_env_first_use):
    tid = new_thread(client)
    events = sse(post_message(client, tid))
    assert of(events, "interrupt")[0] == {"kind": "request_style"}
    assert "plan" not in names(events) and names(events)[-1] == "done"
    events = sse(post_resume(client, tid, "plan_then_adjust"))
    assert "plan" in names(events) and "interrupt" not in names(events)


def test_resume_with_null_skips_the_style_question(client, chat_env_first_use, session):
    tid = new_thread(client)
    sse(post_message(client, tid))
    events = sse(post_resume(client, tid, None))
    assert "plan" in names(events) and "interrupt" not in names(events)
    session.expire_all()
    assert get_request_style(session) == "plan_then_adjust"


def test_resume_value_is_bounded(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid))
    assert post_resume(client, tid, "x" * 4001).status_code == 422
    assert post_resume(client, tid, "x" * 4000).status_code == 200


def test_resume_without_pending_question_is_409(client, chat_env):
    tid = new_thread(client)
    r = post_resume(client, tid, "x")
    assert r.status_code == 409 and r.json()["code"] == "nothing_pending"
    sse(post_message(client, tid))
    r = post_resume(client, tid, "x")
    assert r.status_code == 409 and r.json()["code"] == "nothing_pending"


def test_reload_returns_pending_question_and_plan(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid, "dinner please", "en"))
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert body["pending"] == {"kind": "request_style"}
    assert body["current_plan"] is None
    assert body["messages"] == [{"role": "user", "text": "dinner please"}]
    sse(post_resume(client, tid, "ask_first"))
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert body["pending"]["kind"] == "question" and body["current_plan"] is None
    sse(post_resume(client, tid, "two dishes"))
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert body["pending"] is None and body["current_plan"]["passed"] is True
    assert [m["role"] for m in body["messages"]] == ["user", "user", "assistant"]


def test_unknown_thread_is_404(client, chat_env):
    for tid in (str(uuid.uuid4()), "not-a-uuid"):
        for r in (
            client.get(f"/api/chat/threads/{tid}"),
            post_message(client, tid),
            post_resume(client, tid, "x"),
        ):
            assert r.status_code == 404 and r.json()["code"] == "thread_not_found"


def test_message_validation(client, chat_env):
    tid = new_thread(client)
    url = f"/api/chat/threads/{tid}/messages"
    assert client.post(url, json={"text": "", "locale": "en"}).status_code == 422
    assert client.post(url, json={"text": "x" * 4001, "locale": "en"}).status_code == 422
    assert client.post(url, json={"text": "x", "locale": "fr"}).status_code == 422


def test_cross_site_message_is_rejected(client, chat_env):
    tid = new_thread(client)
    r = client.post(
        f"/api/chat/threads/{tid}/messages",
        json={"text": "hi", "locale": "en"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403 and r.json()["code"] == "forbidden_origin"


def test_models_down_yields_error_event_finishes_record_and_stays_resumable(
    client, chat_env, recipe
):
    model = chat_env.model
    working = model.invoke

    def failing(messages, config=None, **kwargs):
        raise AllModelsFailed("down")

    # Intent and parsing use structured output and work; the planner's calls fail.
    model.invoke = failing
    tid = new_thread(client)
    events = sse(post_message(client, tid))
    assert of(events, "error") == [{"code": "models_unavailable"}]
    assert names(events)[-2:] == ["error", "done"] and "plan" not in names(events)
    chat_env.session.expire_all()
    run = chat_env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    assert run.final_passed is False and run.final_plan is None and run.retries == 0
    # The conversation stays resumable: a new message works once the model is back.
    model.invoke = working
    model.structured["Intent"] = [{"intent": "plan"}]
    events = sse(post_message(client, tid))
    assert "plan" in names(events) and names(events)[-1] == "done"


def test_unexpected_error_yields_internal_without_details(client, chat_env, monkeypatch, caplog):
    def broken(self, *args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(PlanningRecorder, "start", broken)
    tid = new_thread(client)
    events = sse(post_message(client, tid))
    assert of(events, "error") == [{"code": "internal"}]
    assert names(events)[-1] == "done"
    assert "secret detail" not in json.dumps(events)
    assert any("secret detail" in (r.exc_text or "") for r in caplog.records)


def test_fail_only_finishes_unfinished_records(session):
    factory = rollback_session_factory(session)
    recorder = PlanningRecorder(factory)
    args = dict(request={}, defaults_used=[], prompt_version="p", tools_version="t",
                has_temporary_guests=False)
    open_run = recorder.start("t-open", **args)
    done_run = recorder.start("t-done", **args)
    recorder.finish(done_run, retries=1, first_passed=True, final_passed=True, final_plan={"a": 1})
    recorder.fail(open_run, retries=2)
    recorder.fail(done_run, retries=2)
    session.expire_all()
    opened, done = (session.get(PlanningRun, i) for i in (open_run, done_run))
    assert (opened.final_passed, opened.final_plan, opened.retries) == (False, None, 2)
    assert (done.final_passed, done.final_plan, done.retries) == (True, {"a": 1}, 1)


def test_client_disconnect_mid_run_finishes_the_record(client, chat_env):
    runtime = app.dependency_overrides[get_chat_runtime]()
    tid = new_thread(client)
    config = {"configurable": {"thread_id": tid}}
    stream = chat_module._stream(
        runtime, {"messages": [HumanMessage("hi")], "locale": "en"}, config
    )
    chunks = []
    for chunk in stream:
        chunks.append(chunk)
        if '"context"' in chunk:  # the planning record exists from here on
            break
    chat_env.session.expire_all()
    run = chat_env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    assert run.final_passed is None
    # Starlette does not call close(); the generator is closed on garbage collection or
    # explicitly, and either way its finally block runs.
    stream.close()
    chat_env.session.expire_all()
    run = chat_env.session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()
    assert run.final_passed is False and run.final_plan is None
    assert not any("done" in c for c in chunks)


def test_thread_existence_with_the_postgres_checkpointer(
    client, tmp_path, session, recipe, test_database_url
):
    created: list[str] = []
    with open_checkpointer(test_database_url) as saver:
        try:
            install(tmp_path, session, plan_model(recipe), "plan_then_adjust", saver)
            unknown = client.get(f"/api/chat/threads/{uuid.uuid4()}")
            assert unknown.status_code == 404 and unknown.json()["code"] == "thread_not_found"
            tid = new_thread(client)
            created.append(tid)
            r = client.get(f"/api/chat/threads/{tid}")
            assert r.status_code == 200
            assert r.json() == {"messages": [], "current_plan": None, "pending": None}
        finally:
            for thread_id in created:
                saver.delete_thread(thread_id)
