import json
from dataclasses import replace
from datetime import date, timedelta

from minemydinner.models import Rating
from minemydinner.planning.validate import (
    EXPIRY_SOON_DAYS,
    LOW_RATING,
    H2Override,
    PlanSpec,
    validate_plan,
)
from tests.planningfakes import (
    L,
    ctx_for,
    make_recipe,
    own_cookware,
    rule,
    rule_ref,
    self_diner,
    stock,
)
from tests.recipefakes import by_zh, seeded  # noqa: F401

SPEC = PlanSpec(mode="shopping", dishes=1, soups=0, time_limit_min=None)


def spec(**kwargs):
    return SPEC.model_copy(update=kwargs)


def run(session, ctx, recipes, plan=SPEC, overrides=()):
    return validate_plan(session, ctx, plan, [r.id for r in recipes], overrides)


def codes(findings):
    return [f.code for f in findings]


def set_amount(recipe, name, amount, unit, amount_type):
    for line in recipe.ingredients:
        if line.name == name:
            line.amount, line.unit, line.amount_type = amount, unit, amount_type


def ctx_with_rules(session, *rules, **kwargs):
    return replace(ctx_for(session, **kwargs), rules=tuple(rules))


def allergy(allergen, key="diner:1"):
    return rule_ref("allergy", allergen=allergen, diner_key=key)


# ---- hard constraints ----


def test_h1_fridge_only_reports_missing_per_recipe(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉", role="main"), L("盐", role="seasoning")])
    result = run(session, ctx_for(session), [r], spec(mode="fridge_only"))
    assert [(v.code, v.recipe_id, v.detail) for v in result.violations] == [
        ("H1", r.id, {"missing": ["五花肉"]})
    ]
    assert not result.passed


def test_shopping_mode_skips_h1_but_keeps_h2(session, seeded):
    r = make_recipe(session, "蛋", lines=[L("鸡蛋", role="main")])
    result = run(session, ctx_with_rules(session, allergy("egg")), [r])
    assert codes(result.violations) == ["H2"]


def test_h2_detail_has_rule_target_and_line_but_no_name(session, seeded):
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    result = run(session, ctx_with_rules(session, allergy("egg", "guest:1")), [r])
    (v,) = result.violations
    assert v.recipe_id == r.id
    assert v.detail == {
        "diner_key": "guest:1",
        "rule_type": "allergy",
        "allergen": "egg",
        "ingredient_id": None,
        "text": None,
        "line": "鸡蛋",
    }
    json.dumps(v.detail)


def test_h2_restriction_ingredient_and_text(session, seeded):
    pork = by_zh(session, "猪肉").id
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    ctx = ctx_with_rules(session, rule_ref("dietary restriction", ingredient_id=pork))
    (v,) = run(session, ctx, [r]).violations
    assert (v.code, v.detail["ingredient_id"], v.detail["rule_type"]) == (
        "H2",
        pork,
        "dietary restriction",
    )
    ctx = ctx_with_rules(session, rule_ref("dietary restriction", text="五花"))
    assert run(session, ctx, [r]).violations[0].detail["text"] == "五花"


def test_restriction_on_parent_rejects_child_dish(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    ctx = ctx_with_rules(
        session, rule_ref("dietary restriction", ingredient_id=by_zh(session, "猪肉").id)
    )
    assert codes(run(session, ctx, [r]).violations) == ["H2"]


def test_variant_is_checked_like_any_recipe(session, seeded):
    base = make_recipe(session, "炒蛋", lines=[L("鸡蛋")])
    variant = make_recipe(session, "炒花生", lines=[L("花生")])
    variant.parent_recipe_id = base.id
    session.flush()
    result = run(session, ctx_with_rules(session, allergy("peanut")), [variant])
    assert [(v.code, v.recipe_id) for v in result.violations] == [("H2", variant.id)]


def test_confirmed_override_covers_only_that_diner_and_rule(session, seeded):
    r = make_recipe(session, "蛋炒花生", lines=[L("鸡蛋"), L("花生")])
    ctx = ctx_with_rules(session, allergy("egg"), allergy("peanut"))
    override = H2Override(recipe_id=r.id, diner_key="diner:1", allergen="egg")
    result = run(session, ctx, [r], overrides=[override])
    assert [v.detail["allergen"] for v in result.violations if v.code == "H2"] == ["peanut"]


def test_override_for_other_diner_recipe_or_target_does_not_apply(session, seeded):
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    other = make_recipe(session, "别的", lines=[L("鸡蛋")])
    ctx = ctx_with_rules(session, allergy("egg"))
    wrong = [
        H2Override(recipe_id=r.id, diner_key="diner:2", allergen="egg"),
        H2Override(recipe_id=other.id, diner_key="diner:1", allergen="egg"),
        H2Override(recipe_id=r.id, diner_key="diner:1", allergen="soy"),
        H2Override(recipe_id=r.id, diner_key="diner:1", text="egg"),
        H2Override(recipe_id=r.id, diner_key="diner:1", unknown_allergens=True),
    ]
    for override in wrong:
        assert codes(run(session, ctx, [r], overrides=[override]).violations) == ["H2"]


def test_unknown_allergen_dish_fails_h2_only_with_an_allergy(session, seeded):
    r = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    result = run(session, ctx_with_rules(session, allergy("egg")), [r])
    assert [(v.code, v.detail) for v in result.violations] == [("H2", {"unknown": ["某种酱"]})]
    restriction = rule_ref("dietary restriction", ingredient_id=by_zh(session, "猪肉").id)
    assert run(session, ctx_with_rules(session, restriction), [r]).violations == []
    assert run(session, ctx_for(session), [r]).violations == []


def test_unknown_allergen_override_accepts_block_but_not_conflicts(session, seeded):
    r = make_recipe(session, "神秘蛋", lines=[L("某种酱", linked=False), L("鸡蛋")])
    ctx = ctx_with_rules(session, allergy("egg"))
    override = H2Override(recipe_id=r.id, diner_key="diner:1", unknown_allergens=True)
    result = run(session, ctx, [r], overrides=[override])
    assert [v.detail["allergen"] for v in result.violations] == ["egg"]
    only_unknown = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    override = H2Override(recipe_id=only_unknown.id, diner_key="diner:1", unknown_allergens=True)
    assert run(session, ctx, [only_unknown], overrides=[override]).violations == []


def test_h3_missing_cookware(session, seeded):
    own_cookware(session, "炒锅")
    r = make_recipe(session, "蒸", cookware=["炒锅", "蒸锅"])
    (v,) = run(session, ctx_for(session), [r]).violations
    assert (v.code, v.recipe_id) == ("H3", r.id)
    assert v.detail["missing"] == ["蒸锅"]
    json.dumps(v.detail)


def test_h4_counts_dishes_and_soups(session, seeded):
    meat = make_recipe(session, "肉", dish_type="meat dish")
    veg = make_recipe(session, "菜", dish_type="vegetable dish")
    soup = make_recipe(session, "汤", dish_type="soup")
    ok = run(session, ctx_for(session), [meat, veg, soup], spec(dishes=2, soups=1))
    assert ok.violations == []
    bad = run(session, ctx_for(session), [meat, soup], spec(dishes=2, soups=0))
    (v,) = bad.violations
    assert (v.code, v.recipe_id, v.detail) == (
        "H4",
        None,
        {"dishes": 1, "soups": 1, "wanted": [2, 0]},
    )


def test_staple_dish_is_an_extra_for_h4(session, seeded):
    meat = make_recipe(session, "肉", dish_type="meat dish")
    rice = make_recipe(session, "饭", dish_type="staple food")
    result = run(session, ctx_for(session), [meat, rice], spec(dishes=1, soups=0))
    assert "H4" not in codes(result.violations)


def test_nonexistent_and_duplicate_ids_fail_h5(session, seeded):
    r = make_recipe(session, "肉", dish_type="meat dish", lines=[L("五花肉", role="main")])
    result = validate_plan(
        session, ctx_for(session), spec(mode="fridge_only"), [r.id, r.id, 999999, 999999, 888888]
    )
    h5 = [(v.recipe_id, v.detail["reason"]) for v in result.violations if v.code == "H5"]
    assert h5 == [
        (r.id, "duplicate"),
        (999999, "not_found"),
        (999999, "duplicate"),
        (888888, "not_found"),
    ]
    # Unknown ids are dropped from every other check; the real recipe is checked once.
    assert [v.recipe_id for v in result.violations if v.code == "H1"] == [r.id]
    assert len([v for v in result.violations if v.code == "H3"]) == 0


def test_h6_ignores_estimated_times_but_s7_warns(session, seeded):
    stated = make_recipe(session, "a", time_min=90, time_source="stated")
    user = make_recipe(session, "b", time_min=70, time_source="user")
    est = make_recipe(session, "c", time_min=200, time_source="estimated")
    plan = spec(dishes=3, time_limit_min=60)
    result = run(session, ctx_for(session), [stated, user, est], plan)
    h6 = [v for v in result.violations if v.code == "H6"]
    assert [v.recipe_id for v in h6] == [stated.id, user.id]
    assert h6[0].detail == {"time_min": 90, "limit": 60, "time_source": "stated"}
    assert (est.id, {"kind": "estimated", "time_min": 200}) in [
        (w.recipe_id, w.detail) for w in result.warnings if w.code == "S7"
    ]


def test_h6_needs_a_time_limit(session, seeded):
    r = make_recipe(session, "a", time_min=900, time_source="stated")
    assert run(session, ctx_for(session), [r]).violations == []


# ---- soft warnings ----


def test_s1_g_line_exceeding_stock_grams(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉", role="main")])
    set_amount(r, "五花肉", 500, "g", "exact")
    stock(session, "五花肉", level="plenty", grams=300)
    (w,) = run(session, ctx_for(session), [r]).warnings
    assert (w.code, w.recipe_id) == ("S1", r.id)
    assert w.detail["name"] == "五花肉"
    assert (w.detail["needed"], w.detail["have"], w.detail["unit"]) == (500, 300, "g")
    json.dumps(w.detail)


def test_s1_piece_line_vs_stock_count_and_unit_mismatch(session, seeded):
    stock(session, "鸡蛋", count=2)
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    set_amount(r, "鸡蛋", 4, "piece", "count")
    assert codes(run(session, ctx_for(session), [r]).warnings) == ["S1"]
    r2 = make_recipe(session, "蛋2", lines=[L("鸡蛋")])
    set_amount(r2, "鸡蛋", 4, "stalk", "count")
    assert "S1" not in codes(run(session, ctx_for(session), [r2]).warnings)
    r3 = make_recipe(session, "蛋3", lines=[L("鸡蛋")])
    set_amount(r3, "鸡蛋", 400, "g", "exact")
    assert "S1" not in codes(run(session, ctx_for(session), [r3]).warnings)
    r4 = make_recipe(session, "蛋4", lines=[L("鸡蛋")])
    set_amount(r4, "鸡蛋", 2, "piece", "count")
    assert "S1" not in codes(run(session, ctx_for(session), [r4]).warnings)


def test_s1_uses_the_lines_own_ingredient_stock_row(session, seeded):
    r = make_recipe(session, "炒", lines=[L("猪肉", role="main")])
    set_amount(r, "猪肉", 500, "g", "exact")
    stock(session, "五花肉", level="plenty", grams=100)
    assert "S1" not in codes(run(session, ctx_for(session), [r]).warnings)


def test_s2_dislike_names_diner_and_line(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    dislike = rule_ref("dislike", ingredient_id=by_zh(session, "猪肉").id, diner_key="diner:7")
    (w,) = run(session, ctx_with_rules(session, dislike), [r]).warnings
    assert (w.code, w.recipe_id, w.detail) == (
        "S2",
        r.id,
        {"diner_key": "diner:7", "line": "五花肉"},
    )


def test_s3_expiring_stock_unused(session, seeded):
    soon = date.today() + timedelta(days=EXPIRY_SOON_DAYS)
    stock(session, "鸡蛋", count=2, expires_on=soon)
    stock(session, "黄瓜", count=2, expires_on=date.today() - timedelta(days=1))
    stock(session, "番茄", count=2, expires_on=soon + timedelta(days=1))
    r = make_recipe(session, "炒", lines=[L("五花肉")])
    result = run(session, ctx_for(session), [r])
    s3 = [w for w in result.warnings if w.code == "S3"]
    assert sorted(w.detail["ingredient_id"] for w in s3) == sorted(
        [by_zh(session, "鸡蛋").id, by_zh(session, "黄瓜").id]
    )
    assert all(w.recipe_id is None for w in s3)
    json.dumps([w.detail for w in s3])


def test_s3_used_directly_or_through_hierarchy_is_not_warned(session, seeded):
    today = date.today()
    stock(session, "鸡蛋", count=2, expires_on=today)
    stock(session, "五花肉", level="plenty", expires_on=today)
    r = make_recipe(session, "炒", lines=[L("鸡蛋"), L("猪肉")])
    assert "S3" not in codes(run(session, ctx_for(session), [r]).warnings)


def test_s4_low_rated(session, seeded):
    r = make_recipe(session, "差")
    good = make_recipe(session, "好")
    session.add_all([Rating(recipe_id=r.id, score=LOW_RATING), Rating(recipe_id=good.id, score=3)])
    session.flush()
    result = run(session, ctx_for(session), [r, good], spec(dishes=2))
    assert [(w.code, w.recipe_id, w.detail) for w in result.warnings if w.code == "S4"] == [
        ("S4", r.id, {"score": LOW_RATING})
    ]


def test_s5_unbalanced_needs_two_counted_dishes(session, seeded):
    m1 = make_recipe(session, "肉1", dish_type="meat dish")
    m2 = make_recipe(session, "肉2", dish_type="meat dish")
    v1 = make_recipe(session, "菜1", dish_type="vegetable dish")
    soup = make_recipe(session, "汤", dish_type="soup")
    plan = spec(dishes=2, soups=1)
    assert "S5" in codes(run(session, ctx_for(session), [m1, m2, soup], plan).warnings)
    assert "S5" not in codes(run(session, ctx_for(session), [m1, v1, soup], plan).warnings)
    assert "S5" not in codes(run(session, ctx_for(session), [m1, soup], spec(soups=1)).warnings)


def test_s6_vague_line_with_low_own_stock(session, seeded):
    r = make_recipe(session, "炒", lines=[L("五花肉"), L("猪肉")])
    stock(session, "五花肉", level="low")
    stock(session, "猪肉", level="plenty")
    (w,) = [w for w in run(session, ctx_for(session), [r]).warnings if w.code == "S6"]
    assert (w.recipe_id, w.detail["name"]) == (r.id, "五花肉")
    exact = make_recipe(session, "炒2", lines=[L("五花肉")])
    set_amount(exact, "五花肉", 100, "g", "exact")
    assert "S6" not in codes(run(session, ctx_for(session), [exact]).warnings)


def test_s7_unknown_estimated_and_total(session, seeded):
    unknown = make_recipe(session, "a")
    est = make_recipe(session, "b", time_min=30, time_source="estimated")
    known = make_recipe(session, "c", time_min=50, time_source="stated")
    result = run(
        session, ctx_for(session), [unknown, est, known], spec(dishes=3, time_limit_min=50)
    )
    s7 = [(w.recipe_id, w.detail) for w in result.warnings if w.code == "S7"]
    assert (unknown.id, {"kind": "unknown"}) in s7
    assert (est.id, {"kind": "estimated", "time_min": 30}) in s7
    assert (None, {"kind": "total", "total_min": 80, "limit": 50}) in s7


def test_s7_total_must_exceed_one_and_a_half_times(session, seeded):
    a = make_recipe(session, "a", time_min=40, time_source="stated")
    b = make_recipe(session, "b", time_min=35, time_source="stated")
    result = run(session, ctx_for(session), [a, b], spec(dishes=2, time_limit_min=50))
    assert "S7" not in codes(result.warnings)
    no_limit = run(session, ctx_for(session), [a, b], spec(dishes=2))
    assert "S7" not in codes(no_limit.warnings)


def test_s8_unconfirmed_diners_and_unrecognized_lines(session, seeded):
    r = make_recipe(session, "神秘", lines=[L("某种酱", linked=False)])
    result = run(session, ctx_for(session, unconfirmed_diners=2), [r])
    s8 = [(w.recipe_id, w.detail) for w in result.warnings if w.code == "S8"]
    assert (None, {"unconfirmed_diners": 2}) in s8
    assert (r.id, {"unrecognized": ["某种酱"]}) in s8
    clean = run(session, ctx_for(session), [make_recipe(session, "ok")])
    assert "S8" not in codes(clean.warnings)


def test_passed_ignores_warnings(session, seeded):
    r = make_recipe(session, "神秘", lines=[L("某种酱", linked=False)])
    result = run(session, ctx_for(session), [r])
    assert result.passed and result.warnings


def test_rules_loaded_from_database_flow_through(session, seeded):
    diner = self_diner(session)
    rule(session, diner.id, "allergy", allergen="egg")
    r = make_recipe(session, "蛋", lines=[L("鸡蛋")])
    result = run(session, ctx_for(session, diner.id), [r])
    assert result.violations[0].detail["diner_key"] == f"diner:{diner.id}"
