"""SPIKE smoke tests: the one-reader mode of a follow-up turn (agent/one_reader.py), with a
scripted model. Each answer kind once."""

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from minemydinner.agent.graph import GraphDeps, build_graph
from minemydinner.agent.records import PlanningRecorder
from minemydinner.agent.request import set_request_style
from minemydinner.models import AppPreference, PlanningRun
from tests.agentfakes import ScriptedChatModel, final_proposal, make_llm_client, tool_call
from tests.planningfakes import L, make_recipe, rollback_session_factory
from tests.recipefakes import seeded  # noqa: F401


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
    deps = GraphDeps(factory, client, PlanningRecorder(factory), one_reader_task="planning")
    dishes = {
        title: make_recipe(session, title, kind, lines)
        for title, kind, lines in [
            ("鱼香茄子", "meat dish", [L("茄子", "main"), L("猪肉")]),
            ("番茄炒蛋", "vegetable dish", [L("番茄"), L("鸡蛋")]),
            ("清炒小白菜", "vegetable dish", [L("小白菜")]),
            ("土豆炖牛肉", "meat dish", [L("牛肉", "main"), L("土豆")]),
        ]
    }
    graph = build_graph(deps, InMemorySaver())
    config = {"configurable": {"thread_id": "one-reader-1"}}
    ids = {title: recipe.id for title, recipe in dishes.items()}
    model.replies = [final_proposal([ids["鱼香茄子"], ids["番茄炒蛋"], ids["清炒小白菜"]])]
    graph.invoke({"messages": [HumanMessage("今晚吃什么")], "locale": "zh"}, config)
    return graph, model, session, config, ids


def answer(kind, recipe_ids=(), message=""):
    return tool_call(
        "OneReaderAnswer", {"kind": kind, "recipe_ids": list(recipe_ids), "message": message}
    )


def follow_up(env, text, replies):
    graph, model, _, config, _ = env
    model.replies = list(replies)
    return graph.invoke({"messages": [HumanMessage(text)], "locale": "zh"}, config)


def last_run(session) -> PlanningRun:
    session.expire_all()
    return session.scalars(select(PlanningRun).order_by(PlanningRun.id.desc())).first()


def ids_of(plan):
    return [r["id"] for r in plan["recipes"]]


def test_plan_answer_is_checked_retried_once_and_presented(env):
    graph, model, session, config, ids = env
    keep = [ids["番茄炒蛋"], ids["清炒小白菜"]]
    out = follow_up(
        env,
        "茄子这道换成牛肉的",
        [
            # Not in the library (H5): retried once with the reason.
            answer("plan", [*keep, 999999], "换了。"),
            answer("plan", [*keep, ids["土豆炖牛肉"]], "换成了炖牛肉。"),
        ],
    )
    assert sorted(ids_of(out["current_plan"])) == sorted([*keep, ids["土豆炖牛肉"]])
    assert out["messages"][-1].content.endswith("换成了炖牛肉。")
    run = last_run(session)
    assert run.request["one_reader"] is True and run.request["revision"] is not None
    assert run.final_passed is True and run.retries == 1
    steps = [e.get("step") for e in run.events if e["kind"] == "model_call"]
    # No route, parse or revise call on this turn.
    assert steps == ["one_reader", "one_reader"]
    answers = [e["answer"] for e in run.events if e["kind"] == "one_reader"]
    assert answers == ["plan", "plan"]


def test_question_is_plain_text_and_keeps_the_plan(env):
    graph, model, session, config, ids = env
    before = graph.get_state(config).values["current_plan"]
    out = follow_up(env, "那个换掉", [answer("question", message="你想换哪一道？")])
    assert out["messages"][-1].content == "你想换哪一道？"
    assert out["messages"][-1].id not in out["reply_plans"]
    assert ids_of(out["current_plan"]) == ids_of(before)


def test_nothing_fits_keeps_the_plan_with_the_reason(env):
    graph, model, session, config, ids = env
    out = follow_up(env, "加一道羊肉", [answer("nothing_fits", message="菜谱库里没有羊肉菜。")])
    card = out["reply_plans"][out["messages"][-1].id]
    assert card["plan_kept"] is True
    assert card["unmet"][0]["text"] == "菜谱库里没有羊肉菜。"
    assert "import_recipes" in card["next_actions"]


def test_new_meal_runs_the_normal_plan_path(env):
    graph, model, session, config, ids = env
    model.structured["ParsedRequest"] = [{"dishes": 1, "mode": "shopping"}]
    out = follow_up(
        env,
        "周末另外安排一顿",
        [answer("new_meal"), final_proposal([ids["土豆炖牛肉"]])],
    )
    assert ids_of(out["current_plan"]) == [ids["土豆炖牛肉"]]
    run = last_run(session)
    assert run.request.get("revision") is None
    assert [e.get("step") for e in run.events if e["kind"] == "model_call"] == [
        "parse",
        "planner",
    ]
