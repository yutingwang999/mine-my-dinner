import json
from dataclasses import asdict

import pytest

from minemydinner.agent.request import ParsedRequest
from minemydinner.models import Rating
from minemydinner.planning.search import search_recipes
from minemydinner.planning.shortlist import (
    SHORTLIST_MARGIN,
    SHORTLIST_MIN_PER_CATEGORY,
    SHORTLIST_SAME_MAIN,
    ShortlistEntry,
    build_shortlist,
    build_shortlist_result,
    needed_by_dish_type,
)
from minemydinner.planning.validate import PlanSpec
from tests.planningfakes import (
    FakeEmbedClient,
    L,
    blend_vector,
    ctx_for,
    make_recipe,
    rule,
    self_diner,
    stock,
    unit_vector,
)
from tests.recipefakes import seeded  # noqa: F401

# Vegetables with no parent entry, so each gives its own main-ingredient group.
VEGETABLES = ["小白菜", "土豆", "黄瓜", "茄子", "青椒", "豆腐", "胡萝卜", "番茄", "香菇"]


def spec(**kwargs) -> PlanSpec:
    values = {"mode": "shopping", "dishes": 0, "soups": 0, **kwargs}
    return PlanSpec(**values)


def shortlist(session, plan_spec, parsed=None, ctx=None, client=None, cache=None):
    return build_shortlist(
        session,
        client or FakeEmbedClient(),
        ctx or ctx_for(session),
        plan_spec,
        parsed or ParsedRequest(),
        {} if cache is None else cache,
    )


def ids(entries):
    return [e.id for e in entries]


def make_many(session, count, dish_type, prefix, **kwargs):
    """`count` recipes of one type, each with a different main ingredient."""
    return [
        make_recipe(
            session,
            f"{prefix}{i}",
            dish_type=dish_type,
            lines=[L(VEGETABLES[i % len(VEGETABLES)], role="main")] if i < len(VEGETABLES) else [],
            **kwargs,
        )
        for i in range(count)
    ]


def test_constants_have_the_designed_values():
    assert (SHORTLIST_MIN_PER_CATEGORY, SHORTLIST_MARGIN, SHORTLIST_SAME_MAIN) == (5, 3, 2)


def test_each_needed_category_gets_its_quota(session, seeded):
    for dish_type in ("meat dish", "vegetable dish", "soup", "staple food", "dessert", "other"):
        make_many(session, 7, dish_type, dish_type)
    result = shortlist(session, spec(dishes=2, soups=1))  # 两菜一汤
    assert set(result) == {"meat dish", "vegetable dish", "soup"}
    for dish_type, entries in result.items():
        assert len(entries) == 5
        assert {e.dish_type for e in entries} == {dish_type}


def test_quota_grows_with_the_needed_count(session, seeded):
    make_many(session, 12, "vegetable dish", "素")
    make_many(session, 8, "meat dish", "荤")
    result = shortlist(session, spec(dishes=8, meat_dishes=2))  # 8道菜，包含2个荤菜
    assert len(result["vegetable dish"]) == 9
    assert len(result["meat dish"]) == 5
    only_meat = shortlist(session, spec(dishes=2, meat_dishes=2))  # 两个荤菜
    assert set(only_meat) == {"meat dish"}


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"dishes": 2, "soups": 1}, {"meat dish": 2, "vegetable dish": 2, "soup": 1}),
        ({"dishes": 2, "meat_dishes": 1, "vegetable_dishes": 1}, {"meat dish": 1, "vegetable dish": 1}),
        ({"dishes": 2, "meat_dishes": 2}, {"meat dish": 2}),
        ({"dishes": 3, "meat_dishes": 1}, {"meat dish": 1, "vegetable dish": 2}),
        (
            {"dishes": 3, "meat_dishes": 1, "vegetable_dishes": 1, "split_is_minimum": True},
            {"meat dish": 2, "vegetable dish": 2},
        ),
        ({"staples": 1}, {"staple food": 1}),
        ({"desserts": 1, "snacks": 2}, {"dessert": 1, "snack": 2}),
    ],
)
def test_needed_by_dish_type(values, expected):
    assert needed_by_dish_type(spec(**values)) == expected


def egg_dishes(session, count, prefix="蛋"):
    return [
        make_recipe(session, f"{prefix}{i}", lines=[L("鸡蛋", role="main"), L("盐", role="seasoning")])
        for i in range(count)
    ]


def test_same_main_ingredient_is_spread(session, seeded):
    others = make_many(session, 6, "vegetable dish", "素")
    eggs = egg_dishes(session, 6)  # newest, so they rank first
    result = shortlist(session, spec(dishes=2))["vegetable dish"]
    assert len(result) == 5
    kept_eggs = [i for i in ids(result) if i in {e.id for e in eggs}]
    assert kept_eggs == [eggs[-1].id, eggs[-2].id]
    assert len(set(ids(result)) & {o.id for o in others}) == 3


def test_recipe_without_main_line_is_not_grouped(session, seeded):
    make_many(session, 3, "vegetable dish", "素")  # oldest, so they rank last
    no_main = [
        make_recipe(session, f"无主{i}", lines=[L("鸡蛋"), L("盐", role="seasoning")])
        for i in range(3)
    ]
    unlinked = [
        make_recipe(session, f"未识别{i}", lines=[L("某种菜", role="main", linked=False)])
        for i in range(2)
    ]
    result = shortlist(session, spec(dishes=1))["vegetable dish"]
    assert set(ids(result)) == {r.id for r in no_main + unlinked}


def test_parent_entry_groups_children(session, seeded):
    # Created oldest first; the walk meets 荤2, 荤1, both 肉末, then 红烧肉 (skipped: the
    # 猪肉 group is full), then 荤0 fills the last slot, so no back-fill is needed.
    oldest = make_recipe(session, "荤0", dish_type="meat dish", lines=[L("虾", role="main")])
    pork = make_recipe(session, "红烧肉", dish_type="meat dish", lines=[L("猪肉", role="main")])
    mince = [
        make_recipe(session, f"肉末{i}", dish_type="meat dish", lines=[L("猪肉末", role="main")])
        for i in range(2)
    ]
    others = [
        make_recipe(session, f"荤{i}", dish_type="meat dish", lines=[L(name, role="main")])
        for i, name in ((1, "鸡肉"), (2, "牛肉"))
    ]
    result = shortlist(session, spec(dishes=2, meat_dishes=2))["meat dish"]
    assert pork.id not in ids(result)
    assert set(ids(result)) == {r.id for r in mince + others + [oldest]}


@pytest.mark.parametrize("dishes", [2, 3])
def test_eggs_only_fridge_lists_every_makeable_egg_dish(session, seeded, dishes):
    # Only eggs in stock; 蒸蛋羹, 葱花煎蛋, 水煮蛋, 溏心蛋 need eggs only.
    stock(session, "鸡蛋", count=10)
    make_many(session, 6, "vegetable dish", "素")
    eggs = egg_dishes(session, 4)
    plan_spec = spec(mode="fridge_only", dishes=dishes, vegetable_dishes=dishes)
    result = shortlist(session, plan_spec)
    assert set(result) == {"vegetable dish"}
    entries = result["vegetable dish"]
    # The fetch keeps makeable dishes only; the spread keeps two, the back-fill the rest.
    assert ids(entries) == [e.id for e in reversed(eggs)]
    assert all(not e.missing for e in entries)


def test_spread_keeps_enough_dishes_in_shopping_mode(session, seeded):
    eggs = egg_dishes(session, 4)
    result = shortlist(session, spec(dishes=3, vegetable_dishes=3))["vegetable dish"]
    assert ids(result) == [e.id for e in reversed(eggs)]


def test_slots_are_a_cap_without_padding(session, seeded):
    made = make_many(session, 2, "vegetable dish", "素")
    result = shortlist(session, spec(dishes=1))
    assert ids(result["vegetable dish"]) == [made[1].id, made[0].id]
    assert result["meat dish"] == []


def test_back_fill_uses_skipped_recipes_in_rank_order(session, seeded):
    others = make_many(session, 2, "vegetable dish", "素")
    eggs = egg_dishes(session, 4)  # newest, so they rank first
    entries = ids(shortlist(session, spec(dishes=1))["vegetable dish"])
    # The walk keeps eggs[3], eggs[2], skips eggs[1] and eggs[0], keeps both others; one
    # slot is left, and the back-fill takes the highest-ranked skipped recipe.
    assert entries == [eggs[3].id, eggs[2].id, eggs[1].id, others[1].id, others[0].id]


def test_named_ingredient_is_left_out_of_the_spread(session, seeded):
    plain = [
        make_recipe(session, title, lines=[L("鸡蛋", role="main")])
        for title in ("蒸蛋羹", "水煮蛋", "溏心蛋")
    ]
    tomato = [
        make_recipe(session, title, lines=[L("番茄", role="main"), L("鸡蛋", role="main")])
        for title in ("番茄炒蛋", "番茄蛋饼", "番茄蛋卷")
    ]
    cabbage = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])
    result = shortlist(session, spec(dishes=1, all_use=["鸡蛋"]))  # 全做鸡蛋
    entries = ids(result["vegetable dish"])
    assert len(entries) == 5
    assert {r.id for r in plain} <= set(entries)
    assert len(set(entries) & {r.id for r in tomato}) == 2
    assert cabbage.id not in entries
    # The same holds when eggs are only preferred or required at least once.
    for named in ({"prefer_use": ["鸡蛋"]}, {"must_use": ["鸡蛋"]}):
        entries = ids(shortlist(session, spec(dishes=1, **named))["vegetable dish"])
        assert {r.id for r in plain} <= set(entries)


def test_all_use_filters_and_prefer_use_ranks(session, seeded):
    egg = make_recipe(session, "番茄炒蛋", lines=[L("番茄", role="main"), L("鸡蛋", role="main")])
    cabbage = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])
    potato = make_recipe(session, "土豆丝", lines=[L("土豆", role="main")])
    session.add(Rating(recipe_id=cabbage.id, score=5))
    session.flush()
    only = shortlist(session, spec(dishes=1, all_use=["鸡蛋"]))
    assert ids(only["vegetable dish"]) == [egg.id]
    preferred = shortlist(session, spec(dishes=1, prefer_use=["鸡蛋"]))
    assert ids(preferred["vegetable dish"]) == [egg.id, cabbage.id, potato.id]
    plain = shortlist(session, spec(dishes=1))
    assert ids(plain["vegetable dish"]) == [cabbage.id, potato.id, egg.id]


def test_must_use_keeps_a_dish_using_it(session, seeded):
    near = [
        make_recipe(
            session,
            f"素{i}",
            lines=[L(VEGETABLES[i], role="main")],
            embedding=unit_vector(0),
        )
        for i in range(7)
    ]
    egg = make_recipe(
        session, "番茄炒蛋", lines=[L("鸡蛋", role="main")], embedding=unit_vector(5)
    )
    parsed = ParsedRequest(query="清淡")
    client = FakeEmbedClient(vector=unit_vector(0))
    without = shortlist(session, spec(dishes=1), parsed=parsed, client=client)
    assert egg.id not in ids(without["vegetable dish"])
    result = shortlist(session, spec(dishes=1, must_use=["鸡蛋"]), parsed=parsed, client=client)
    entries = ids(result["vegetable dish"])
    assert len(entries) == 5
    # The egg dish ranks low (by query relevance) but stays, replacing the lowest-ranked entry.
    assert entries[-1] == egg.id
    assert entries[:4] == ids(without["vegetable dish"])[:4]
    assert set(entries[:4]) <= {r.id for r in near}


def test_query_ranks_but_does_not_empty_the_shortlist(session, seeded):
    from tests.planningfakes import over_cap_client

    made = make_many(session, 3, "vegetable dish", "素")
    matched = make_recipe(session, "清淡白菜", lines=[L("小白菜", role="main")])
    result = shortlist(
        session, spec(dishes=1), parsed=ParsedRequest(query="清淡"), client=over_cap_client()
    )
    entries = ids(result["vegetable dish"])
    assert entries[0] == matched.id
    assert set(entries) == {matched.id} | {r.id for r in made}


def test_query_is_embedded_once_per_run(session, seeded):
    make_many(session, 3, "vegetable dish", "素", embedding=unit_vector(0))
    client = FakeEmbedClient(vector=unit_vector(0))
    cache: dict[str, list[float]] = {}
    shortlist(session, spec(dishes=2, soups=1), ParsedRequest(query="清淡"), client=client, cache=cache)
    search_recipes(session, client, ctx_for(session), query="清淡", query_vectors=cache)
    assert client.embedded == ["清淡"]


def test_exclude_ids_reaches_the_next_batch(session, seeded):
    make_many(session, 12, "soup", "汤")
    first = shortlist(session, spec(soups=1))["soup"]
    assert len(first) == 5
    client = FakeEmbedClient()
    everything = ids(search_recipes(session, client, ctx_for(session), dish_types=["soup"]).hits)
    assert ids(first) == everything[:5]
    following = search_recipes(
        session, client, ctx_for(session), dish_types=["soup"], exclude_ids=ids(first)
    )
    assert ids(following.hits) == everything[5:]


def test_shortlist_respects_dietary_exclusions(session, seeded):
    make_recipe(session, "番茄炒蛋", lines=[L("番茄", role="main"), L("鸡蛋", role="main")])
    odd = make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    ok = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])
    disliked = make_recipe(session, "拍黄瓜", lines=[L("黄瓜", role="main")])
    me = self_diner(session)
    me.display_name = "Zhang Wei"
    rule(session, me.id, "allergy", allergen="egg")
    rule(session, me.id, "dislike", ingredient="黄瓜")
    result = shortlist(session, spec(dishes=1, must_use=["鸡蛋"]), ctx=ctx_for(session, me.id))
    entries = result["vegetable dish"]
    assert ids(entries) == [ok.id, disliked.id]
    assert odd.id not in ids(entries)
    assert entries[1].dislikes == ["Diner 1: 黄瓜"]
    text = json.dumps([asdict(e) for e in entries], ensure_ascii=False)
    assert "Zhang Wei" not in text and "source_text" not in text


def test_shortlist_result_carries_the_search_notes(session, seeded):
    from tests.planningfakes import all_failed_client

    make_recipe(session, "神秘菜", lines=[L("某种酱", linked=False)])
    ok = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")])
    me = self_diner(session)
    rule(session, me.id, "allergy", allergen="egg")
    result = build_shortlist_result(
        session,
        all_failed_client(),
        ctx_for(session, me.id),
        spec(dishes=1),
        ParsedRequest(query="清淡"),
        {},
    )
    assert ids(result.entries["vegetable dish"]) == [ok.id]
    assert result.left_out_unknown_allergens == ["神秘菜"]
    assert result.semantic_paused is True
    calm = build_shortlist_result(
        session, FakeEmbedClient(), ctx_for(session), spec(dishes=1), ParsedRequest(), {}
    )
    assert calm.left_out_unknown_allergens == [] and calm.semantic_paused is False


def test_entry_fields(session, seeded):
    make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")], time_min=10, time_source="stated", servings=2)
    entry = shortlist(session, spec(dishes=1))["vegetable dish"][0]
    assert isinstance(entry, ShortlistEntry)
    assert set(asdict(entry)) == {
        "id", "title", "dish_type", "time_min", "time_source", "servings",
        "missing", "missing_cookware", "uses_expiring", "dislikes",
    }
    assert entry.time_min == 10 and entry.time_source == "stated" and entry.servings == 2
    assert entry.missing == ["小白菜"]


def test_put_back_never_drops_another_protected_dish(session, seeded):
    near = [
        make_recipe(session, f"素{i}", lines=[L(VEGETABLES[i], role="main")], embedding=unit_vector(0))
        for i in range(4)
    ]
    tomato = make_recipe(
        session, "番茄", lines=[L("番茄", role="main")], embedding=blend_vector(0, 5, 1.0)
    )
    egg = make_recipe(session, "蒸蛋", lines=[L("鸡蛋", role="main")], embedding=unit_vector(5))
    result = shortlist(
        session,
        spec(dishes=1, must_use=["番茄", "鸡蛋"]),
        parsed=ParsedRequest(query="清淡"),
        client=FakeEmbedClient(vector=unit_vector(0)),
    )
    entries = ids(result["vegetable dish"])
    # The egg dish replaces the lowest-ranked entry that no protection needs, not the
    # tomato dish, which is the only one using tomato.
    assert entries == [near[3].id, near[2].id, near[1].id, tomato.id, egg.id]


def _near_unmakeable_and_far_makeable(session):
    """Six dishes closest to the query but missing their main ingredient, and three dishes
    farther away whose only line is in stock."""
    near = [
        make_recipe(session, f"近{i}", lines=[L(name, role="main")], embedding=unit_vector(0))
        for i, name in enumerate(["小白菜", "土豆", "青椒", "胡萝卜", "番茄", "香菇"])
    ]
    far = []
    for i, name in enumerate(["豆腐", "黄瓜", "茄子"]):
        stock(session, name, count=2)
        far.append(
            make_recipe(session, f"远{i}", lines=[L(name, role="main")], embedding=unit_vector(5))
        )
    return near, far


def test_fridge_only_shortlist_holds_only_makeable_recipes(session, seeded):
    near, far = _near_unmakeable_and_far_makeable(session)
    result = shortlist(
        session,
        spec(mode="fridge_only", dishes=1),
        parsed=ParsedRequest(query="清淡"),
        client=FakeEmbedClient(vector=unit_vector(0)),
    )
    entries = result["vegetable dish"]
    # Five slots, three makeable recipes: the list stops at three and is not padded.
    assert {e.id for e in entries} == {r.id for r in far}
    assert all(not e.missing for e in entries)
    assert result["meat dish"] == []


def test_shopping_keeps_query_relevance_first(session, seeded):
    near, far = _near_unmakeable_and_far_makeable(session)
    result = shortlist(
        session,
        spec(mode="shopping", dishes=1),
        parsed=ParsedRequest(query="清淡"),
        client=FakeEmbedClient(vector=unit_vector(0)),
    )
    entries = result["vegetable dish"]
    assert {e.id for e in entries} <= {r.id for r in near}
    assert len(entries) == 5


def test_fridge_only_must_use_keeps_the_makeable_user(session, seeded):
    stock(session, "鸡蛋", count=6)
    stock(session, "豆腐", count=2)
    # A makeable dish closest to the query, so the makeable protection is already met
    # and only the must_use protection decides which egg dish is kept.
    make_recipe(session, "近豆腐", lines=[L("豆腐", role="main")], embedding=unit_vector(0))
    for i, name in enumerate(["小白菜", "土豆", "青椒", "胡萝卜", "香菇"]):
        make_recipe(session, f"近{i}", lines=[L(name, role="main")], embedding=unit_vector(0))
    unmakeable = make_recipe(
        session,
        "番茄炒蛋",
        lines=[L("鸡蛋", role="main"), L("番茄", role="main")],
        embedding=blend_vector(0, 5, 0.5),
    )
    makeable = make_recipe(
        session, "蒸蛋", lines=[L("鸡蛋", role="main")], embedding=unit_vector(5)
    )
    result = shortlist(
        session,
        spec(mode="fridge_only", dishes=1, must_use=["鸡蛋"]),
        parsed=ParsedRequest(query="清淡"),
        client=FakeEmbedClient(vector=unit_vector(0)),
    )
    entries = ids(result["vegetable dish"])
    assert makeable.id in entries
    assert unmakeable.id not in entries


def test_must_use_protection_runs_after_the_back_fill(session, seeded):
    other = make_recipe(session, "清炒白菜", lines=[L("小白菜", role="main")], embedding=unit_vector(0))
    eggs = [
        make_recipe(session, f"蛋{i}", lines=[L("鸡蛋", role="main")], embedding=unit_vector(0))
        for i in range(5)
    ]
    # Uses tomato (a secondary line) and has eggs as its main set; farthest from the query.
    tomato = make_recipe(
        session,
        "番茄蛋",
        lines=[L("鸡蛋", role="main"), L("番茄")],
        embedding=unit_vector(5),
    )
    result = shortlist(
        session,
        spec(dishes=1, must_use=["番茄"]),
        parsed=ParsedRequest(query="清淡"),
        client=FakeEmbedClient(vector=unit_vector(0)),
    )
    entries = ids(result["vegetable dish"])
    # Walk: eggs[4], eggs[3], then 清炒白菜; eggs[2..0] and 番茄蛋 are skipped ({鸡蛋} full).
    # Back-fill: eggs[2], eggs[1]. No entry uses tomato, so the protection puts 番茄蛋 back
    # in place of the lowest-ranked entry, 清炒白菜.
    assert entries == [eggs[4].id, eggs[3].id, eggs[2].id, eggs[1].id, tomato.id]
    assert other.id not in entries
    without = ids(
        shortlist(
            session,
            spec(dishes=1),
            parsed=ParsedRequest(query="清淡"),
            client=FakeEmbedClient(vector=unit_vector(0)),
        )["vegetable dish"]
    )
    assert without == [eggs[4].id, eggs[3].id, eggs[2].id, eggs[1].id, other.id]


def complete_types(session, plan_spec):
    return build_shortlist_result(
        session, FakeEmbedClient(), ctx_for(session), plan_spec, ParsedRequest(), {}
    ).complete


def test_type_is_complete_when_fewer_recipes_fit_than_slots(session, seeded):
    make_many(session, 3, "vegetable dish", "素")
    assert complete_types(session, spec(dishes=2, meat_dishes=0, vegetable_dishes=2)) == {
        "vegetable dish"
    }


def test_type_is_not_complete_when_the_cap_cut_a_recipe(session, seeded):
    make_many(session, 7, "vegetable dish", "素")
    make_many(session, 2, "soup", "汤")
    result = complete_types(session, spec(dishes=2, meat_dishes=0, vegetable_dishes=2, soups=1))
    # 7 vegetable dishes against 5 slots: not complete; the 2 soups all fit.
    assert result == {"soup"}


def test_type_is_not_complete_when_the_spread_left_one_out(session, seeded):
    # Six dishes, five slots: whichever way the walk goes, one recipe is left out.
    make_many(session, 6, "vegetable dish", "素")
    assert "vegetable dish" not in complete_types(session, spec(dishes=1, meat_dishes=0, vegetable_dishes=1))


def test_type_with_no_recipes_is_complete(session, seeded):
    make_many(session, 1, "vegetable dish", "素")
    result = complete_types(session, spec(dishes=2, soups=1))
    assert "soup" in result and "meat dish" in result
