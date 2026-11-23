"""The pure rules of a change (plan 3b-1 spec 5.2, 5.3; plan 3b-2 spec 5.4): quotes, the
general wish's limit, the spec a change starts from and the conditions a follow-up leaves
unstated. No model and no database. The revise step's R1-R9 checks left with it (plan 3b-2)."""

from minemydinner.agent.revision import (
    DishFacts,
    max_general_changes,
    quote_matches,
    revision_spec,
)
from minemydinner.agent.revise_step import CATEGORY_WORDS, CONFLICT_WORDS

FACTS = {
    1: DishFacts("鱼香茄子", ["茄子", "猪肉"], [], "vegetable dish"),
    2: DishFacts("番茄炒蛋", ["番茄", "鸡蛋"], [], "vegetable dish"),
    3: DishFacts("韭菜炒鸡蛋", ["韭菜", "鸡蛋"], [], "vegetable dish"),
    4: DishFacts("玉米排骨汤", ["玉米", "排骨"], ["汤"], "soup"),
    5: DishFacts("清炒西兰花", ["西兰花"], [], "vegetable dish"),
    6: DishFacts("红烧肉", ["五花肉"], [], "meat dish"),
    7: DishFacts("鱼香肉丝", ["猪肉", "木耳"], [], "meat dish"),
    8: DishFacts("冬瓜汤", ["冬瓜"], ["汤"], "soup"),
    9: DishFacts("蒜蓉菠菜", ["菠菜"], [], "vegetable dish"),
    10: DishFacts("醋溜白菜", ["白菜"], [], "vegetable dish"),
    11: DishFacts("干煸四季豆", ["四季豆"], [], "vegetable dish"),
    12: DishFacts("拍黄瓜", ["黄瓜"], [], "vegetable dish"),
    13: DishFacts("青椒肉丝", ["青椒", "猪肉"], [], "meat dish"),
    14: DishFacts("蒜蓉西兰花", ["西兰花", "大蒜"], [], "vegetable dish"),
}


def _plan(ids, locked=(), conflicts=(), overrides=()):
    veg, meat = "vegetable dish", "meat dish"
    types = {1: veg, 2: veg, 3: veg, 4: "soup", 5: veg, 6: meat, 7: meat, 8: "soup",
             9: veg, 10: veg, 11: veg, 12: veg, 13: meat, 14: veg}
    return {
        "recipes": [{"id": i, "title": FACTS[i].title, "dish_type": types[i]} for i in ids],
        "spec": {"mode": "fridge_only", "dishes": 3, "soups": 0},
        "locked": list(locked),
        "edit": {
            "overrides": [{"recipe_id": i} for i in overrides],
            "conflicts": [{"recipe_id": i} for i in conflicts],
        },
    }


def test_max_general_changes():
    assert [max_general_changes(n) for n in range(6)] == [0, 1, 1, 2, 2, 3]


def test_quote_matches():
    assert quote_matches("茄子这道", "鱼香茄子", [], [])
    assert not quote_matches("土豆这道", "鱼香茄子", ["猪肉"], [])
    assert quote_matches("那道汤", "玉米排骨汤", [], ["汤"])
    assert quote_matches("不要猪肉", "鱼香茄子", ["猪肉"], [])
    assert not quote_matches("", "鱼香茄子", ["猪肉"], ["汤"])


def test_revision_spec_keeps_hand_grown_count():
    plan = _plan([1, 2, 5, 6])  # 3 vegetable + 1 meat, then make it 2 and 2
    plan["recipes"][2]["dish_type"] = "meat dish"
    plan["spec"]["dishes"] = 3
    request = {"dishes": 3}
    spec = revision_spec(plan, request, request)
    assert (spec.dishes, spec.meat_dishes, spec.vegetable_dishes) == (4, 2, 2)
    assert spec.meat_bound == spec.vegetable_bound == "exact"
    assert spec.soups == 0
    assert revision_spec(plan, request, {"dishes": 5}).dishes == 5


def test_revision_spec_null_never_clears():
    plan = _plan([1, 2])
    plan["spec"]["mode"] = "fridge_only"
    plan["spec"]["time_limit_min"] = 30
    plan["spec"]["must_use"] = ["鸡蛋"]
    previous = {"mode": "fridge_only", "time_limit_min": 30, "must_use": ["鸡蛋"]}
    spec = revision_spec(plan, previous, {"mode": None, "time_limit_min": None, "must_use": []})
    assert (spec.mode, spec.time_limit_min, spec.must_use) == ("fridge_only", 30, ["鸡蛋"])
    spec = revision_spec(plan, previous, {"mode": "shopping", "time_limit_min": 20})
    assert (spec.mode, spec.time_limit_min) == ("shopping", 20)


def test_revision_spec_counts_stated_now_true_takes_an_equal_count():
    # A plan of 3 trimmed by hand to 2; "再加一道吧" parsed from the app note as 3, which
    # equals the previous request's 3. The newest message stated a count, so 3 wins.
    plan = _plan([1, 6])
    previous = {"dishes": 3}
    request = {"dishes": 3, "counts_stated_now": True}
    spec = revision_spec(plan, previous, request)
    assert spec.dishes == 3
    assert spec.meat_dishes is None and spec.vegetable_dishes is None
    assert spec.soups == 0
    # Every non-null count of such a request replaces the plan's, equal or not.
    spec = revision_spec(plan, {"dishes": 3, "soups": 1}, {
        "dishes": 3, "soups": 1, "counts_stated_now": True,
    })
    assert (spec.dishes, spec.soups) == (3, 1)


def test_revision_spec_counts_stated_now_true_skips_a_carried_split():
    # "一荤一素" first, grown by hand to 3; "再加一道" with the split carried over from the
    # first request: the split is not applied (no stale exact side), the total is 4.
    plan = _plan([1, 5, 6])  # 2 vegetable + 1 meat
    previous = {"meat_dishes": 1, "meat_bound": "exact", "vegetable_dishes": 1,
                "vegetable_bound": "exact"}
    request = {**previous, "dishes": 4, "counts_stated_now": True}
    spec = revision_spec(plan, previous, request)
    assert spec.dishes == 4
    assert spec.meat_dishes is None and spec.vegetable_dishes is None
    assert spec.meat_bound is None and spec.vegetable_bound is None
    # A side the newest message changes is still taken.
    spec = revision_spec(plan, previous, {**request, "meat_dishes": 2})
    assert (spec.dishes, spec.meat_dishes, spec.meat_bound) == (4, 2, "exact")
    assert spec.vegetable_dishes is None


def test_revision_spec_counts_stated_now_false_keeps_the_plan_counts():
    # A plan asked as 3, grown by hand to 4; "想要辣一些的" repeats 3 (or even a stale 2):
    # the newest message stated no count, so the plan keeps 4.
    plan = _plan([1, 2, 5, 6])
    for repeated in ({"dishes": 3}, {"dishes": 2, "soups": 1}):
        request = {**repeated, "counts_stated_now": False, "query": "辣一些的"}
        spec = revision_spec(plan, {"dishes": 3}, request)
        assert (spec.dishes, spec.soups) == (4, 0)
        assert (spec.meat_dishes, spec.vegetable_dishes) == (1, 3)
    # Non-count fields keep their own rule.
    spec = revision_spec(
        plan, {"mode": "fridge_only"}, {"mode": "shopping", "counts_stated_now": False}
    )
    assert spec.mode == "shopping" and spec.dishes == 4


def test_revision_spec_without_counts_stated_now_keeps_the_old_rule():
    # Requests stored before the field: a count changes only when it differs from the
    # previous request.
    plan = _plan([1, 6])
    for request in ({"dishes": 3}, {"dishes": 3, "counts_stated_now": None}):
        assert revision_spec(plan, {"dishes": 3}, request).dishes == 2
    assert revision_spec(plan, {"dishes": 3}, {"dishes": 4}).dishes == 4


def test_revision_spec_no_dishes_uses_request_counts():
    plan = _plan([])
    plan["spec"]["dishes"] = 3
    spec = revision_spec(plan, {"dishes": 3}, {"dishes": 3})
    assert spec.dishes == 3


def test_revision_spec_changed_side_keeps_total_and_frees_the_other():
    plan = _plan([1, 2, 5, 6])  # 3 vegetable + 1 meat
    request = {"meat_dishes": 2, "meat_bound": "at_least"}
    spec = revision_spec(plan, {}, request)
    assert (spec.dishes, spec.meat_dishes, spec.meat_bound) == (4, 2, "at_least")
    assert (spec.vegetable_dishes, spec.vegetable_bound) == (None, None)
    empty = _plan([])
    empty["spec"].update(dishes=3, meat_dishes=1, vegetable_dishes=2)
    spec = revision_spec(empty, {}, {"vegetable_dishes": 3})
    assert (spec.dishes, spec.vegetable_dishes, spec.meat_dishes) == (3, 3, None)


def test_staple_quotes():
    staple = CATEGORY_WORDS["staple food"]
    assert quote_matches("面", "清汤面", ["面粉"], staple)
    assert quote_matches("饭", "白米饭", ["大米"], staple)
    assert not quote_matches("面", "番茄炒蛋", ["番茄", "鸡蛋"], [])
    # A one-character quote that is the title's last character also matches.
    assert quote_matches("面", "担担面", [], [])
    assert not quote_matches("担", "担担面", [], [])


def test_unsuitable_words_name_kept_conflict_dishes():
    # A dish kept against a conflict carries the conflict words, so "不适合的菜" points at it
    # and at no other dish.
    kept = DishFacts("韭菜炒鸡蛋", ["韭菜", "鸡蛋"], list(CONFLICT_WORDS), "vegetable dish")
    quote = "不适合的菜"
    assert quote_matches(quote, kept.title, kept.ingredient_names, kept.category_words)
    other = FACTS[1]
    assert not quote_matches(quote, other.title, other.ingredient_names, other.category_words)


def test_conflict_words_cover_conflicts_kept_and_overrides():
    from minemydinner.agent.revise_step import conflict_words

    plan = {
        "edit": {
            "conflicts": [{"recipe_id": 1, "ingredient_name": "鸡蛋"}],
            "kept": [{"recipe_id": 2, "allergen": "peanut"}],
            "overrides": [{"recipe_id": 2}, {"recipe_id": 3}],
        }
    }
    words = conflict_words(plan)
    assert set(words) == {1, 2, 3}
    assert "不适合" in words[3] and "鸡蛋" in words[1] and "peanut" in words[2]
    assert conflict_words(None) == {}


def test_short_override_text_is_not_a_match_word():
    from minemydinner.agent.revise_step import conflict_words

    plan = {"edit": {"conflicts": [{"recipe_id": 1, "text": "辣", "ingredient_name": "辣椒"}]}}
    assert "辣" not in conflict_words(plan)[1]
    assert "辣椒" in conflict_words(plan)[1]


# ---- a revise turn's unstated fields come from the plan's request (dev run 3 of 3b-1b) ----

PLAN_REQUEST = {
    "headcount": 2,
    "others_mentioned": True,
    "dishes": 3,
    "meat_dishes": 1,
    "meat_bound": "at_least",
    "range_fields": ["dishes"],
    "mode": "shopping",
    "time_limit_min": 40,
    "query": "下饭的",
    "must_use": ["茄子"],
    "prefer_use": ["土豆"],
    "all_use": [],
    "exclude_recent_days": 3,
    "meal_avoid": ["羊肉"],
    "guests": [{"name": "Alex", "allergens": ["peanut"]}],
    "friend_rules": [{"code": "Diner 2", "allergens": ["peanut"]}],
    "self_rules": {"avoid": ["香菜"]},
}


def test_unstated_fields_come_from_the_plan_request():
    from minemydinner.agent.revision import carry_unstated

    filled = carry_unstated({"dishes": None, "counts_stated_now": False}, PLAN_REQUEST)
    for name in (
        "headcount", "others_mentioned", "mode", "time_limit_min", "query", "must_use",
        "prefer_use", "exclude_recent_days", "meal_avoid", "dishes", "meat_dishes",
        "meat_bound", "range_fields",
    ):
        assert filled[name] == PLAN_REQUEST[name], name
    assert filled["counts_stated_now"] is False
    # Statements about people are never carried: the settled diner set holds them.
    assert not filled.get("guests") and not filled.get("friend_rules")
    assert filled.get("self_rules") is None


def test_stated_fields_are_kept():
    from minemydinner.agent.revision import carry_unstated

    parsed = {
        "headcount": 1,
        "mode": "fridge_only",
        "query": "辣的",
        "must_use": ["豆角"],
        "soups": 1,
        "counts_stated_now": False,
    }
    filled = carry_unstated(parsed, PLAN_REQUEST)
    assert (filled["headcount"], bool(filled.get("others_mentioned"))) == (1, False)
    assert (filled["mode"], filled["query"], filled["must_use"]) == ("fridge_only", "辣的", ["豆角"])
    # A stated count keeps the other counts unstated: the counts carry only as a group.
    assert filled["soups"] == 1 and filled.get("dishes") is None
    assert filled.get("meat_dishes") is None and not filled.get("range_fields")
    assert filled["time_limit_min"] == 40


def test_who_eats_carries_only_when_the_parse_mentions_nobody():
    from minemydinner.agent.revision import carry_unstated

    for said in (
        {"others_mentioned": True},
        {"guests": [{"name": "小李"}]},
        {"friend_rules": [{"code": "Diner 3", "avoid": ["香菜"]}]},
    ):
        filled = carry_unstated(said, PLAN_REQUEST)
        assert filled.get("headcount") is None, said
        assert filled["mode"] == "shopping"


def test_counts_do_not_carry_into_a_message_that_states_a_count():
    from minemydinner.agent.revision import carry_unstated

    filled = carry_unstated({"counts_stated_now": True}, PLAN_REQUEST)
    assert all(filled.get(name) is None for name in ("dishes", "meat_dishes", "meat_bound"))


def test_no_plan_request_changes_nothing():
    from minemydinner.agent.revision import carry_unstated

    assert carry_unstated({"dishes": 2}, None) == {"dishes": 2}
