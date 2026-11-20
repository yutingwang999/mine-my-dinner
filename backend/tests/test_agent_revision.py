"""Revision checks (plan 3b-1 spec 5.2, 5.3): pure code, no model and no database."""

from minemydinner.agent.revision import (
    DishFacts,
    RemovedDish,
    RevisionProposal,
    changed_text,
    check_revision,
    max_general_changes,
    quote_matches,
    revised_ids,
    revision_spec,
)

FACTS = {
    1: DishFacts("鱼香茄子", ["茄子", "猪肉"], []),
    2: DishFacts("番茄炒蛋", ["番茄", "鸡蛋"], []),
    3: DishFacts("韭菜炒鸡蛋", ["韭菜", "鸡蛋"], []),
    4: DishFacts("玉米排骨汤", ["玉米", "排骨"], ["汤"]),
    5: DishFacts("清炒西兰花", ["西兰花"], []),
    6: DishFacts("红烧肉", ["五花肉"], []),
}


def _plan(ids, locked=(), conflicts=(), overrides=()):
    veg, meat = "vegetable dish", "meat dish"
    types = {1: veg, 2: veg, 3: veg, 4: "soup", 5: veg, 6: meat}
    return {
        "recipes": [{"id": i, "title": FACTS[i].title, "dish_type": types[i]} for i in ids],
        "spec": {"mode": "fridge_only", "dishes": 3, "soups": 0},
        "locked": list(locked),
        "edit": {
            "overrides": [{"recipe_id": i} for i in overrides],
            "conflicts": [{"recipe_id": i} for i in conflicts],
        },
    }


def _proposal(target, remove=(), add=(), scope_quote="", excepted=()):
    return RevisionProposal(
        target=target,
        remove=[RemovedDish(id=i, quote=q) for i, q in remove],
        add=list(add),
        scope_quote=scope_quote,
        excepted=list(excepted),
        note="ok",
    )


def _codes(problems):
    return [p.code for p in problems]


def test_max_general_changes():
    assert [max_general_changes(n) for n in range(6)] == [0, 1, 1, 2, 2, 3]


def test_quote_matches():
    assert quote_matches("茄子这道", "鱼香茄子", [], [])
    assert not quote_matches("土豆这道", "鱼香茄子", ["猪肉"], [])
    assert quote_matches("那道汤", "玉米排骨汤", [], ["汤"])
    assert quote_matches("不要猪肉", "鱼香茄子", ["猪肉"], [])
    assert not quote_matches("", "鱼香茄子", ["猪肉"], ["汤"])


def test_named_quote_must_be_in_message_and_match():
    plan = _plan([1, 2, 4])
    message = "茄子这道不要，换一个别的"
    ok = _proposal("named", remove=[(1, "茄子这道")], add=[5])
    assert check_revision(plan, ok, message, FACTS) == []
    bad = _proposal("named", remove=[(1, "土豆这道")], add=[5])
    assert _codes(check_revision(plan, bad, message, FACTS)) == ["R1"]
    wrong = _proposal("named", remove=[(2, "茄子这道")], add=[5])
    assert _codes(check_revision(plan, wrong, message, FACTS)) == ["R2"]
    empty = _proposal("named", remove=[(1, "")], add=[5])
    assert _codes(check_revision(plan, empty, message, FACTS)) == ["R2"]


def test_locked_dish_needs_named_quote():
    plan = _plan([1, 3, 4], locked=[3])
    message = "韭菜炒鸡蛋这道不要了"
    general = _proposal("general", remove=[(3, "")])
    assert _codes(check_revision(plan, general, message, FACTS)) == ["R3"]
    named = _proposal("named", remove=[(3, "韭菜炒鸡蛋这道")])
    assert check_revision(plan, named, message, FACTS) == []


def test_scope_removes_locked_but_not_excepted():
    plan = _plan([1, 3, 4], locked=[3])
    message = "除了玉米这道剩下全换"
    ok = _proposal("scope", remove=[(1, ""), (3, "")], scope_quote="除了玉米这道剩下全换", excepted=[4])
    assert check_revision(plan, ok, message, FACTS) == []
    bad = _proposal(
        "scope", remove=[(1, ""), (3, ""), (4, "")], scope_quote="除了玉米这道剩下全换", excepted=[4]
    )
    assert _codes(check_revision(plan, bad, message, FACTS)) == ["R5"]
    missing = _proposal("scope", remove=[(1, "")], scope_quote="全部换掉")
    assert _codes(check_revision(plan, missing, message, FACTS)) == ["R1"]
    blank = _proposal("scope", remove=[(1, "")], scope_quote="")
    assert _codes(check_revision(plan, blank, message, FACTS)) == ["R1"]


def test_general_wish_limit_excludes_open_conflicts():
    plan = _plan([1, 2, 3, 4, 5], conflicts=[1])
    ok = _proposal("general", remove=[(1, ""), (2, ""), (3, ""), (4, "")])
    assert check_revision(plan, ok, "想要辣一些的", FACTS) == []
    over = _proposal("general", remove=[(1, ""), (2, ""), (3, ""), (4, ""), (5, "")])
    assert _codes(check_revision(plan, over, "想要辣一些的", FACTS)) == ["R4"]


def test_conflicted_dish_needs_no_quote_under_named():
    plan = _plan([1, 2], conflicts=[1], locked=[1])
    proposal = _proposal("named", remove=[(1, "")])
    assert check_revision(plan, proposal, "换一下", FACTS) == []


def test_unclear_changes_nothing():
    plan = _plan([1, 2])
    assert check_revision(plan, _proposal("unclear"), "那个不要", FACTS) == []
    assert _codes(check_revision(plan, _proposal("unclear", remove=[(1, "")]), "x", FACTS)) == ["R6"]
    assert _codes(check_revision(plan, _proposal("unclear", add=[5]), "x", FACTS)) == ["R6"]


def test_ids_must_fit_the_plan():
    plan = _plan([1, 2])
    gone = _proposal("general", remove=[(9, "")])
    assert _codes(check_revision(plan, gone, "x", FACTS)) == ["R6"]
    dup = _proposal("general", add=[2])
    assert _codes(check_revision(plan, dup, "x", FACTS)) == ["R6"]


def test_revised_ids():
    plan = _plan([1, 2, 4])
    proposal = _proposal("named", remove=[(2, "番茄")], add=[5, 6])
    assert revised_ids(plan, proposal) == [1, 4, 5, 6]


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


def test_revision_spec_no_dishes_uses_request_counts():
    plan = _plan([])
    plan["spec"]["dishes"] = 3
    spec = revision_spec(plan, {"dishes": 3}, {"dishes": 3})
    assert spec.dishes == 3


def test_changed_text_both_languages():
    assert changed_text(["A"], ["B"], [], "zh") == "把A换成了B。"
    assert changed_text(["A"], ["B"], [], "en") == "Replaced A with B."
    assert changed_text([], ["B", "C"], [], "zh") == "加了B、C。"
    assert changed_text(["A", "D"], [], [], "zh") == "去掉了A、D。"
    assert changed_text([], ["B"], ["X"], "zh") == "加了B。X是你手动加的，没有改动。"
    assert changed_text(["A"], ["B"], ["X"], "en") == (
        "Replaced A with B. X was added by you, so it was left as is."
    )


def test_count_cut_is_outside_the_general_limit():
    plan = _plan([1, 2, 3, 4, 5, 6])  # 6 unlocked: the plain cap is 3
    request = {"dishes": 3, "soups": 1}
    spec = revision_spec(plan, {"dishes": 5, "soups": 1}, request)  # total 4: 2 removals exempt, cap 5
    four = _proposal("general", remove=[(1, ""), (2, ""), (3, ""), (5, "")])
    assert _codes(check_revision(plan, four, "只要两个菜", FACTS)) == ["R4"]
    assert check_revision(plan, four, "只要两个菜", FACTS, spec=spec) == []
    five = _proposal("general", remove=[(i, "") for i in (1, 2, 3, 4, 5, 6)])
    assert _codes(check_revision(plan, five, "只要两个菜", FACTS, spec=spec)) == ["R4"]


def test_count_cut_never_removes_a_locked_dish_under_general():
    plan = _plan([1, 2, 3, 4], locked=[3])
    spec = revision_spec(plan, {"dishes": 3}, {"dishes": 1})
    proposal = _proposal("general", remove=[(1, ""), (2, ""), (3, "")])
    assert _codes(check_revision(plan, proposal, "只要一个菜", FACTS, spec=spec)) == ["R3"]


def test_duplicate_ids_are_r6():
    plan = _plan([1, 2, 3, 4])
    twice = _proposal("general", remove=[(1, ""), (1, "")])
    assert _codes(check_revision(plan, twice, "x", FACTS)) == ["R6"]
    added = _proposal("general", add=[5, 5])
    assert _codes(check_revision(plan, added, "x", FACTS)) == ["R6"]


def test_excepted_dish_with_open_conflict_may_go():
    plan = _plan([1, 4], conflicts=[4])
    proposal = _proposal(
        "scope", remove=[(1, ""), (4, "")], scope_quote="全换", excepted=[4]
    )
    assert check_revision(plan, proposal, "全换", FACTS) == []


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
