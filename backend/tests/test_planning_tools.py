import json

import pytest
from pydantic import BaseModel
from langchain.tools import ToolRuntime
from sqlalchemy import select

from minemydinner.agent.planner import TOOLS_VERSION, PlanProposal
from minemydinner.models import Inventory
from minemydinner.planning.context import MealConstraints
from minemydinner.planning.tools import (
    INVENTORY_TOOLS,
    PLANNER_TOOLS,
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
    unit_vector,
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
    assert TOOLS_VERSION == tools_version(PLANNER_TOOLS, [PlanProposal])
    assert len(TOOLS_VERSION) == 12
    int(TOOLS_VERSION, 16)


def test_tools_version_changes_with_a_schema(monkeypatch):
    before = tools_version(PLANNER_TOOLS)
    monkeypatch.setattr(PLANNER_TOOLS[0], "description", "changed")
    assert tools_version(PLANNER_TOOLS) != before


def test_tools_version_covers_the_final_answer_schema():
    class Answer(BaseModel):
        ids: list[int]

    class ChangedAnswer(BaseModel):
        ids: list[int]
        extra: str

    assert TOOLS_VERSION != tools_version(PLANNER_TOOLS)
    assert tools_version(PLANNER_TOOLS, [Answer]) != tools_version(PLANNER_TOOLS)
    assert tools_version(PLANNER_TOOLS, [Answer]) == tools_version(PLANNER_TOOLS, [Answer])
    assert tools_version(PLANNER_TOOLS, [ChangedAnswer]) != tools_version(PLANNER_TOOLS, [Answer])


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
        ("search_recipes", {"dish_type": "other"}),
        ("search_recipes", {"dish_type": "drink"}),
        ("search_recipes", {"exclude_ids": list(range(1, 52))}),
        ("search_recipes", {"exclude_ids": [0]}),
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


def test_tool_rejects_other_type_and_too_many_exclude_ids():
    from pydantic import ValidationError

    schema = ALL_TOOLS["search_recipes"].tool_call_schema.model_json_schema()
    text = json.dumps(schema)
    assert '"other"' not in text
    for args in ({"dish_type": "other"}, {"exclude_ids": list(range(1, 52))}):
        call = {"name": "search_recipes", "args": args, "id": "c", "type": "tool_call"}
        with pytest.raises(ValidationError):
            ALL_TOOLS["search_recipes"].invoke(call)


def test_search_description_mentions_facts_and_next_batch():
    text = ALL_TOOLS["search_recipes"].description
    assert "missing" in text and "missing_cookware" in text
    assert "exclude_ids" in text and "next batch" in text
    assert "dish_type" in text


def test_search_tool_filters_by_type_and_skips_excluded_ids(invoke, session, seeded):
    first = make_recipe(session, "番茄汤", dish_type="soup", lines=[L("番茄")], cookware=["炒锅"])
    second = make_recipe(session, "豆腐汤", dish_type="soup", lines=[L("豆腐")])
    make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    stock(session, "豆腐", count=1)
    result = invoke("search_recipes", {"dish_type": "soup", "exclude_ids": [second.id]})
    assert [h["id"] for h in result["hits"]] == [first.id]
    hit = result["hits"][0]
    assert hit["missing"] == ["番茄"] and hit["missing_cookware"] == ["炒锅"]
    assert hit["uses_expiring"] == [] and hit["dislikes"] == []
    both = invoke("search_recipes", {"dish_type": "soup"})
    assert [h["id"] for h in both["hits"]] == [second.id, first.id]


def test_search_tool_reuses_the_run_query_vectors(session_factory, session, seeded):
    make_recipe(session, "近", lines=[L("小白菜")], embedding=unit_vector(0))
    client = FakeEmbedClient(vector=unit_vector(0))
    deps = PlannerDeps(
        session_factory=session_factory,
        client=client,
        constraints=MealConstraints(diner_ids=()),
        mode="plan",
    )
    for _ in range(2):
        call = {
            "name": "search_recipes",
            "args": {"query": "菜", "runtime": _runtime(deps)},
            "id": "c",
            "type": "tool_call",
        }
        assert json.loads(ALL_TOOLS["search_recipes"].invoke(call).content)["hits"]
    assert client.embedded == ["菜"]
    assert list(deps.query_vectors) == ["菜"]


def test_planner_deps_get_their_own_query_vectors():
    def deps():
        return PlannerDeps(
            session_factory=None,
            client=FakeEmbedClient(),
            constraints=MealConstraints(diner_ids=()),
            mode="plan",
        )

    a, b = deps(), deps()
    a.query_vectors["x"] = [1.0]
    assert a.query_vectors == {"x": [1.0]} and b.query_vectors == {}


def test_search_tool_ranks_and_filters_like_the_shortlist(session_factory, session, seeded):
    egg = make_recipe(session, "番茄炒蛋", lines=[L("番茄", role="main"), L("鸡蛋", role="main")])
    cabbage = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])

    def search(**names):
        deps = PlannerDeps(
            session_factory=session_factory,
            client=FakeEmbedClient(),
            constraints=MealConstraints(diner_ids=()),
            mode="shopping",
            **names,
        )
        call = {
            "name": "search_recipes",
            "args": {"runtime": _runtime(deps)},
            "id": "c",
            "type": "tool_call",
        }
        hits = json.loads(ALL_TOOLS["search_recipes"].invoke(call).content)["hits"]
        return [hit["id"] for hit in hits if hit["id"] in (egg.id, cabbage.id)]

    # Newer first by default; the request's named ingredients rank, and all_use filters.
    assert search() == [cabbage.id, egg.id]
    assert search(prefer_use=["鸡蛋"]) == [egg.id, cabbage.id]
    assert search(must_use=["鸡蛋"]) == [egg.id, cabbage.id]
    assert search(all_use=["鸡蛋"]) == [egg.id]


@pytest.fixture
def search_args(session_factory, monkeypatch):
    """Runs the search tool with request filters on PlannerDeps; returns what reached search."""
    from minemydinner.planning import tools
    from minemydinner.planning.search import SearchResult

    seen: dict = {}

    def capture(session, client, ctx, **kwargs):
        seen.clear()
        seen.update(kwargs)
        return SearchResult([])

    monkeypatch.setattr(tools, "search_recipes", capture)

    def run(args, **request):
        deps = PlannerDeps(
            session_factory=session_factory,
            client=FakeEmbedClient(),
            constraints=MealConstraints(diner_ids=()),
            mode="shopping",
            **request,
        )
        call = {
            "name": "search_recipes",
            "args": {**args, "runtime": _runtime(deps)},
            "id": "c",
            "type": "tool_call",
        }
        ALL_TOOLS["search_recipes"].invoke(call)
        return dict(seen)

    return run


def test_search_tool_always_applies_the_request_time_limit(search_args):
    assert search_args({"max_time": 60}, time_limit_min=30)["max_time"] == 30
    assert search_args({"max_time": 20}, time_limit_min=30)["max_time"] == 20
    assert search_args({}, time_limit_min=30)["max_time"] == 30
    assert search_args({"max_time": 45})["max_time"] == 45
    assert search_args({})["max_time"] is None


def test_search_tool_defaults_recency_and_query_from_the_request(search_args):
    request = {"exclude_recent_days": 7, "query": "清淡"}
    defaulted = search_args({}, **request)
    assert defaulted["exclude_recent_days"] == 7 and defaulted["query"] == "清淡"
    # The request's query only ranks, as in the shortlist; it never empties a search.
    assert defaulted["keep_unmatched"] is True
    # The model's own query wins and filters as before.
    given = search_args({"exclude_recent_days": 3, "query": "辣"}, **request)
    assert given["query"] == "辣" and given["keep_unmatched"] is False
    # The recency window never shrinks below the request's; the model may widen it.
    assert given["exclude_recent_days"] == 7
    assert search_args({"exclude_recent_days": 14}, **request)["exclude_recent_days"] == 14
    assert search_args({"exclude_recent_days": 3})["exclude_recent_days"] == 3
    assert search_args({})["query"] is None and search_args({})["exclude_recent_days"] is None


def test_search_tool_time_limit_drops_slower_recipes(session_factory, session, seeded):
    quick = make_recipe(session, "快手菜", lines=[L("小白菜")], time_min=15, time_source="stated")
    slow = make_recipe(session, "慢炖菜", lines=[L("小白菜")], time_min=90, time_source="stated")
    deps = PlannerDeps(
        session_factory=session_factory,
        client=FakeEmbedClient(),
        constraints=MealConstraints(diner_ids=()),
        mode="shopping",
        time_limit_min=30,
    )
    call = {
        "name": "search_recipes",
        "args": {"max_time": 120, "runtime": _runtime(deps)},
        "id": "c",
        "type": "tool_call",
    }
    hits = {h["id"] for h in json.loads(ALL_TOOLS["search_recipes"].invoke(call).content)["hits"]}
    assert quick.id in hits and slow.id not in hits


@pytest.mark.parametrize("mode,first", [("fridge_only", "far"), ("shopping", "near")])
def test_search_tool_ranks_makeable_first_only_in_fridge_mode(
    session_factory, session, seeded, mode, first
):
    stock(session, "豆腐", count=2)
    recipes = {
        "near": make_recipe(session, "近", lines=[L("小白菜")], embedding=unit_vector(0)),
        "far": make_recipe(session, "远", lines=[L("豆腐")], embedding=unit_vector(5)),
    }
    deps = PlannerDeps(
        session_factory=session_factory,
        client=FakeEmbedClient(vector=unit_vector(0)),
        constraints=MealConstraints(diner_ids=()),
        mode=mode,
    )
    call = {
        "name": "search_recipes",
        "args": {"query": "菜", "runtime": _runtime(deps)},
        "id": "c",
        "type": "tool_call",
    }
    hits = json.loads(ALL_TOOLS["search_recipes"].invoke(call).content)["hits"]
    assert hits[0]["id"] == recipes[first].id
