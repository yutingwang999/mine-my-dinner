import json

import pytest
from langchain.tools import ToolRuntime
from sqlalchemy import select

from minemydinner.models import Inventory
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.tools import (
    INVENTORY_TOOLS,
    PLANNER_TOOLS,
    TOOLS_VERSION,
    PlannerDeps,
    tools_version,
)
from tests.planningfakes import (
    FakeEmbedClient,
    L,
    make_recipe,
    own_cookware,
    rollback_session_factory,
    rule,
    self_diner,
    stock,
)
from tests.recipefakes import by_zh, seeded  # noqa: F401

ALL_TOOLS = {t.name: t for t in PLANNER_TOOLS + INVENTORY_TOOLS}


@pytest.fixture
def session_factory(session):
    return rollback_session_factory(session)


@pytest.fixture
def invoke(session_factory):
    def run(name, args=None, diner_ids=(), client=None):
        deps = PlannerDeps(
            session_factory=session_factory,
            client=client or FakeEmbedClient(),
            constraints=MealConstraints(diner_ids=tuple(diner_ids)),
            mode="plan",
        )
        runtime = ToolRuntime(
            state={},
            context=deps,
            config={},
            stream_writer=lambda _: None,
            tool_call_id="call-1",
            store=None,
        )
        call = {"name": name, "args": {**(args or {}), "runtime": runtime}, "id": "call-1"}
        call["type"] = "tool_call"
        message = ALL_TOOLS[name].invoke(call)
        return json.loads(message.content)

    return run


def test_tools_version_is_stable():
    assert TOOLS_VERSION == tools_version(PLANNER_TOOLS)
    assert len(TOOLS_VERSION) == 12
    int(TOOLS_VERSION, 16)


def test_tools_version_changes_with_a_schema(monkeypatch):
    before = tools_version(PLANNER_TOOLS)
    monkeypatch.setattr(PLANNER_TOOLS[0], "description", "changed")
    assert tools_version(PLANNER_TOOLS) != before


def test_planner_tools_order_and_inventory_tool_is_separate():
    assert [t.name for t in PLANNER_TOOLS] == [
        "get_inventory",
        "search_recipes",
        "get_recipe",
        "compute_missing",
        "check_cookware",
        "get_preferences",
    ]
    assert [t.name for t in INVENTORY_TOOLS] == ["update_inventory"]


def test_tool_argument_schemas_are_plain_json_schema():
    for t in PLANNER_TOOLS + INVENTORY_TOOLS:
        json.dumps(t.tool_call_schema.model_json_schema())


def test_runtime_is_not_in_the_model_facing_schema():
    for t in PLANNER_TOOLS + INVENTORY_TOOLS:
        assert "runtime" not in t.tool_call_schema.model_json_schema().get("properties", {})


def test_search_description_states_what_code_enforces():
    text = ALL_TOOLS["search_recipes"].description
    assert "Dishes the selected diners cannot eat are already excluded" in text
    assert "never a filter" in text
    assert "only when the user names" in text
    assert "only when the user asks" in text


def test_search_tool_returns_json_with_codes_not_names(invoke, session, seeded):
    make_recipe(session, "番茄炒蛋", lines=[L("鸡蛋")])
    odd = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    ok = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    me = self_diner(session)
    me.display_name = "Zhang Wei"
    rule(session, me.id, "allergy", allergen="egg")
    result = invoke("search_recipes", {}, diner_ids=[me.id])
    assert [h["id"] for h in result["hits"]] == [ok.id]
    assert result["left_out_unknown_allergens"] == [odd.title]
    assert result["semantic_paused"] is False
    assert result["unresolved_must_use"] == []
    text = json.dumps(result, ensure_ascii=False)
    assert "Zhang Wei" not in text
    assert "source_text" not in text
    prefs = invoke("get_preferences", diner_ids=[me.id])
    assert prefs == {"preferences": ["Diner 1: allergy egg"]}
    assert "Zhang Wei" not in json.dumps(prefs)


def test_get_recipe_unknown_id_returns_error_payload(invoke, seeded):
    assert invoke("get_recipe", {"recipe_id": 999999}) == {"error": "not_found"}


def test_get_recipe_has_no_source_text(invoke, session, seeded):
    recipe = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    detail = invoke("get_recipe", {"recipe_id": recipe.id})
    assert detail["id"] == recipe.id
    assert "source_text" not in detail


def test_inventory_missing_and_cookware_tools(invoke, session, seeded):
    recipe = make_recipe(session, "清炒白菜", lines=[L("小白菜")], cookware=["炒锅"])
    stock(session, "鸡蛋", count=3)
    snapshot = invoke("get_inventory", {"sort_by_expiry": True})
    assert any(row["id"] == by_zh(session, "鸡蛋").id for row in snapshot["ingredients"])
    missing = invoke("compute_missing", {"recipe_ids": [recipe.id]})
    assert [m["name"] for m in missing["missing"][str(recipe.id)]] == ["小白菜"]
    cookware = invoke("check_cookware", {"recipe_ids": [recipe.id]})
    assert len(cookware["missing_cookware"][str(recipe.id)]) == 1
    own_cookware(session, "炒锅")
    cookware = invoke("check_cookware", {"recipe_ids": [recipe.id]})
    assert cookware["missing_cookware"][str(recipe.id)] == []


def test_update_inventory_commits_without_reaching_the_database(invoke, session, seeded):
    result = invoke(
        "update_inventory",
        {"changes": [{"name": "鸡蛋", "action": "set", "count": 6}, {"name": "不存在的菜", "action": "set", "count": 1}]},
    )
    assert result == {"applied": ["鸡蛋"], "unmatched": ["不存在的菜"], "rejected": []}
    row = session.scalars(select(Inventory).where(Inventory.ingredient_id == by_zh(session, "鸡蛋").id)).one()
    assert row.count == 6


def _runtime(deps):
    return ToolRuntime(
        state={}, context=deps, config={}, stream_writer=lambda _: None,
        tool_call_id="c", store=None,
    )


def test_failing_session_factory_returns_tool_failed_and_logs(caplog):
    def broken():
        raise RuntimeError("secret detail")

    deps = PlannerDeps(
        session_factory=broken,
        client=FakeEmbedClient(),
        constraints=MealConstraints(diner_ids=()),
        mode="plan",
    )
    call = {"name": "get_preferences", "args": {"runtime": _runtime(deps)}, "id": "c"}
    call["type"] = "tool_call"
    with caplog.at_level("ERROR"):
        message = ALL_TOOLS["get_preferences"].invoke(call)
    assert json.loads(message.content) == {"error": "tool_failed"}
    assert "secret detail" not in message.content
    assert "planner tool failed" in caplog.text


@pytest.mark.parametrize(
    "name,args",
    [
        ("compute_missing", {"recipe_ids": list(range(1, 22))}),
        ("compute_missing", {"recipe_ids": []}),
        ("check_cookware", {"recipe_ids": [0]}),
        ("update_inventory", {"changes": []}),
        ("update_inventory", {"changes": [{"name": "x", "action": "set", "count": 1}] * 31}),
        ("update_inventory", {"changes": [{"name": "x", "action": "set", "level": "huge"}]}),
        ("update_inventory", {"changes": [{"name": "x", "action": "set", "count": -1}]}),
        ("search_recipes", {"max_time": 0}),
        ("search_recipes", {"max_time": 1441}),
        ("search_recipes", {"exclude_recent_days": 61}),
        ("search_recipes", {"tags": ["a"] * 11}),
        ("search_recipes", {"must_use": ["a"] * 11}),
        ("get_inventory", {"category": "not a category"}),
    ],
)
def test_oversized_or_invalid_arguments_are_rejected(name, args):
    from pydantic import ValidationError

    call = {"name": name, "args": args, "id": "c", "type": "tool_call"}
    with pytest.raises(ValidationError):
        ALL_TOOLS[name].invoke(call)


def test_unknown_recipe_ids_are_reported_not_treated_as_nothing_missing(invoke, session, seeded):
    recipe = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    result = invoke("compute_missing", {"recipe_ids": [recipe.id, 999999]})
    assert list(result["missing"]) == [str(recipe.id)]
    assert result["unknown_recipe_ids"] == [999999]
    result = invoke("check_cookware", {"recipe_ids": [999999]})
    assert result == {"missing_cookware": {}, "unknown_recipe_ids": [999999]}


def test_tool_runs_through_toolnode_with_injected_runtime(session_factory, session, seeded):
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    recipe = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    graph = StateGraph(MessagesState, context_schema=PlannerDeps)
    graph.add_node("tools", ToolNode(PLANNER_TOOLS))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    deps = PlannerDeps(
        session_factory=session_factory,
        client=FakeEmbedClient(),
        constraints=MealConstraints(diner_ids=()),
        mode="plan",
    )
    call = {"name": "get_recipe", "args": {"recipe_id": recipe.id}, "id": "c1", "type": "tool_call"}
    out = graph.compile().invoke({"messages": [AIMessage("", tool_calls=[call])]}, context=deps)
    assert json.loads(out["messages"][-1].content)["id"] == recipe.id


def test_exclude_recent_days_accepts_60(invoke, seeded):
    assert "hits" in invoke("search_recipes", {"exclude_recent_days": 60})
