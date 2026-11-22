import json
import re
from dataclasses import dataclass
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, func, select

from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.labels import replace_nicknames, restore_labels
from minemydinner.agent.plan_card import client_card
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import set_request_style
from minemydinner.api.chat import ChatRuntime, get_chat_runtime
from minemydinner.api.deps import get_llm_client
from minemydinner.llm.client import AllModelsFailed
from minemydinner.main import app
from minemydinner.models import AppPreference, Diner, PlanningRun, Recipe
from tests.agentfakes import FakeEmbeddings, ScriptedChatModel, final_proposal, make_llm_client
from tests.agentfakes import tool_call
from tests.planningfakes import L, make_recipe, rollback_session_factory, rule, self_diner, stock
from tests.recipefakes import by_zh, seeded  # noqa: F401
from tests.test_chat_api import names, new_thread, of, post_message, post_resume, sse

LABEL = re.compile(r"(?i)diner\s*\d+")


@dataclass
class Env:
    session: Any
    runtime: ChatRuntime
    model: ScriptedChatModel
    embeddings: FakeEmbeddings
    me: Diner
    wang: Diner
    veg: Any
    egg: Any
    pork: Any


@pytest.fixture
def env(client, tmp_path, session, seeded):  # noqa: F811
    session.execute(delete(AppPreference))
    session.flush()
    set_request_style(session, "plan_then_adjust")
    me = self_diner(session)
    wang = Diner(display_name="小王", is_self=False)
    session.add(wang)
    session.flush()
    stock(session, "小白菜", level="plenty")
    veg = make_recipe(session, "清炒白菜", "vegetable dish", [L("小白菜")], servings=2)
    egg = make_recipe(session, "番茄炒蛋", "vegetable dish", [L("番茄"), L("鸡蛋")])
    pork = make_recipe(session, "红烧肉", "meat dish", [L("五花肉")])
    model = ScriptedChatModel(
        replies=[tool_call("get_recipe", {"recipe_id": veg.id}), final_proposal([veg.id])],
        structured={"Intent": [{"intent": "plan"}], "ParsedRequest": [{}]},
    )
    embeddings = FakeEmbeddings()
    factory = rollback_session_factory(session)
    llm = make_llm_client(
        tmp_path,
        factory,
        model,
        embeddings=embeddings,
        pre_hooks=(replace_nicknames,),
        post_hooks=(restore_labels,),
    )
    graph = build_graph(GraphDeps(factory, llm, PlanningRecorder(factory)), InMemorySaver())
    runtime = ChatRuntime(graph, PlanningRecorder(factory))
    app.dependency_overrides[get_chat_runtime] = lambda: runtime
    app.dependency_overrides[get_llm_client] = lambda: llm
    return Env(session, runtime, model, embeddings, me, wang, veg, egg, pork)


def planned_thread(client, env: Env, with_wang: bool = False) -> str:
    """A thread whose newest reply carries a plan of the vegetable dish. With `with_wang`,
    小王 eats too (picked on the diner card)."""
    tid = new_thread(client)
    if with_wang:
        # One dish, so the one dish in stock makes a plan.
        env.model.structured["ParsedRequest"] = [
            {"headcount": 2, "others_mentioned": True, "dishes": 1}
        ]
        events = sse(post_message(client, tid, "我和小王吃饭"))
        assert of(events, "interrupt")[0]["kind"] == "diners"
        events = sse(post_resume(client, tid, {"selected": [env.me.id, env.wang.id]}))
    else:
        events = sse(post_message(client, tid, "今晚吃什么"))
    assert "plan" in names(events)
    return tid


def edit(client, tid, action: str, locale: str = "zh", **body):
    return client.post(
        f"/api/chat/threads/{tid}/plan/{action}", json={"locale": locale, **body}
    )


def thread(client, tid) -> dict:
    return client.get(f"/api/chat/threads/{tid}").json()


def values_of(env: Env, tid: str) -> dict:
    return env.runtime.graph.get_state({"configurable": {"thread_id": tid}}).values


def ids(plan: dict) -> list[int]:
    return [r["id"] for r in plan["recipes"]]


def restrict_pork(env: Env) -> None:
    rule(env.session, env.wang.id, "dietary restriction", ingredient="五花肉")


def test_remove_returns_card_and_persists(client, env):
    tid = planned_thread(client, env)
    r = edit(client, tid, "remove", recipe_id=env.veg.id)
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["recipes"] == [] and plan["edited"] is True
    body = thread(client, tid)
    assert body["current_plan"] == plan
    newest = [i for i in body["items"] if i["type"] == "assistant"][-1]
    assert newest["plan"] == plan
    # No chat message is added by an edit.
    assert [i["type"] for i in body["items"]] == ["user", "assistant"]


def test_add_needs_confirmation_409(client, env):
    restrict_pork(env)
    tid = planned_thread(client, env, with_wang=True)
    before = values_of(env, tid)
    r = edit(client, tid, "add", recipe_id=env.pork.id, confirm=[])
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "needs_confirmation"
    [conflict] = body["conflicts"]
    assert conflict["recipe_id"] == env.pork.id
    assert conflict["diner_name"] == "小王" and conflict["kind"] == "ingredient"
    assert conflict["ingredient_name"] == "五花肉"
    after = values_of(env, tid)
    assert after["current_plan"] == before["current_plan"]
    assert after["plan_versions"] == before["plan_versions"]


def test_add_confirmed_200(client, env):
    restrict_pork(env)
    tid = planned_thread(client, env, with_wang=True)
    conflicts = edit(client, tid, "add", recipe_id=env.pork.id).json()["conflicts"]
    r = edit(client, tid, "add", recipe_id=env.pork.id, confirm=conflicts)
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert ids(plan) == [env.veg.id, env.pork.id]
    [override] = plan["edit"]["overrides"]
    assert override["recipe_id"] == env.pork.id
    assert override["ingredient_id"] == by_zh(env.session, "五花肉").id
    assert plan["edit"]["conflicts"] == []
    # Adding the same dish again is refused.
    again = edit(client, tid, "add", recipe_id=env.pork.id, confirm=conflicts)
    assert again.status_code == 422 and again.json()["code"] == "already_in_plan"


def test_add_unknown_recipe_404(client, env):
    tid = planned_thread(client, env)
    r = edit(client, tid, "add", recipe_id=999999999)
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_keep_and_restore(client, env):
    tid = planned_thread(client, env, with_wang=True)
    # A rule added after planning: the dish in the plan gains a conflict.
    rule(env.session, env.wang.id, "dietary restriction", ingredient="小白菜")
    plan = edit(client, tid, "add", recipe_id=env.egg.id).json()["plan"]
    [marked] = plan["edit"]["conflicts"]
    assert marked["recipe_id"] == env.veg.id and plan["passed"] is False
    r = edit(client, tid, "keep", recipe_id=env.veg.id, confirm=[marked])
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["edit"]["conflicts"] == []
    assert r.json()["plan"]["passed"] is True
    # Keep on a dish with no conflict returns the re-checked card, with nothing logged.
    r = edit(client, tid, "keep", recipe_id=env.egg.id, confirm=[])
    assert r.status_code == 200, r.text
    kept = r.json()["plan"]
    assert kept["edit"]["conflicts"] == [] and kept["passed"] is True
    assert [e["action"] for e in kept["edit"]["log"]] == ["add", "keep"]
    r = edit(client, tid, "restore")
    assert r.status_code == 200, r.text
    restored = r.json()["plan"]
    assert ids(restored) == [env.veg.id] and restored["edited"] is False
    assert thread(client, tid)["current_plan"] == restored
    # Restoring twice: the card is no longer a manual version.
    r = edit(client, tid, "restore")
    assert r.status_code == 409 and r.json()["code"] == "not_edited"


def test_remove_dish_not_in_plan_422(client, env):
    tid = planned_thread(client, env)
    r = edit(client, tid, "remove", recipe_id=env.egg.id)
    assert r.status_code == 422 and r.json()["code"] == "not_in_plan"


def test_busy_thread_refused(client, env):
    tid = planned_thread(client, env)
    # The next message names a saved friend: the diner card is pending.
    env.model.structured["ParsedRequest"] = [{"headcount": 2, "others_mentioned": True}]
    events = sse(post_message(client, tid, "我和小王吃饭"))
    assert of(events, "interrupt")[0]["kind"] == "diners"
    before = values_of(env, tid)
    for action, body in (
        ("remove", {"recipe_id": env.veg.id}),
        ("add", {"recipe_id": env.egg.id}),
        ("keep", {"recipe_id": env.veg.id}),
        ("restore", {}),
        ("search", {"query": "白菜"}),
    ):
        r = edit(client, tid, action, **body)
        assert r.status_code == 409 and r.json()["code"] == "thread_busy", action
    after = values_of(env, tid)
    assert after["current_plan"] == before["current_plan"]
    assert after["reply_plans"] == before["reply_plans"]
    assert thread(client, tid)["pending"]["kind"] == "diners"


def test_thread_reports_editable(client, env):
    # A new thread has no plan.
    assert thread(client, new_thread(client))["editable"] is False
    tid = planned_thread(client, env)
    assert thread(client, tid)["editable"] is True
    # A pending diner card.
    env.model.structured["ParsedRequest"] = [{"headcount": 2, "others_mentioned": True}]
    sse(post_message(client, tid, "我和小王吃饭"))
    assert thread(client, tid)["pending"]["kind"] == "diners"
    assert thread(client, tid)["editable"] is False


def test_failed_run_is_not_editable(client, env):
    tid = planned_thread(client, env)
    assert edit(client, tid, "remove", recipe_id=env.veg.id).status_code == 200

    def failing(messages, config=None, **kwargs):
        raise AllModelsFailed("down")

    # The planner's calls fail: the run stops mid-way with its next step saved.
    env.model.invoke = failing
    events = sse(post_message(client, tid, "再来一次"))
    assert of(events, "error") == [{"code": "models_unavailable"}]
    body = thread(client, tid)
    assert body["pending"] is None and body["editable"] is False
    r = edit(client, tid, "restore")
    assert r.status_code == 409 and r.json()["code"] == "thread_busy"


def test_sequential_edits_build_on_each_other(client, env):
    tid = planned_thread(client, env)
    edit(client, tid, "add", recipe_id=env.egg.id)
    edit(client, tid, "add", recipe_id=env.pork.id)
    first = edit(client, tid, "remove", recipe_id=env.veg.id)
    second = edit(client, tid, "remove", recipe_id=env.egg.id)
    assert first.status_code == second.status_code == 200
    plan = second.json()["plan"]
    assert ids(plan) == [env.pork.id]
    assert [e["action"] for e in plan["edit"]["log"]] == ["add", "add", "remove", "remove"]
    versions = values_of(env, tid)["plan_versions"]
    # One model version and one manual version, updated in place; the client gets it without
    # the server-only fields.
    assert len(versions) == 2 and client_card(versions[-1]) == plan


def test_edit_keeps_thread_at_end_and_chat_continues(client, env):
    tid = planned_thread(client, env)
    edit(client, tid, "remove", recipe_id=env.veg.id)
    state = env.runtime.graph.get_state({"configurable": {"thread_id": tid}})
    assert state.next == () and not state.interrupts
    events = sse(post_message(client, tid, "再来一次"))
    assert "plan" in names(events) and names(events)[-1] == "done"
    body = thread(client, tid)
    assert [i["type"] for i in body["items"]] == ["user", "assistant", "user", "assistant"]
    # Until plan 3b, a new message plans again: the edits are not carried into it.
    assert ids(body["current_plan"]) == [env.veg.id]
    assert not body["current_plan"].get("edited")


def test_old_thread_without_edit_fields(client, env):
    tid = planned_thread(client, env)
    plan = values_of(env, tid)["current_plan"]
    assert "edited" not in plan and "edit" not in plan
    assert edit(client, tid, "remove", recipe_id=env.veg.id).status_code == 200
    empty = new_thread(client)
    for action, body in (
        ("remove", {"recipe_id": env.veg.id}),
        ("add", {"recipe_id": env.egg.id}),
        ("restore", {}),
        ("search", {"query": "白菜"}),
    ):
        r = edit(client, empty, action, **body)
        assert r.status_code == 409 and r.json()["code"] == "no_plan", action


def test_unknown_thread_404(client, env):
    for tid in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        r = edit(client, tid, "remove", recipe_id=env.veg.id)
        assert r.status_code == 404 and r.json()["code"] == "thread_not_found"


def test_edits_do_not_touch_planning_runs(client, env):
    restrict_pork(env)
    tid = planned_thread(client, env, with_wang=True)
    session = env.session
    session.expire_all()
    run_id = values_of(env, tid)["run_id"]
    count = session.scalar(select(func.count()).select_from(PlanningRun))
    final_plan = session.get(PlanningRun, run_id).final_plan
    calls_before = len(env.model.sent)
    conflicts = edit(client, tid, "add", recipe_id=env.pork.id).json()["conflicts"]
    edit(client, tid, "add", recipe_id=env.pork.id, confirm=conflicts)
    edit(client, tid, "remove", recipe_id=env.veg.id)
    edit(client, tid, "restore")
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(PlanningRun)) == count
    assert session.get(PlanningRun, run_id).final_plan == final_plan
    # No model call during an edit.
    assert len(env.model.sent) == calls_before


def test_search_marks_in_plan_and_conflicts(client, env):
    restrict_pork(env)
    tid = planned_thread(client, env, with_wang=True)
    r = edit(client, tid, "search", query="白菜")
    assert r.status_code == 200, r.text
    [veg] = [h for h in r.json()["hits"] if h["id"] == env.veg.id]
    assert veg["in_plan"] is True and veg["conflicts"] == []
    assert set(veg) >= {
        "id",
        "title",
        "dish_type",
        "missing",
        "missing_cookware",
        "conflicts",
        "blocked_unknown",
        "in_plan",
    }
    hits = edit(client, tid, "search", query="红烧肉").json()["hits"]
    [pork] = [h for h in hits if h["id"] == env.pork.id]
    assert pork["in_plan"] is False
    assert pork["missing"] == ["五花肉"]
    [conflict] = pork["conflicts"]
    assert conflict["diner_name"] == "小王" and conflict["is_self"] is False
    assert conflict["kind"] == "ingredient" and conflict["ingredient_name"] == "五花肉"
    # The search's conflicts are the ones adding the dish asks to confirm.
    asked = edit(client, tid, "add", recipe_id=env.pork.id).json()["conflicts"]
    assert asked == pork["conflicts"]


def mentions(session, recipe_id: int, query: str) -> bool:
    """Whether the recipe's title, tags, line names, or linked entry names contain query."""
    recipe = session.get(Recipe, recipe_id)
    texts = [recipe.title, *recipe.tags]
    for line in recipe.ingredients:
        texts.append(line.name)
        entry = line.ingredient
        if entry is not None:
            texts += [entry.name_zh or "", entry.name_en or "", *entry.aliases_zh]
            texts += [*entry.aliases_en, *entry.user_aliases_zh, *entry.user_aliases_en]
    return any(query.lower() in text.lower() for text in texts)


def test_search_is_a_text_match(client, env):
    tid = planned_thread(client, env)
    stew = make_recipe(env.session, "牛腩煲", "meat dish", [L("萝卜块", linked=False)])
    bone = make_recipe(env.session, "骨汤", "soup", [L("牛骨XQ", linked=False)])
    late = make_recipe(env.session, "凉拌菜", "vegetable dish", [L("黄瓜条", linked=False)])
    late.tags = ["夜宵XQ"]
    env.session.flush()
    env.embeddings.embedded.clear()
    hits = edit(client, tid, "search", query="牛").json()["hits"]
    assert 0 < len(hits) <= 10
    # Only dishes that contain the text; nothing ranked in by meaning.
    assert all(mentions(env.session, h["id"], "牛") for h in hits)
    assert env.egg.id not in [h["id"] for h in hits]
    # Title matches come first.
    in_title = ["牛" in h["title"] for h in hits]
    assert in_title == sorted(in_title, reverse=True)
    assert stew.id in [h["id"] for h in hits] or all(in_title)
    # A line name and a tag match too.
    for query, recipe in (("牛骨XQ", bone), ("夜宵XQ", late)):
        found = edit(client, tid, "search", query=query).json()["hits"]
        assert [h["id"] for h in found] == [recipe.id]
    # No embedding call: the card search never ranks by meaning.
    assert env.embeddings.embedded == []


def test_search_matches_an_ingredient_alias(client, env):
    tid = planned_thread(client, env)
    by_zh(env.session, "五花肉").user_aliases_zh = ["猪五花XQ"]
    env.session.flush()
    hits = edit(client, tid, "search", query="猪五花XQ").json()["hits"]
    assert [h["id"] for h in hits] == [env.pork.id]
    assert hits[0]["missing"] == ["五花肉"]


def test_search_with_no_match(client, env):
    tid = planned_thread(client, env)
    r = edit(client, tid, "search", query="没有这道菜XQ")
    assert r.status_code == 200, r.text
    assert r.json() == {"hits": []}


def test_no_label_in_responses(client, env):
    restrict_pork(env)
    rule(env.session, env.wang.id, "dislike", ingredient="番茄")
    tid = planned_thread(client, env, with_wang=True)
    bodies = []
    search = edit(client, tid, "search", query="番茄炒蛋")
    bodies.append(search.text)
    [egg] = [h for h in search.json()["hits"] if h["id"] == env.egg.id]
    # Dislikes name the diner, never a label.
    assert egg["dislikes"] == [
        {
            "diner_key": f"diner:{env.wang.id}",
            "diner_name": "小王",
            "is_self": False,
            "line": "番茄",
        }
    ]
    bodies.append(edit(client, tid, "search", query="红烧肉").text)
    asked = edit(client, tid, "add", recipe_id=env.pork.id)
    bodies.append(asked.text)
    added = edit(client, tid, "add", recipe_id=env.pork.id, confirm=asked.json()["conflicts"])
    bodies.append(added.text)
    rule(env.session, env.wang.id, "dietary restriction", ingredient="小白菜")
    bodies.append(edit(client, tid, "add", recipe_id=env.egg.id).text)
    marked = values_of(env, tid)["current_plan"]["edit"]["conflicts"]
    bodies.append(edit(client, tid, "keep", recipe_id=env.veg.id, confirm=marked).text)
    bodies.append(edit(client, tid, "remove", recipe_id=env.pork.id).text)
    bodies.append(edit(client, tid, "restore").text)
    for body in bodies:
        assert not LABEL.search(body), body
    assert all(json.loads(body) for body in bodies)


def test_locale_and_body_validation(client, env):
    tid = planned_thread(client, env)
    url = f"/api/chat/threads/{tid}/plan"
    assert client.post(f"{url}/remove", json={"recipe_id": env.veg.id}).status_code == 422
    assert (
        client.post(f"{url}/remove", json={"recipe_id": env.veg.id, "locale": "fr"}).status_code
        == 422
    )
    assert client.post(f"{url}/search", json={"query": "", "locale": "zh"}).status_code == 422


def test_search_rejects_blank_query(client, env):
    tid = planned_thread(client, env)
    for query in ("", "   "):
        assert edit(client, tid, "search", query=query).status_code == 422
    assert edit(client, tid, "search", query="  白菜 ").status_code == 200
