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
from langchain_core.messages import AIMessage, HumanMessage

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
        structured={
            "Intent": [{"intent": "plan"}],
            # A follow-up on a plan is read (plan 3b-2); by default it asks for a new meal.
            "FollowUpReading": [{"kind": "new_meal"}],
            "ParsedRequest": [{}],
        },
    )


@pytest.fixture
def chat_env(client, tmp_path, session, recipe):
    return install(tmp_path, session, plan_model(recipe), "plan_then_adjust")


@pytest.fixture
def chat_env_first_use(client, tmp_path, session, recipe):
    return install(tmp_path, session, plan_model(recipe), None)


@pytest.fixture
def chat_env_empty_fridge(client, tmp_path, session, seeded):
    # A recipe that needs a fridge item nobody has stocked: the request stops before the planner.
    make_recipe(session, "红烧肉", "meat dish", [L("五花肉", role="main")])
    model = ScriptedChatModel(structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]})
    return install(tmp_path, session, model, "plan_then_adjust")


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
    assert r.json() == {
        "items": [],
        "messages": [],
        "current_plan": None,
        "pending": None,
        "editable": False,
    }


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


def test_plan_event_carries_reasons_and_new_actions(client, chat_env_empty_fridge):
    events = sse(post_message(client, new_thread(client), locale="en"))
    plan = of(events, "plan")[0]
    assert plan["passed"] is False and plan["recipes"] == []
    assert [r["reason"] for r in plan["reasons"]] == ["empty_inventory"]
    assert plan["reasons"][0]["text"] == "There is nothing in the fridge to cook with"
    assert plan["next_actions"] == ["switch_to_shopping", "update_inventory"]
    steps = [e["step"] for e in of(events, "progress")]
    assert steps == ["parse", "context", "context", "present"]
    assert not any("PlanProposal" in sent[0].content for sent in chat_env_empty_fridge.model.sent)
    assert names(events)[-1] == "done"


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


def test_resume_accepts_diner_answer(client, tmp_path, session, recipe):
    model = plan_model(recipe)
    model.structured["ParsedRequest"] = [{"headcount": 2, "others_mentioned": True}]
    install(tmp_path, session, model, "plan_then_adjust")
    tid = new_thread(client)
    events = sse(post_message(client, tid, "我和 Alex 吃饭"))
    assert of(events, "interrupt")[0]["kind"] == "diners"
    # A malformed answer: nobody eats.
    assert post_resume(client, tid, {"selected": []}).status_code == 422
    assert post_resume(client, tid, {"selected": [], "others": -1}).status_code == 422
    good = {"selected": [], "guests": [{"name": "Alex", "rules": []}], "others": 0}
    events = sse(post_resume(client, tid, good))
    assert "plan" in names(events) and "interrupt" not in names(events)
    body = client.get(f"/api/chat/threads/{tid}").json()
    (question,) = [item for item in body["items"] if item["type"] == "question"]
    assert question["value"]["kind"] == "diners"
    assert question["value"]["summary"]["names"] == ["Alex"]


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
            assert r.json() == {
                "items": [],
                "messages": [],
                "current_plan": None,
                "pending": None,
                "editable": False,
            }
        finally:
            for thread_id in created:
                saver.delete_thread(thread_id)


def items_of(client, tid) -> list[dict]:
    return client.get(f"/api/chat/threads/{tid}").json()["items"]


def test_plan_is_stored_with_its_reply(client, chat_env):
    tid = new_thread(client)
    sse(post_message(client, tid, "first dinner", "en"))
    sse(post_message(client, tid, "second dinner", "en"))
    items = items_of(client, tid)
    assert [i["type"] for i in items] == ["user", "assistant", "user", "assistant"]
    first, second = items[1], items[3]
    assert first["id"] != second["id"]
    assert first["text"] == "Here is your dinner." and first["plan"]["passed"] is True
    assert second["plan"] is not None
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user", "assistant"]
    assert body["current_plan"] == second["plan"]


def test_empty_note_plan_is_a_card_only_item(client, tmp_path, session, recipe):
    model = ScriptedChatModel(
        replies=[tool_call("get_recipe", {"recipe_id": recipe.id}), final_proposal([recipe.id], "")],
        structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]},
    )
    install(tmp_path, session, model, "plan_then_adjust")
    tid = new_thread(client)
    sse(post_message(client, tid, "dinner", "en"))
    items = items_of(client, tid)
    assert [i["type"] for i in items] == ["user", "assistant"]
    assert items[1]["text"] == "" and items[1]["plan"] is not None
    # The old messages list still leaves the empty reply out.
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert [m["role"] for m in body["messages"]] == ["user"]


def test_questions_stay_in_the_history(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid, "dinner please", "en"))
    sse(post_resume(client, tid, "ask_first"))
    sse(post_resume(client, tid, "two dishes"))
    items = items_of(client, tid)
    kinds = [i["type"] for i in items]
    assert kinds == ["user", "question", "question", "user", "assistant"]
    assert items[0]["text"] == "dinner please"
    assert items[1]["value"] == {"kind": "request_style"}
    assert items[2]["value"]["kind"] == "question"
    assert items[3]["text"] == "two dishes"
    assert items[4]["plan"] is not None


def test_pending_question_is_not_an_item_yet(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid, "dinner please", "en"))
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert body["pending"] == {"kind": "request_style"}
    assert [i["type"] for i in body["items"]] == ["user"]


def test_old_thread_without_reply_plans_reads(client, chat_env):
    runtime = app.dependency_overrides[get_chat_runtime]()
    tid = new_thread(client)
    old_plan = {"recipes": [], "passed": True}
    runtime.graph.update_state(
        {"configurable": {"thread_id": tid}},
        {
            "messages": [HumanMessage("hi"), AIMessage("old reply")],
            "current_plan": old_plan,
        },
        as_node="__start__",
    )
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert body["current_plan"] == old_plan
    assert [i["type"] for i in body["items"]] == ["user", "assistant"]
    assert all(i["plan"] is None for i in body["items"] if i["type"] == "assistant")


def test_display_records_never_reach_models(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid, "dinner please", "en"))
    sse(post_resume(client, tid, "ask_first"))
    sse(post_resume(client, tid, "two dishes"))
    earlier = [i for i in items_of(client, tid) if i["type"] in ("assistant", "question")]
    earlier_plan = next(i["plan"] for i in earlier if i["type"] == "assistant")
    questions = [i["value"] for i in earlier if i["type"] == "question"]
    assert earlier_plan is not None and len(questions) == 2

    # The next turn asks the ask-first question again; answer it so the planner runs.
    chat_env_first_use.model.sent.clear()
    first = sse(post_message(client, tid, "another one", "en"))
    assert of(first, "interrupt")[0]["kind"] == "question"
    second = sse(post_resume(client, tid, "two dishes again"))
    assert "plan" in names(second)

    sent = chat_env_first_use.model.sent
    # Route, parse, and the planner's calls all happened on this turn.
    assert len(sent) >= 4
    text = "\n".join(str(m.content) for call in sent for m in call)
    # The planner's own tool results may name the recipe, so check what only a stored plan
    # or question record would contain.
    # Keys the planner's own tool results never use.
    plan_only_keys = [
        "defaults_used",
        "servings_notes",
        "left_out_unknown_allergens",
        "semantic_paused",
        "next_actions",
        "headcount",
        "unmet",
        "reasons",
    ]
    forbidden = [
        json.dumps(earlier_plan, ensure_ascii=False),
        str(earlier_plan),
        # Plan keys as they would be serialized; bare names appear in the planner's prompt.
        *(f'"{key}":' for key in plan_only_keys),
        *(f"'{key}':" for key in plan_only_keys),
        *(json.dumps(q, ensure_ascii=False) for q in questions),
        *(str(q) for q in questions),
    ]
    for marker in forbidden:
        assert marker not in text, marker


def card_env(tmp_path, session, recipe):
    model = plan_model(recipe)
    model.structured["ParsedRequest"] = [{"headcount": 2, "others_mentioned": True}]
    install(tmp_path, session, model, "plan_then_adjust")


def test_resume_answer_type_must_fit_the_question(client, tmp_path, session, recipe):
    card_env(tmp_path, session, recipe)
    tid = new_thread(client)
    sse(post_message(client, tid, "我和朋友吃饭"))
    r = post_resume(client, tid, "两个人")
    assert r.status_code == 422 and r.json()["code"] == "wrong_answer_type"
    # Null skips the card.
    events = sse(post_resume(client, tid, None))
    assert "plan" in names(events)


def test_resume_text_question_refuses_a_card_answer(client, chat_env_first_use):
    tid = new_thread(client)
    sse(post_message(client, tid))
    r = post_resume(client, tid, {"selected": [1]})
    assert r.status_code == 422 and r.json()["code"] == "wrong_answer_type"
    assert post_resume(client, tid, "plan_then_adjust").status_code == 200


def test_resume_card_with_stated_rules(client, tmp_path, session, recipe):
    from minemydinner.models import Diner
    from tests.planningfakes import self_diner

    me = self_diner(session)
    wang = Diner(display_name="小王", is_self=False)
    session.add(wang)
    session.flush()
    card_env(tmp_path, session, recipe)
    tid = new_thread(client)
    sse(post_message(client, tid, "我和小王吃饭"))
    peanut = {"rule_type": "allergy", "allergen": "peanut"}
    # JSON object keys are strings; the answer reads them as diner ids.
    good = {"selected": [me.id, wang.id], "stated": {str(wang.id): [peanut]}}
    events = sse(post_resume(client, tid, good))
    assert "plan" in names(events)
    runtime = app.dependency_overrides[get_chat_runtime]()
    values = runtime.graph.get_state({"configurable": {"thread_id": tid}}).values
    assert values["constraints"]["guest_rules"] == [
        {
            "diner_key": f"diner:{wang.id}",
            "rule_type": "allergy",
            "allergen": "peanut",
            "ingredient_id": None,
            "text": None,
        }
    ]
    assert values["settled_diners"]["diner_ids"] == [me.id, wang.id]


def test_resume_card_out_of_date(client, tmp_path, session, recipe):
    # A friend deleted (or an ingredient gone) between showing and confirming the card:
    # the run ends with a clear code, again on a retry.
    from minemydinner.models import Diner
    from tests.planningfakes import self_diner

    me = self_diner(session)
    wang = Diner(display_name="小王", is_self=False)
    session.add(wang)
    session.flush()
    card_env(tmp_path, session, recipe)
    tid = new_thread(client)
    sse(post_message(client, tid, "我和小王吃饭"))
    gone_id = wang.id
    session.delete(wang)
    session.flush()
    for attempt in range(2):
        events = sse(post_resume(client, tid, {"selected": [me.id, gone_id]}))
        assert of(events, "error") == [{"code": "card_out_of_date"}], attempt
    bad_ingredient = {"rule_type": "dislike", "ingredient_id": 99999999}
    answer = {"selected": [me.id], "stated": {me.id: [bad_ingredient]}}
    events = sse(post_resume(client, tid, answer))
    assert of(events, "error") == [{"code": "card_out_of_date"}]


def test_resume_card_headcount(client, tmp_path, session, recipe):
    # The card's confirmed total sets the unmatched people; a total below the people on the
    # card is refused with its own code, and the card stays pending.
    from tests.planningfakes import self_diner

    me = self_diner(session)
    card_env(tmp_path, session, recipe)
    tid = new_thread(client)
    sse(post_message(client, tid, "我和朋友吃饭"))
    guest = {"name": "Alex", "rules": []}
    r = post_resume(client, tid, {"selected": [me.id], "guests": [guest], "headcount": 1})
    assert r.status_code == 422 and r.json()["code"] == "headcount_too_low"
    answer = {"selected": [me.id], "guests": [guest], "headcount": 4, "others_answer": "none"}
    events = sse(post_resume(client, tid, answer))
    assert "plan" in names(events)
    runtime = app.dependency_overrides[get_chat_runtime]()
    values = runtime.graph.get_state({"configurable": {"thread_id": tid}}).values
    assert values["settled_diners"]["others"] == 2
    assert values["settled_diners"]["headcount"] == 4
    assert values["settled_diners"]["stated_headcount"] == 2


def picker_reply(slots=(), choices=(), wish_met=None, note: str = "") -> AIMessage:
    return tool_call(
        "PickerAnswer",
        {
            "choices": [{"index": i, "ids": list(ids)} for i, ids in choices],
            "slots": [{"index": i, "recipe_id": r or 0, "reason": ""} for i, r in slots],
            "note": note,
            "wish_result": {True: "yes", False: "no", None: "no_wish"}[wish_met],
        },
    )


def revise_env(tmp_path, session, recipe) -> tuple[ChatEnv, Any]:
    """A chat whose first message plans one dish and whose later messages change it: the
    reader reads "清炒白菜这道换掉吧" as replacing that dish (plan 3b-2)."""
    other = make_recipe(session, "白菜汤", "vegetable dish", [L("小白菜")], servings=2)
    model = plan_model(recipe)
    swap = {"id": recipe.id, "mode": "replace", "quote": "清炒白菜这道"}
    model.structured["FollowUpReading"] = [{"kind": "change", "remove": [swap]}]
    return install(tmp_path, session, model, "plan_then_adjust"), other


def plan_then(client, env: ChatEnv, replies: list) -> list:
    tid = new_thread(client)
    first = sse(post_message(client, tid, "今晚吃什么"))
    assert len(of(first, "plan")) == 1
    env.model.replies = replies
    return sse(post_message(client, tid, "清炒白菜这道换掉吧"))


def test_nothing_fits_revision_streams_its_card(client, tmp_path, session, recipe):
    env, _ = revise_env(tmp_path, session, recipe)
    events = plan_then(client, env, [picker_reply(slots=[(0, None)], note="没有更合适的了。")])
    [card] = of(events, "plan")
    assert [r["id"] for r in card["recipes"]] == [recipe.id]
    assert card["passed"] is False
    assert [u["code"] for u in card["unmet"]] == ["unfilled"]
    assert "import_recipes" in card["next_actions"]
    assert "unfinished" not in card and "plan_kept" not in card
    # The card follows its reply, so the client puts it on that reply.
    assert names(events).index("plan") == names(events).index("message") + 1


def test_unclear_revision_asks_which_dish_without_a_card(client, tmp_path, session, recipe):
    # Plan 3b-2 spec 5.6: a reference that matches no dish keeps the plan and asks which.
    from minemydinner.agent.revise_step import READING_PREFIX_ZH, WHICH_DISH_ZH

    env, _ = revise_env(tmp_path, session, recipe)
    env.model.structured["FollowUpReading"] = [
        {"kind": "change", "ambiguous": True, "reading": "方案里没有鱼。"}
    ]
    events = plan_then(client, env, [picker_reply()])
    assert of(events, "plan") == []
    text = f"{READING_PREFIX_ZH}方案里没有鱼。{WHICH_DISH_ZH}"
    assert of(events, "message") == [{"role": "assistant", "text": text}]


def test_passed_revision_streams_one_card_and_its_steps(client, tmp_path, session, recipe):
    env, other = revise_env(tmp_path, session, recipe)
    events = plan_then(
        client,
        env,
        [
            # The first answer picks the dish already in the plan, so the picker retries.
            picker_reply(slots=[(0, recipe.id)]),
            picker_reply(slots=[(0, other.id)]),
        ],
    )
    [card] = of(events, "plan")
    assert [r["id"] for r in card["recipes"]] == [other.id]
    assert card["passed"] is True
    steps = [e["step"] for e in of(events, "progress")]
    # No parse on a change: the frame (context), the picker (check), then the reply.
    assert steps[0] == "context" and steps[-1] == "present"
    assert "check" in steps and "parse" not in steps


def test_friend_rule_code_never_reaches_the_client(client, tmp_path, session, recipe):
    # The card keeps the request it was built from (plan 3b-1b spec 5.5), and that request's
    # friend_rules carry the label "Diner 2". The client gets the card without the request:
    # no payload holds the label, while the stored card keeps it for the buttons.
    from minemydinner.models import Diner
    from tests.planningfakes import self_diner

    self_diner(session)
    session.add(Diner(display_name="小王", is_self=False))
    session.flush()
    model = plan_model(recipe)
    model.structured["ParsedRequest"] = [
        {
            "headcount": 2,
            "others_mentioned": True,
            "friend_rules": [{"code": "Diner 2", "allergens": ["peanut"]}],
        }
    ]
    install(tmp_path, session, model, "plan_then_adjust")
    tid = new_thread(client)
    first = sse(post_message(client, tid, "我和小王吃饭，小王对花生过敏"))
    assert of(first, "interrupt")[0]["kind"] == "diners"
    events = sse(post_resume(client, tid, None))
    [card] = of(events, "plan")
    assert "request" not in card and "plan_kept" not in card
    body = client.get(f"/api/chat/threads/{tid}").json()
    assert "request" not in body["current_plan"]
    for payload in (first, events, body):
        assert "Diner 2" not in json.dumps(payload, ensure_ascii=False)
    runtime = app.dependency_overrides[get_chat_runtime]()
    values = runtime.graph.get_state({"configurable": {"thread_id": tid}}).values
    stored = values["reply_plans"][values["messages"][-1].id]
    assert stored["request"]["friend_rules"][0]["code"] == "Diner 2"
    assert values["current_plan"]["request"]["friend_rules"][0]["code"] == "Diner 2"
