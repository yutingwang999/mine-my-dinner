from langchain_core.messages import AIMessage, HumanMessage

from minemydinner.agent.follow_up import (
    FollowUpReading,
    ReadCounts,
    ReadConditions,
    reader_messages,
    request_after,
)


def _request(**over) -> dict:
    base = {
        "dishes": 3,
        "soups": 1,
        "meat_dishes": 2,
        "vegetable_dishes": 1,
        "staples": None,
        "meat_bound": "exact",
        "vegetable_bound": "at_least",
        "range_fields": ["meat_dishes"],
        "counts_stated_now": False,
        "mode": "shopping",
        "time_limit_min": 30,
        "must_use": ["鸡蛋"],
        "prefer_use": [],
        "meal_avoid": ["猪肉"],
        "query": "清淡",
        "headcount": 2,
    }
    return {**base, **over}


def test_reading_schema_defaults():
    reading = FollowUpReading.model_validate({"kind": "other"})
    assert reading.kind == "other"
    assert reading.ambiguous is False
    assert reading.remove == [] and reading.add == []
    assert reading.wish == "" and reading.wish_general is False
    assert reading.counts == ReadCounts()
    assert reading.conditions == ReadConditions()


def test_request_after_counts_replace_as_a_group():
    old = _request()
    reading = FollowUpReading(kind="change", counts=ReadCounts(dishes=4))
    new = request_after(old, reading)
    assert new["dishes"] == 4
    assert new["soups"] is None
    assert new["meat_dishes"] is None and new["vegetable_dishes"] is None
    assert new["meat_bound"] is None and new["vegetable_bound"] is None
    assert new["range_fields"] == []
    assert new["counts_stated_now"] is True
    assert old["dishes"] == 3 and old["meat_dishes"] == 2  # the input is not changed


def test_request_after_no_counts_keeps_the_group():
    new = request_after(_request(), FollowUpReading(kind="change"))
    assert new["dishes"] == 3 and new["meat_dishes"] == 2
    assert new["counts_stated_now"] is False


def test_request_after_meal_avoid_is_the_whole_list():
    old = _request(meal_avoid=["猪肉"])
    assert request_after(old, FollowUpReading(kind="change", meal_avoid=[]))["meal_avoid"] == []
    both = request_after(old, FollowUpReading(kind="change", meal_avoid=["猪肉", "香菜"]))
    assert both["meal_avoid"] == ["猪肉", "香菜"]


def test_request_after_wish_replaces_query_only_when_stated():
    old = _request(query="清淡")
    assert request_after(old, FollowUpReading(kind="change"))["query"] == "清淡"
    assert request_after(old, FollowUpReading(kind="change", wish="辣一些"))["query"] == "辣一些"


def test_request_after_conditions_and_clear_time_limit():
    old = _request()
    kept = request_after(old, FollowUpReading(kind="change"))
    assert kept["time_limit_min"] == 30 and kept["mode"] == "shopping"
    cond = ReadConditions(mode="fridge_only", time_limit_min=20, must_use=["番茄"])
    changed = request_after(old, FollowUpReading(kind="change", conditions=cond))
    assert changed["mode"] == "fridge_only"
    assert changed["time_limit_min"] == 20
    assert changed["must_use"] == ["番茄"]
    assert changed["prefer_use"] == []
    cleared = request_after(
        old, FollowUpReading(kind="change", conditions=ReadConditions(clear_time_limit=True))
    )
    assert cleared["time_limit_min"] is None
    assert cleared["mode"] == "shopping"


def test_reader_messages_order_and_no_nickname():
    conversation = [HumanMessage("来个三菜一汤"), AIMessage("好的，方案如下"), HumanMessage("换掉汤")]
    messages = reader_messages(
        conversation,
        "Current plan:\n- 7 番茄汤 (soup; 番茄)",
        ["Diner 1: no rules", "Diner 2: 不吃羊肉"],
        "Request: 3 dishes",
        "zh",
    )
    text = "\n".join(str(m.content) for m in messages)
    assert "Diner 2: 不吃羊肉" in text
    assert "小明" not in text  # a saved nickname never reaches the model
    order = [
        text.index(part)
        for part in ("Current plan:", "Diner 1: no rules", "Request: 3 dishes", "来个三菜一汤", "换掉汤")
    ]
    assert order == sorted(order)
    assert "zh" in text
