from datetime import date, datetime, timedelta, timezone

import pytest

from minemydinner.dictionary.service import set_personal_aliases
from minemydinner.models import MealPlan, MealPlanItem, Rating
from minemydinner.planning.search import (
    ExcludedRecipe,
    recipe_detail,
    search_recipes,
    unknown_allergen_exclusions,
)
from tests.planningfakes import (
    FakeEmbedClient,
    L,
    all_failed_client,
    blend_vector,
    ctx_for,
    make_recipe,
    over_cap_client,
    rule,
    self_diner,
    stock,
    unit_vector,
)
from tests.recipefakes import by_zh, seeded  # noqa: F401


@pytest.fixture
def fake_embed():
    return FakeEmbedClient()


def ids(result):
    return [h.id for h in result.hits]


def test_search_excludes_allergen_dish_and_lists_unknown(session, seeded, fake_embed):
    make_recipe(session, "番茄炒蛋", lines=[L("鸡蛋")])
    odd = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    ok = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    me = self_diner(session).id
    rule(session, me, "allergy", allergen="egg")
    result = search_recipes(session, fake_embed, ctx_for(session, me))
    assert ids(result) == [ok.id]
    assert [e.id for e in result.left_out_unknown_allergens] == [odd.id]
    assert result.left_out_unknown_allergens[0].lines == ["某种酱"]
    assert not result.semantic_paused


def test_dislike_does_not_exclude(session, seeded, fake_embed):
    r = make_recipe(session, "芹菜炒肉", lines=[L("芹菜")])
    me = self_diner(session).id
    rule(session, me, "dislike", ingredient="芹菜")
    result = search_recipes(session, fake_embed, ctx_for(session, me))
    assert ids(result) == [r.id]


def test_max_time_drops_only_stated_or_user_times(session, seeded, fake_embed):
    stated = make_recipe(session, "慢炖", lines=[L("小白菜")], time_min=90, time_source="stated")
    user = make_recipe(session, "慢炖2", lines=[L("小白菜")], time_min=90, time_source="user")
    est = make_recipe(session, "慢炖3", lines=[L("小白菜")], time_min=90, time_source="estimated")
    unknown = make_recipe(session, "不知道", lines=[L("小白菜")])
    quick = make_recipe(session, "快手", lines=[L("小白菜")], time_min=10, time_source="stated")
    result = search_recipes(session, fake_embed, ctx_for(session), max_time=30)
    assert set(ids(result)) == {est.id, unknown.id, quick.id}
    assert stated.id not in ids(result) and user.id not in ids(result)


def test_tags_rank_first_but_do_not_filter(session, seeded, fake_embed):
    plain = make_recipe(session, "白菜", lines=[L("小白菜")])
    tagged = make_recipe(session, "土豆丝", lines=[L("土豆")])
    tagged.tags = ["Quick-Fix"]
    titled = make_recipe(session, "快手汤", lines=[L("小白菜")])
    session.flush()
    result = search_recipes(session, fake_embed, ctx_for(session), tags=["quick"])
    assert ids(result)[0] == tagged.id
    assert set(ids(result)) == {plain.id, tagged.id, titled.id}
    by_title = search_recipes(session, fake_embed, ctx_for(session), tags=["快手"])
    assert ids(by_title)[0] == titled.id
    assert len(by_title.hits) == 3


def test_must_use_accepts_child_lines(session, seeded, fake_embed):
    sauce = make_recipe(session, "红烧", lines=[L("生抽", role="seasoning")])
    make_recipe(session, "清炒", lines=[L("小白菜")])
    result = search_recipes(session, fake_embed, ctx_for(session), must_use=["酱油"])
    assert ids(result) == [sauce.id]


def test_must_use_needs_every_name(session, seeded, fake_embed):
    both = make_recipe(session, "白菜土豆", lines=[L("小白菜"), L("土豆")])
    make_recipe(session, "白菜", lines=[L("小白菜")])
    result = search_recipes(session, fake_embed, ctx_for(session), must_use=["小白菜", "土豆"])
    assert ids(result) == [both.id]


def test_exclude_recent_days_uses_saved_plans(session, seeded, fake_embed):
    recent = make_recipe(session, "近期", lines=[L("小白菜")])
    old = make_recipe(session, "很久以前", lines=[L("小白菜")])
    fresh = make_recipe(session, "没做过", lines=[L("小白菜")])
    now = datetime.now(timezone.utc)
    for recipe, age in ((recent, 2), (old, 20)):
        plan = MealPlan(thread_id="t", constraints={}, confirmed_at=now - timedelta(days=age))
        session.add(plan)
        session.flush()
        session.add(MealPlanItem(plan_id=plan.id, recipe_id=recipe.id, recipe_title=recipe.title))
    session.flush()
    result = search_recipes(session, fake_embed, ctx_for(session), exclude_recent_days=7)
    assert set(ids(result)) == {old.id, fresh.id}


def test_no_query_orders_by_rating_then_newest(session, seeded, fake_embed):
    a = make_recipe(session, "甲", lines=[L("小白菜")])
    b = make_recipe(session, "乙", lines=[L("小白菜")])
    c = make_recipe(session, "丙", lines=[L("小白菜")])
    session.add(Rating(recipe_id=a.id, score=5))
    session.add(Rating(recipe_id=b.id, score=3))
    session.flush()
    result = search_recipes(session, fake_embed, ctx_for(session))
    assert ids(result) == [a.id, b.id, c.id]
    assert [h.rating for h in result.hits] == [5, 3, None]
    assert ids(search_recipes(session, fake_embed, ctx_for(session), limit=2)) == [a.id, b.id]


def test_limit_applies_after_exclusion(session, seeded, fake_embed):
    for i in range(3):
        make_recipe(session, f"蛋{i}", lines=[L("鸡蛋")])
    ok = make_recipe(session, "白菜", lines=[L("小白菜")])
    me = self_diner(session).id
    rule(session, me, "allergy", allergen="egg")
    result = search_recipes(session, fake_embed, ctx_for(session, me), limit=1)
    assert ids(result) == [ok.id]


def test_query_ranks_by_vector_distance(session, seeded):
    far = make_recipe(session, "远", lines=[L("小白菜")], embedding=unit_vector(5))
    near = make_recipe(session, "近", lines=[L("小白菜")], embedding=blend_vector(0, 5, 0.1))
    mid = make_recipe(session, "中", lines=[L("小白菜")], embedding=blend_vector(0, 5, 1.0))
    text_only = make_recipe(session, "汤菜", lines=[L("小白菜")])
    make_recipe(session, "无关", lines=[L("土豆")])
    client = FakeEmbedClient(vector=unit_vector(0))
    result = search_recipes(session, client, ctx_for(session), query="菜")
    assert ids(result) == [near.id, mid.id, far.id, text_only.id]
    assert client.embedded == ["菜"] and client.tasks == ["embeddings"]
    assert not result.semantic_paused


@pytest.mark.parametrize("make_client", [over_cap_client, all_failed_client])
def test_embedding_failure_falls_back_to_text_search(session, seeded, make_client):
    make_recipe(session, "土豆丝", lines=[L("土豆")])
    make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    result = search_recipes(session, make_client(), ctx_for(session), query="土豆")
    assert result.semantic_paused and [h.title for h in result.hits] == ["土豆丝"]


def test_text_search_matches_dictionary_alias(session, seeded):
    make_recipe(session, "素菜", lines=[L("土豆")])
    make_recipe(session, "别的", lines=[L("小白菜")])
    result = search_recipes(session, over_cap_client(), ctx_for(session), query="potato")
    assert [h.title for h in result.hits] == ["素菜"]


def test_text_search_matches_personal_alias(session, seeded):
    make_recipe(session, "素菜", lines=[L("土豆")])
    set_personal_aliases(session, by_zh(session, "土豆").id, ["洋芋"], [])
    result = search_recipes(session, over_cap_client(), ctx_for(session), query="洋芋")
    assert [h.title for h in result.hits] == ["素菜"]


def test_text_search_matches_tags(session, seeded):
    r = make_recipe(session, "素菜", lines=[L("土豆")])
    r.tags = ["下饭"]
    session.flush()
    result = search_recipes(session, over_cap_client(), ctx_for(session), query="下饭")
    assert ids(result) == [r.id]


def test_recipe_detail_has_lines_and_no_source_text(session, seeded):
    r = make_recipe(
        session,
        "红烧",
        lines=[L("生抽", role="seasoning"), L("某酱", linked=False)],
        cookware=["炒锅"],
        time_min=20,
        time_source="stated",
        servings=2,
    )
    r.source_text = "full method"
    r.steps_summary = "steps"
    session.flush()
    detail = recipe_detail(session, r.id)
    assert detail["title"] == "红烧" and detail["time_min"] == 20
    assert detail["time_source"] == "stated" and detail["steps_summary"] == "steps"
    assert "source_text" not in detail and "images" not in detail
    assert detail["cookware"] == ["炒锅"]
    first = detail["lines"][0]
    assert first["name"] == "生抽" and first["ingredient_id"] == by_zh(session, "生抽").id
    assert set(first) == {
        "raw", "name", "ingredient_id", "amount", "unit", "amount_type",
        "role", "optional", "choice_group",
    }
    assert detail["lines"][1]["ingredient_id"] is None
    assert recipe_detail(session, r.id + 9999) is None


def test_blank_must_use_is_skipped_and_unknown_names_are_reported(session, seeded, fake_embed):
    r = make_recipe(session, "神秘", lines=[L("某种酱", linked=False)])
    make_recipe(session, "清炒", lines=[L("小白菜")])
    result = search_recipes(session, fake_embed, ctx_for(session), must_use=["  ", "某种酱"])
    assert ids(result) == [r.id]
    assert result.unresolved_must_use == ["某种酱"]
    known = search_recipes(session, fake_embed, ctx_for(session), must_use=["土豆"])
    assert known.unresolved_must_use == []


def test_text_fallback_splits_words_and_ranks_by_matches(session, seeded):
    one = make_recipe(session, "土豆丝", lines=[L("土豆")])
    both = make_recipe(session, "快手土豆", lines=[L("土豆")])
    make_recipe(session, "清炒", lines=[L("小白菜")])
    result = search_recipes(session, over_cap_client(), ctx_for(session), query="土豆 快手")
    assert ids(result) == [both.id, one.id]


def test_dish_type_filter_and_exclude_ids(session, seeded, fake_embed):
    soup_a = make_recipe(session, "番茄汤", dish_type="soup", lines=[L("番茄")])
    soup_b = make_recipe(session, "豆腐汤", dish_type="soup", lines=[L("豆腐")])
    make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    soups = search_recipes(session, fake_embed, ctx_for(session), dish_types=["soup"])
    assert set(ids(soups)) == {soup_a.id, soup_b.id}
    rest = search_recipes(
        session, fake_embed, ctx_for(session), dish_types=["soup"], exclude_ids=[soup_a.id]
    )
    assert ids(rest) == [soup_b.id]


def test_hits_carry_missing_items_and_cookware(session, seeded, fake_embed):
    recipe = make_recipe(
        session,
        "番茄炒蛋",
        lines=[
            L("鸡蛋", role="main"),
            L("番茄", role="main"),
            L("盐", role="seasoning"),
            L("小葱", optional=True),
        ],
        cookware=["炒锅"],
    )
    stock(session, "鸡蛋", count=4, expires_on=date.today() + timedelta(days=1))
    stock(session, "土豆", count=2, expires_on=date.today() + timedelta(days=1))
    stock(session, "豆腐", count=1, expires_on=date.today() + timedelta(days=30))
    me = self_diner(session)
    me.display_name = "Zhang Wei"
    rule(session, me.id, "dislike", ingredient="番茄")
    result = search_recipes(session, fake_embed, ctx_for(session, me.id))
    hit = next(h for h in result.hits if h.id == recipe.id)
    assert hit.missing == ["番茄"]
    assert hit.missing_cookware == ["炒锅"]
    assert hit.uses_expiring == ["鸡蛋"]
    assert hit.dislikes == ["Diner 1: 番茄"]
    assert "Zhang Wei" not in repr(result)


def test_fewer_missing_ranks_first_after_relevance(session, seeded):
    stock(session, "土豆", count=3)
    stock(session, "鸡蛋", count=3)
    two_missing = make_recipe(session, "土豆烧肉", lines=[L("土豆"), L("猪肉"), L("青椒")])
    none_missing = make_recipe(session, "土豆炒蛋", lines=[L("土豆"), L("鸡蛋")])
    one_missing = make_recipe(session, "土豆丝", lines=[L("土豆"), L("青椒")])
    unmatched = make_recipe(session, "蒸蛋", lines=[L("鸡蛋")])
    client = over_cap_client()
    # No query: fewer missing lines first, then the usual rating and recency order.
    plain = search_recipes(session, client, ctx_for(session))
    assert ids(plain) == [unmatched.id, none_missing.id, one_missing.id, two_missing.id]
    # A text query: the best match stays first; equal matches go by missing lines.
    ranked = search_recipes(session, client, ctx_for(session), query="土豆 烧肉")
    assert ids(ranked) == [two_missing.id, none_missing.id, one_missing.id]


def test_ties_break_by_expiring_then_dislike(session, seeded, fake_embed):
    stock(session, "鸡蛋", count=3, expires_on=date.today() + timedelta(days=2))
    stock(session, "豆腐", count=3)
    stock(session, "黄瓜", count=3)
    # Created oldest first, so recency alone would give the reverse order.
    expiring = make_recipe(session, "蒸蛋", lines=[L("鸡蛋")])
    estimated = make_recipe(
        session, "豆腐估计", lines=[L("豆腐")], time_min=10, time_source="estimated"
    )
    firm = make_recipe(session, "豆腐", lines=[L("豆腐")], time_min=10, time_source="stated")
    disliked = make_recipe(session, "黄瓜", lines=[L("黄瓜")])
    me = self_diner(session).id
    rule(session, me, "dislike", ingredient="黄瓜")
    ctx = ctx_for(session, me)
    no_limit = search_recipes(session, fake_embed, ctx)
    assert ids(no_limit) == [expiring.id, firm.id, estimated.id, disliked.id]
    with_limit = search_recipes(session, fake_embed, ctx, max_time=30)
    assert ids(with_limit) == [expiring.id, firm.id, estimated.id, disliked.id]


def test_firm_time_breaks_ties_only_when_max_time_is_set(session, seeded, fake_embed):
    firm = make_recipe(session, "豆腐甲", lines=[L("豆腐")], time_min=10, time_source="stated")
    estimated = make_recipe(
        session, "豆腐乙", lines=[L("豆腐")], time_min=10, time_source="estimated"
    )
    # Without max_time the newest comes first; with it, the firm time does.
    assert ids(search_recipes(session, fake_embed, ctx_for(session))) == [estimated.id, firm.id]
    limited = search_recipes(session, fake_embed, ctx_for(session), max_time=30)
    assert ids(limited) == [firm.id, estimated.id]


def test_prefer_use_ranks_right_after_relevance(session, seeded, fake_embed):
    stock(session, "小白菜", count=3)
    plain = make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    egg = make_recipe(session, "番茄炒蛋", lines=[L("鸡蛋"), L("番茄")])
    result = search_recipes(session, fake_embed, ctx_for(session), prefer_use=["鸡蛋"])
    assert ids(result) == [egg.id, plain.id]


def test_repeated_query_is_embedded_once(session, seeded):
    make_recipe(session, "近", lines=[L("小白菜")], embedding=unit_vector(0))
    client = FakeEmbedClient(vector=unit_vector(0))
    cache: dict[str, list[float]] = {}
    for _ in range(2):
        result = search_recipes(session, client, ctx_for(session), query="菜", query_vectors=cache)
        assert result.hits and not result.semantic_paused
    assert client.embedded == ["菜"]
    assert list(cache) == ["菜"]
    search_recipes(session, client, ctx_for(session), query="汤", query_vectors=cache)
    assert client.embedded == ["菜", "汤"]


def test_failed_embedding_is_not_cached(session, seeded):
    make_recipe(session, "土豆丝", lines=[L("土豆")])
    cache: dict[str, list[float]] = {}
    search_recipes(session, over_cap_client(), ctx_for(session), query="土豆", query_vectors=cache)
    assert cache == {}


def test_hits_are_capped_at_twenty_by_default(session, seeded, fake_embed):
    for i in range(23):
        make_recipe(session, f"菜{i}", lines=[L("小白菜")])
    assert len(search_recipes(session, fake_embed, ctx_for(session)).hits) == 20


def test_search_query_count_does_not_grow_with_hits(session, seeded, count_queries):
    def run(count: int) -> int:
        for i in range(count):
            make_recipe(
                session,
                f"菜{count}-{i}",
                lines=[L("鸡蛋", role="main"), L("番茄")],
                cookware=["炒锅"],
                embedding=unit_vector(i),
            )
        ctx = ctx_for(session, self_diner(session).id)
        with count_queries() as counter:
            search_recipes(
                session, FakeEmbedClient(), ctx, query="菜", prefer_use=["鸡蛋"]
            )
        return counter.count

    stock(session, "鸡蛋", count=3, expires_on=date.today())
    rule(session, self_diner(session).id, "dislike", ingredient="番茄")
    few = run(2)
    assert run(6) == few


def test_makeable_only_drops_recipes_with_missing_lines(session, seeded):
    stock(session, "豆腐", count=2)
    near = make_recipe(session, "近", lines=[L("小白菜")], embedding=unit_vector(0))
    far = make_recipe(
        session, "远", lines=[L("豆腐"), L("小葱", optional=True)], embedding=unit_vector(5)
    )
    client = FakeEmbedClient(vector=unit_vector(0))
    ctx = ctx_for(session)
    assert ids(search_recipes(session, client, ctx, query="菜")) == [near.id, far.id]
    only = search_recipes(session, client, ctx, query="菜", makeable_only=True)
    assert ids(only) == [far.id]


def test_dislike_facts_use_the_conversation_labels(session, seeded, fake_embed):
    from minemydinner.agent.labels import LabelContext, LabelTable, labels_scope
    from minemydinner.models import Diner

    recipe = make_recipe(session, "番茄炒蛋", lines=[L("鸡蛋"), L("番茄")])
    me = self_diner(session)
    friend = Diner(display_name="小王", is_self=False)
    session.add(friend)
    session.flush()
    rule(session, friend.id, "dislike", ingredient="番茄")
    table = LabelTable.from_state(None, me.id)
    table.label_for("diner:999999")
    table.label_for(f"diner:{friend.id}")
    with labels_scope(LabelContext(table, [], frozenset(), "zh")):
        result = search_recipes(session, fake_embed, ctx_for(session, me.id, friend.id))
    hit = next(h for h in result.hits if h.id == recipe.id)
    assert hit.dislikes == ["Diner 3: 番茄"]


def test_unknown_allergen_exclusions_lines(session, seeded):
    odd = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False), L("小白菜")])
    make_recipe(session, "清炒白菜", lines=[L("小白菜")])
    me = self_diner(session).id
    ctx = ctx_for(session, me)
    assert unknown_allergen_exclusions(session, ctx) == []
    rule(session, me, "allergy", allergen="egg")
    ctx = ctx_for(session, me)
    assert unknown_allergen_exclusions(session, ctx) == [
        ExcludedRecipe(odd.id, "神秘菜", ["某种酱"])
    ]
    assert unknown_allergen_exclusions(session, ctx, [odd.id]) == [
        ExcludedRecipe(odd.id, "神秘菜", ["某种酱"])
    ]
    assert unknown_allergen_exclusions(session, ctx, []) == []
