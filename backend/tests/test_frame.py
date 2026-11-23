"""The frame (plan 3b-2 spec 5.4): pure code decides which dishes keep, drop or become slots,
and which bounded choices the picker may make. `build_frame` gets hand-built facts; only
`lookup_named` uses the test database."""

from minemydinner.agent.follow_up import FollowUpReading, ReadAdd, ReadCounts, ReadRemoval
from minemydinner.agent.frame import (
    Choice,
    NamedLookup,
    Slot,
    breaking_from,
    build_frame,
    lookup_named,
)
from minemydinner.agent.revision import DishFacts, max_general_changes
from minemydinner.planning.context import MEAL_KEY
from minemydinner.planning.validate import Finding, PlanSpec, ValidationResult
from tests.planningfakes import L, ctx_for, make_recipe, rule, self_diner
from tests.recipefakes import seeded  # noqa: F401

# The current plan: two meat dishes, two vegetable dishes and a soup.
FACTS = {
    1: DishFacts("宫保鸡丁", ["鸡肉", "花生"], ["荤菜"], "meat dish"),
    2: DishFacts("蒜蓉西兰花", ["西兰花", "大蒜"], ["素菜"], "vegetable dish"),
    3: DishFacts("番茄蛋汤", ["番茄", "鸡蛋"], ["汤"], "soup"),
    4: DishFacts("红烧肉", ["猪肉"], ["荤菜"], "meat dish"),
    5: DishFacts("清炒菠菜", ["菠菜"], ["素菜"], "vegetable dish"),
}
PLAN = [1, 2, 3, 4, 5]
SPEC_BEFORE = {
    "mode": "shopping",
    "dishes": 4,
    "soups": 1,
    "meat_dishes": 2,
    "vegetable_dishes": 2,
    "meat_bound": "exact",
    "vegetable_bound": "exact",
    "must_use": [],
}


def _spec(**over) -> PlanSpec:
    return PlanSpec.model_validate({**SPEC_BEFORE, **over})


LIBRARY = {
    "辣子鸡": NamedLookup(status="found", recipe_id=50, title="辣子鸡", blocked=None, dish_type="meat dish"),
    "凉拌黄瓜": NamedLookup(
        status="found", recipe_id=51, title="凉拌黄瓜", blocked=None, dish_type="vegetable dish"
    ),
    "佛跳墙": NamedLookup(status="missing", recipe_id=None, title="佛跳墙", blocked=None),
    "鸡丁": NamedLookup(status="ambiguous", recipe_id=None, title="鸡丁", blocked=None),
    "酱焖羊蝎子": NamedLookup(
        status="found",
        recipe_id=52,
        title="酱焖羊蝎子",
        blocked="time_limit",
        dish_type="meat dish",
        time_min=75,
    ),
    "清炒菠菜": NamedLookup(
        status="found", recipe_id=5, title="清炒菠菜", blocked=None, dish_type="vegetable dish"
    ),
}


def _lookup(name: str) -> NamedLookup:
    return LIBRARY.get(name) or NamedLookup(status="missing", recipe_id=None, title=name, blocked=None)


def _frame(
    reading=None, *, locked=(), breaking=None, spec=None, message="", plan=PLAN, facts=FACTS, **kw
):
    return build_frame(
        list(plan),
        set(locked),
        facts,
        SPEC_BEFORE,
        spec or _spec(),
        reading,
        breaking or {},
        _lookup,
        message,
        **kw,
    )


def _change(**fields) -> FollowUpReading:
    return FollowUpReading(kind="change", **fields)


def _remove(dish_id, mode="drop", quote="", with_name="") -> ReadRemoval:
    return ReadRemoval(id=dish_id, mode=mode, quote=quote, with_name=with_name)


def test_named_drop_and_named_replace():
    frame = _frame(
        _change(remove=[_remove(3, "drop", "汤"), _remove(2, "replace", "西兰花")]),
        message="汤不要了，西兰花换一个",
    )
    assert frame.drops == [3]
    assert frame.slots == [Slot(kind="replace", dish_type="vegetable dish", replaces=2)]
    assert frame.slots[0].reason == "asked"
    assert frame.keep == [1, 4, 5]
    assert frame.choices == [] and frame.placed == [] and frame.missing == []
    assert frame.empty_plan is False and frame.ignored == []


def test_replace_with_a_named_library_dish_is_placed_without_a_slot():
    frame = _frame(
        _change(remove=[_remove(1, "replace", "宫保鸡丁", "辣子鸡")]),
        message="把宫保鸡丁换成辣子鸡",
    )
    assert frame.placed == [50]
    assert frame.drops == [1]
    assert frame.slots == []
    assert frame.keep == [2, 3, 4, 5]


def test_a_named_replacement_of_another_type_is_placed():
    frame = _frame(
        _change(remove=[_remove(1, "replace", "宫保鸡丁", "凉拌黄瓜")]),
        message="宫保鸡丁换成凉拌黄瓜",
    )
    assert frame.placed == [51] and frame.drops == [1] and frame.slots == []


def test_a_named_dish_missing_ambiguous_or_over_the_time_limit_keeps_the_replaced_dish():
    for name, reason in (
        ("佛跳墙", "not_in_library"),
        ("鸡丁", "ambiguous"),
        ("酱焖羊蝎子", "over_time_limit"),
    ):
        frame = _frame(
            _change(remove=[_remove(1, "replace", "宫保鸡丁", name)]),
            message=f"把宫保鸡丁换成{name}",
        )
        assert frame.keep == PLAN, name
        assert frame.drops == [] and frame.slots == [] and frame.placed == []
        assert [(m.name, m.reason) for m in frame.missing] == [(name, reason)]
        assert frame.missing[0].detail["replaces"] == 1
    over = _frame(
        _change(remove=[_remove(1, "replace", "宫保鸡丁", "酱焖羊蝎子")]),
        message="把宫保鸡丁换成酱焖羊蝎子",
    ).missing[0]
    assert over.detail["recipe_id"] == 52 and over.detail["time_min"] == 75


def test_a_named_dish_already_in_the_plan_is_ignored():
    frame = _frame(
        _change(remove=[_remove(2, "replace", "西兰花", "清炒菠菜")]),
        message="西兰花换成清炒菠菜",
    )
    assert frame.keep == PLAN and frame.placed == []
    assert frame.ignored[0]["reason"] == "already_in_plan"


def test_a_locked_dish_goes_only_with_a_quote_that_points_at_it():
    allowed = _frame(
        _change(remove=[_remove(1, "drop", "宫保鸡丁")]), locked={1}, message="宫保鸡丁不要了"
    )
    assert allowed.drops == [1] and allowed.ignored == []

    # The quote is not in the message.
    absent = _frame(_change(remove=[_remove(1, "drop", "鸡丁")]), locked={1}, message="不要了")
    # The quote is in the message but points at another dish.
    other = _frame(_change(remove=[_remove(1, "drop", "汤")]), locked={1}, message="汤不要了")
    for frame in (absent, other):
        assert frame.drops == [] and 1 in frame.keep
        assert frame.ignored == [
            {"id": 1, "reason": "locked_without_quote", "quote": frame.ignored[0]["quote"]}
        ]


def test_a_removal_of_a_dish_not_in_the_plan_is_ignored():
    frame = _frame(_change(remove=[_remove(99, "drop", "鱼")]), message="鱼不要了")
    assert frame.keep == PLAN and frame.drops == []
    assert frame.ignored == [{"id": 99, "reason": "not_in_plan"}]


def test_dishes_that_break_the_new_request_become_same_type_slots_locked_included():
    frame = _frame(
        _change(meal_avoid=["猪肉"]),
        locked={4},
        breaking={4: "meal_avoid", 2: "diner_rule", 3: "time_limit"},
    )
    assert frame.slots == [
        Slot(kind="replace", dish_type="vegetable dish", replaces=2, reason="breaks_rule"),
        Slot(kind="replace", dish_type="soup", replaces=3, reason="asked"),
        Slot(kind="replace", dish_type="meat dish", replaces=4, reason="breaks_rule"),
    ]
    assert frame.keep == [1, 5]


def test_a_breaking_dish_also_named_gets_one_entry():
    dropped = _frame(
        _change(remove=[_remove(4, "drop", "红烧肉")]),
        breaking={4: "meal_avoid"},
        message="红烧肉不要了",
    )
    assert dropped.drops == [4] and dropped.slots == []

    replaced = _frame(
        _change(remove=[_remove(4, "replace", "红烧肉")]),
        breaking={4: "meal_avoid"},
        message="红烧肉换一个",
    )
    assert replaced.slots == [
        Slot(kind="replace", dish_type="meat dish", replaces=4, reason="breaks_rule")
    ]

    named = _frame(
        _change(remove=[_remove(4, "replace", "红烧肉", "辣子鸡")]),
        breaking={4: "meal_avoid"},
        message="红烧肉换成辣子鸡",
    )
    assert named.placed == [50] and named.drops == [4] and named.slots == []

    # The named dish is not found: the dish still breaks the rule, so it gets its slot.
    unfound = _frame(
        _change(remove=[_remove(4, "replace", "红烧肉", "佛跳墙")]),
        breaking={4: "meal_avoid"},
        message="红烧肉换成佛跳墙",
    )
    assert unfound.slots == [
        Slot(kind="replace", dish_type="meat dish", replaces=4, reason="breaks_rule")
    ]
    assert [m.reason for m in unfound.missing] == ["not_in_library"]


def test_a_locked_breaking_dish_named_without_a_quote_still_goes():
    frame = _frame(
        _change(remove=[_remove(4, "replace", "")]), locked={4}, breaking={4: "diner_rule"}
    )
    assert frame.ignored == []
    assert frame.slots == [
        Slot(kind="replace", dish_type="meat dish", replaces=4, reason="breaks_rule")
    ]


def test_a_general_wish_gives_one_choice_over_the_unlocked_dishes():
    frame = _frame(_change(wish="辣一些", wish_general=True), locked={1})
    assert max_general_changes(4) == 2
    assert frame.choices == [
        Choice(dish_type=None, candidates=[2, 3, 4, 5], up_to=2, mode="replace")
    ]
    assert frame.keep == PLAN and frame.slots == []


def test_a_wish_that_points_at_a_dish_gives_no_choice():
    frame = _frame(_change(wish="辣一些", wish_general=False))
    assert frame.choices == []


def test_a_stated_lower_count_gives_a_drop_choice_of_exactly_the_difference():
    facts = {**FACTS, 6: DishFacts("紫菜汤", ["紫菜"], ["汤"], "soup")}
    plan = [*PLAN, 6]
    frame = _frame(
        _change(counts=ReadCounts(soups=1)), plan=plan, facts=facts, spec=_spec(soups=1)
    )
    assert frame.choices == [Choice(dish_type="soup", candidates=[3, 6], up_to=1, mode="drop")]
    assert frame.drops == []

    # One soup is locked: only the other can go, so code drops it and nothing is left to choose.
    locked = _frame(
        _change(counts=ReadCounts(soups=1)),
        plan=plan,
        facts=facts,
        spec=_spec(soups=1),
        locked={3},
    )
    assert locked.choices == [] and locked.drops == [6]


def test_a_lower_side_count_drops_that_side():
    frame = _frame(
        _change(counts=ReadCounts(dishes=3, vegetable_dishes=1)),
        spec=_spec(dishes=3, meat_dishes=None, meat_bound=None, vegetable_dishes=1),
    )
    assert frame.choices == [
        Choice(dish_type="vegetable dish", candidates=[2, 5], up_to=1, mode="drop")
    ]


def test_a_lower_total_with_no_side_lets_the_picker_choose_the_type():
    frame = _frame(
        _change(counts=ReadCounts(dishes=3)),
        spec=_spec(dishes=3, meat_dishes=None, meat_bound=None, vegetable_dishes=None, vegetable_bound=None),
        locked={1},
    )
    assert frame.choices == [Choice(dish_type=None, candidates=[2, 4, 5], up_to=1, mode="drop")]


def test_a_stated_higher_count_adds_slots():
    frame = _frame(_change(counts=ReadCounts(soups=2)), spec=_spec(soups=2))
    assert frame.slots == [Slot(kind="add", dish_type="soup")]


def test_a_replace_into_another_type_by_counts():
    # "把汤换成一个素菜": the soup goes and a vegetable dish comes.
    frame = _frame(
        _change(
            remove=[_remove(3, "replace", "汤")],
            counts=ReadCounts(soups=0, dishes=5, vegetable_dishes=3),
        ),
        spec=_spec(soups=0, dishes=5, vegetable_dishes=3, meat_dishes=None, meat_bound=None),
        message="把汤换成一个素菜",
    )
    assert frame.drops == [3]
    assert frame.slots == [Slot(kind="add", dish_type="vegetable dish")]


def test_adds_by_type_and_by_name():
    frame = _frame(
        _change(
            add=[
                ReadAdd(dish_type="soup", count=1),
                ReadAdd(name="凉拌黄瓜"),
                ReadAdd(name="佛跳墙"),
            ]
        ),
        message="加个汤，再加凉拌黄瓜和佛跳墙",
    )
    assert frame.slots == [Slot(kind="add", dish_type="soup")]
    assert frame.placed == [51]
    assert [(m.name, m.reason, m.detail["replaces"]) for m in frame.missing] == [
        ("佛跳墙", "not_in_library", None)
    ]
    assert frame.keep == PLAN


def test_an_add_of_an_unknown_dish_type_is_ignored():
    frame = _frame(_change(add=[ReadAdd(dish_type="凉菜", count=1), ReadAdd(dish_type=None)]))
    assert frame.slots == []
    assert [item["reason"] for item in frame.ignored] == ["unknown_dish_type", "unknown_dish_type"]


def test_an_uncovered_must_use_ingredient_gives_one_replace_choice():
    frame = _frame(_change(), spec=_spec(must_use=["豆腐"]), locked={1})
    assert frame.choices == [
        Choice(dish_type=None, candidates=[2, 3, 4, 5], up_to=1, mode="replace")
    ]

    covered = _frame(_change(), spec=_spec(must_use=["菠菜"]))
    assert covered.choices == []

    # An add the message asks for can use the ingredient: no dish needs to go.
    with_add = _frame(
        _change(add=[ReadAdd(dish_type="soup", count=1)]), spec=_spec(must_use=["豆腐"])
    )
    assert with_add.choices == []

    # The caller may say which names no dish covers (validation's own rule).
    told = _frame(_change(), spec=_spec(must_use=["菠菜"]), uncovered_must=["菠菜"])
    assert len(told.choices) == 1


def test_a_change_that_leaves_no_dish_sets_empty_plan():
    frame = _frame(
        _change(remove=[_remove(i, "drop", FACTS[i].title) for i in PLAN]),
        message="".join(FACTS[i].title for i in PLAN) + "都不要了",
    )
    assert frame.empty_plan is True
    assert _frame(_change(remove=[_remove(3, "drop", "汤")]), message="汤不要").empty_plan is False


def test_a_button_builds_the_frame_from_the_specs_alone():
    nothing = _frame(None)
    assert nothing.keep == PLAN
    assert nothing.slots == [] and nothing.choices == [] and nothing.drops == []

    fewer = _frame(
        None,
        spec=_spec(dishes=3, meat_dishes=None, meat_bound=None, vegetable_dishes=None, vegetable_bound=None),
        breaking={3: "time_limit"},
    )
    assert fewer.choices == [Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=1, mode="drop")]
    assert fewer.slots == [Slot(kind="replace", dish_type="soup", replaces=3)]


def test_breaking_from_validation():
    result = ValidationResult(
        passed=False,
        violations=[
            Finding(code="H6", recipe_id=1, detail={}),
            Finding(code="H2", recipe_id=1, detail={"diner_key": MEAL_KEY}),
            Finding(code="H2", recipe_id=2, detail={"diner_key": "diner:1"}),
            Finding(code="H2", recipe_id=6, detail={"unknown": ["x"]}),
            Finding(code="H1", recipe_id=3, detail={}),
            Finding(code="H3", recipe_id=4, detail={}),
            Finding(code="H4", detail={}),
        ],
    )
    assert breaking_from(result) == {
        1: "meal_avoid",
        2: "diner_rule",
        6: "diner_rule",
        3: "stock",
        4: "cookware",
    }


SHOPPING = {"mode": "shopping"}


def test_lookup_one_character_off_is_matched_and_two_matches_are_ambiguous(session, seeded):
    ctx = ctx_for(session)
    found = make_recipe(session, "宫保鸡丁", "meat dish", [L("鸡肉")])
    result = lookup_named(session, "宫爆鸡丁", SHOPPING, ctx=ctx)
    assert result.status == "found" and result.recipe_id == found.id
    assert result.title == "宫保鸡丁" and result.blocked is None
    assert result.dish_type == "meat dish" and "鸡肉" in result.ingredient_names

    exact = lookup_named(session, " 宫保鸡丁 ", SHOPPING, ctx=ctx)
    assert exact.status == "found" and exact.recipe_id == found.id

    make_recipe(session, "宫保鸭丁", "meat dish", [L("鸡肉")])
    # One character off from both titles: not placed.
    ambiguous = lookup_named(session, "宫保鹅丁", SHOPPING, ctx=ctx)
    assert ambiguous.status == "ambiguous" and ambiguous.recipe_id is None
    # An exact title still wins over a near one.
    assert lookup_named(session, "宫保鸡丁", SHOPPING, ctx=ctx).recipe_id == found.id

    assert lookup_named(session, "佛跳墙", SHOPPING, ctx=ctx).status == "missing"
    # Two characters off, or a short name one character off, is no match.
    assert lookup_named(session, "宫爆鸭块", SHOPPING, ctx=ctx).status == "missing"
    make_recipe(session, "鸡汤", "soup", [L("鸡肉")])
    assert lookup_named(session, "鱼汤", SHOPPING, ctx=ctx).status == "missing"


def test_lookup_blocked_by_a_hard_condition(session, seeded):
    slow = make_recipe(
        session, "酱焖羊蝎子", "meat dish", [L("小白菜")], time_min=75, time_source="stated"
    )
    ctx = ctx_for(session)
    over = lookup_named(session, "酱焖羊蝎子", {"time_limit_min": 30, **SHOPPING}, ctx=ctx)
    assert (over.status, over.recipe_id, over.blocked, over.time_min) == (
        "found",
        slow.id,
        "time_limit",
        75,
    )
    assert lookup_named(session, "酱焖羊蝎子", {"time_limit_min": 90, **SHOPPING}, ctx=ctx).blocked is None
    # Fridge-only: the missing stock hides it.
    assert lookup_named(session, "酱焖羊蝎子", {}, ctx=ctx).blocked == "stock"

    me = self_diner(session)
    rule(session, me.id, "allergy", allergen="egg")
    make_recipe(session, "蛋饺汤", "soup", [L("鸡蛋")])
    rules_ctx = ctx_for(session, me.id)
    assert lookup_named(session, "蛋饺汤", {}, ctx=rules_ctx).blocked == "diner_rule"

    make_recipe(session, "烤土豆", "vegetable dish", [L("土豆")], cookware=["烤箱"])
    assert lookup_named(session, "烤土豆", {"mode": "shopping"}, ctx=ctx).blocked == "cookware"


def test_a_locked_dish_is_never_moved_by_stock_cookware_or_the_time_limit():
    for reason in ("time_limit", "stock", "cookware"):
        frame = _frame(_change(), locked={4}, breaking={4: reason})
        assert frame.slots == [] and frame.keep == PLAN, reason

    # Named without a quote: the stock break does not excuse the missing quote.
    frame = _frame(
        _change(remove=[_remove(4, "replace", "")]), locked={4}, breaking={4: "stock"}
    )
    assert frame.slots == [] and 4 in frame.keep
    assert frame.ignored == [{"id": 4, "reason": "locked_without_quote", "quote": ""}]

    # An unlocked dish that breaks the time limit is still replaced.
    unlocked = _frame(_change(), breaking={4: "time_limit"})
    assert unlocked.slots == [Slot(kind="replace", dish_type="meat dish", replaces=4)]


def test_only_the_stated_counts_bind_and_the_rest_follows_the_change_list():
    # "add two vegetable dishes and keep one soup": the adds stay.
    adds = _frame(
        _change(add=[ReadAdd(dish_type="vegetable dish", count=2)], counts=ReadCounts(soups=1)),
        spec=_spec(soups=1),
    )
    assert adds.slots == [Slot(kind="add", dish_type="vegetable dish")] * 2
    assert adds.choices == [] and adds.drops == []

    # "drop the soup, three dishes": the soup does not come back.
    fewer = _frame(
        _change(remove=[_remove(3, "drop", "汤")], counts=ReadCounts(dishes=3)),
        spec=_spec(dishes=3, meat_dishes=None, meat_bound=None, vegetable_dishes=None, vegetable_bound=None),
        message="汤不要了，三个菜就够",
    )
    assert fewer.drops == [3]
    assert fewer.slots == []
    assert fewer.choices == [Choice(dish_type=None, candidates=[1, 2, 4, 5], up_to=1, mode="drop")]


def test_a_placed_dish_that_uses_the_must_use_ingredient_needs_no_choice():
    with_tofu = NamedLookup(
        status="found",
        recipe_id=60,
        title="麻婆豆腐",
        blocked=None,
        dish_type="vegetable dish",
        ingredient_names=["豆腐", "牛肉"],
    )
    frame = build_frame(
        PLAN,
        set(),
        FACTS,
        SPEC_BEFORE,
        _spec(must_use=["豆腐"]),
        _change(remove=[_remove(2, "replace", "西兰花", "麻婆豆腐")]),
        {},
        lambda name: with_tofu,
        "西兰花换成麻婆豆腐",
    )
    assert frame.placed == [60] and frame.choices == []
