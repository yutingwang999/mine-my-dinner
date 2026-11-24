from langchain_core.messages import AIMessage, HumanMessage

from minemydinner.agent.follow_up import (
    FollowUpReading,
    ReadCounts,
    ReadConditions,
    hand_edit_line,
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
    assert reading.wish == "" and reading.wish_targets == []
    # Spec 5.2 (amended 2026-11-23): the reader names the dishes a wish touches; there is no
    # "general wish" flag for code to interpret.
    assert "wish_general" not in FollowUpReading.model_fields
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
    # The flag says whether THIS message states counts: an earlier turn's true never carries,
    # or a later button would aim at that turn's stale counts instead of the plan shown.
    carried = request_after(_request(counts_stated_now=True), FollowUpReading(kind="change"))
    assert carried["dishes"] == 3 and carried["counts_stated_now"] is False


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
    assert "by hand" not in text


def test_hand_edit_line_lists_the_edits_in_order_by_title():
    """Task 11 fix 2, item 3: what the user changed by hand on this plan after it was proposed,
    from the card's edit log: adds, removals and a restore, in order; keeps are not plan
    changes. Fix round 1: never "since the last reply", which an "other" reply makes false."""
    log = [
        {"action": "add", "recipe_id": 5, "at": "t1"},
        {"action": "keep", "recipe_id": 5, "at": "t2"},
        {"action": "remove", "recipe_id": 7, "at": "t3"},
        {"action": "restore", "recipe_id": None, "at": "t4"},
    ]
    titles = {5: "红烧茄子", 7: "番茄炒蛋"}
    assert hand_edit_line(log, titles) == (
        "The user changed this plan by hand after it was proposed: added 红烧茄子; removed "
        "番茄炒蛋; restored the proposed plan."
    )
    assert hand_edit_line([], titles) == ""
    assert hand_edit_line([{"action": "keep", "recipe_id": 5}], titles) == ""
    # A dish with no title (it left the library) is still a fact, without a name.
    assert hand_edit_line([{"action": "remove", "recipe_id": 9}], titles) == (
        "The user changed this plan by hand after it was proposed: removed a dish."
    )


def test_reader_messages_put_the_hand_edit_line_after_the_plan():
    line = "The user changed this plan by hand after it was proposed: added 红烧茄子."
    text = reader_messages(
        [HumanMessage("换掉汤")], "Current plan:\n- 7 番茄汤 (soup; 番茄)", [], "Request", "zh",
        hand_edits=line,
    )[0].content
    assert text.index("Current plan:") < text.index(line) < text.index("Diners:")
