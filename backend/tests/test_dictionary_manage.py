import pytest
from sqlalchemy import select

from minemydinner.models import DietaryRule, Ingredient, Inventory, Recipe, RecipeIngredient
from minemydinner.seed import ensure_self_diner
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh, cookware_id


def id_of(session, name: str) -> int:
    return by_zh(session, name).id


def user_entry(session, name, allergens=None, parent=None, **fields) -> Ingredient:
    entry = Ingredient(
        name_zh=name,
        category="seasoning",
        stock_mode="level",
        source="user",
        allergens=allergens,
        parent=parent,
        **fields,
    )
    session.add(entry)
    session.flush()
    return entry


def dish(session, title, lines) -> Recipe:
    recipe = Recipe(
        lang="zh",
        title=title,
        dish_type="other",
        source_type="manual",
        ingredients=[
            RecipeIngredient(
                raw=name, name=name, ingredient_id=ingredient_id, amount_type="missing", role="main"
            )
            for name, ingredient_id in lines
        ],
    )
    session.add(recipe)
    session.flush()
    return recipe


def lines_named(session, name) -> list[RecipeIngredient]:
    return list(session.scalars(select(RecipeIngredient).where(RecipeIngredient.name == name)))


def post_cookware(client, **fields):
    return client.post("/api/cookware", json=fields)


@pytest.fixture
def dishes_with_unknowns(session, seeded):
    grandma = user_entry(session, "外婆辣酱")
    dish(session, "甲", [("秘制酱", None), ("外婆辣酱", grandma.id)])
    dish(session, "乙", [("秘制酱", None), ("葱", id_of(session, "葱"))])
    return grandma


@pytest.fixture
def entry_used_by_dish(session, seeded):
    entry = user_entry(session, "秘制酱", allergens=[])
    dish(session, "甲", [("秘制酱", entry.id)])
    return entry


@pytest.fixture
def entry_used_by_allergy_rule(session, seeded):
    entry = user_entry(session, "秘制酱", allergens=[])
    diner = ensure_self_diner(session)
    session.add(DietaryRule(diner_id=diner.id, rule_type="allergy", ingredient_id=entry.id))
    session.flush()
    return entry


@pytest.fixture
def entry_with_child(session, seeded):
    parent = user_entry(session, "家酱", allergens=[])
    user_entry(session, "家酱二号", allergens=[], parent=parent)
    return parent


def test_entries_report_how_many_dishes_use_them(client, session, seeded):
    entry = user_entry(session, "秘制酱", allergens=[])
    dish(session, "甲", [("秘制酱", entry.id)])
    dish(session, "乙", [("秘制酱", entry.id), ("秘制酱", entry.id)])
    listed = {item["name_zh"]: item for item in client.get("/api/ingredients?source=user").json()}
    assert listed["秘制酱"]["dish_count"] == 2
    browsed = {item["id"]: item for item in client.get("/api/ingredients?limit=500").json()}
    assert browsed[entry.id]["dish_count"] == 2
    assert browsed[id_of(session, "葱")]["dish_count"] == 0


def test_cookware_reports_how_many_dishes_need_it(client, session, seeded):
    from minemydinner.models import RecipeCookware

    created = post_cookware(client, name_zh="面包机", owned=False).json()
    assert created["dish_count"] == 0
    recipe = dish(session, "甲", [("葱", id_of(session, "葱"))])
    recipe.cookware = [RecipeCookware(cookware_id=created["id"])]
    session.flush()
    listed = client.get("/api/cookware?source=user").json()
    assert listed[0]["dish_count"] == 1


def test_seed_entries_are_read_only(client, seeded):
    葱 = id_of(seeded, "葱")
    assert client.patch(f"/api/ingredients/{葱}", json={"allergens": []}).status_code == 403
    assert client.delete(f"/api/ingredients/{葱}").status_code == 403


def test_list_user_entries_only(client, seeded):
    user_entry(seeded, "秘制酱")
    assert [item["name_zh"] for item in client.get("/api/ingredients?source=user").json()] == [
        "秘制酱"
    ]


def test_patch_user_entry_and_link_new_alias(client, session, seeded):
    entry = user_entry(session, "秘制酱")
    dish(session, "甲", [("秘酱", None)])
    response = client.patch(
        f"/api/ingredients/{entry.id}", json={"aliases_zh": ["秘酱"], "allergens": ["soy"]}
    )
    assert response.status_code == 200
    assert response.json()["allergens"] == ["soy"] and response.json()["linked_lines"] == 1


def test_patch_can_set_allergens_back_to_unknown(client, session, seeded):
    entry = user_entry(session, "秘制酱", allergens=["soy"])
    body = client.patch(f"/api/ingredients/{entry.id}", json={"allergens": None}).json()
    assert body["allergens"] is None


def test_patch_rejects_a_term_owned_by_another_entry(client, session, seeded):
    entry = user_entry(session, "秘制酱")
    response = client.patch(f"/api/ingredients/{entry.id}", json={"aliases_zh": ["葱花"]})
    assert response.status_code == 409


def test_patch_keeps_own_terms(client, session, seeded):
    entry = user_entry(session, "秘制酱")
    response = client.patch(f"/api/ingredients/{entry.id}", json={"name_zh": "秘制酱"})
    assert response.status_code == 200


def test_patch_stock_mode_is_refused_while_in_inventory(client, session, seeded):
    entry = user_entry(session, "秘制酱")
    session.add(Inventory(ingredient_id=entry.id, level="low"))
    session.flush()
    response = client.patch(f"/api/ingredients/{entry.id}", json={"stock_mode": "count"})
    assert response.status_code == 409


def test_patch_parent_keeps_one_level(client, session, seeded, entry_with_child):
    assert (
        client.patch(
            f"/api/ingredients/{entry_with_child.id}", json={"parent_id": id_of(session, "酱油")}
        ).status_code
        == 422
    )
    other = user_entry(session, "新酱")
    assert (
        client.patch(f"/api/ingredients/{other.id}", json={"parent_id": other.id}).status_code
        == 422
    )


def test_unresolved_lists_null_entries_and_unmatched_names_with_counts(
    client, session, seeded, dishes_with_unknowns
):
    body = client.get("/api/allergens/unresolved").json()
    assert body["unmatched"] == [{"name": "秘制酱", "dish_count": 2}]
    assert body["entries"] == [
        {"id": dishes_with_unknowns.id, "name": "外婆辣酱", "dish_count": 1}
    ]


def test_resolving_unmatched_name_creates_entry_and_links_all_dishes(
    client, session, seeded, dishes_with_unknowns
):
    response = client.post("/api/allergens/resolve", json={"name": "秘制酱", "allergens": ["soy"]})
    assert response.status_code == 200 and response.json()["linked_lines"] == 2
    assert client.get("/api/allergens/unresolved").json()["unmatched"] == []
    session.expire_all()
    assert all(line.ingredient_id for line in lines_named(session, "秘制酱"))
    assert by_zh(session, "秘制酱").allergens == ["soy"]


def test_resolving_entry_as_none_clears_unknown(client, seeded, dishes_with_unknowns):
    entry_id = client.get("/api/allergens/unresolved").json()["entries"][0]["id"]
    client.post("/api/allergens/resolve", json={"ingredient_id": entry_id, "allergens": []})
    assert client.get("/api/allergens/unresolved").json()["entries"] == []


def test_resolve_rejects_unlisted_allergen_and_requires_explicit_value(
    client, seeded, dishes_with_unknowns
):
    post = lambda body: client.post("/api/allergens/resolve", json=body)  # noqa: E731
    assert post({"name": "秘制酱", "allergens": ["shrimp"]}).status_code == 422
    assert post({"name": "秘制酱"}).status_code == 422
    assert post({"name": "秘制酱", "allergens": None}).status_code == 422
    assert post({"allergens": []}).status_code == 422


def test_resolve_refuses_seed_entries(client, seeded):
    body = {"ingredient_id": id_of(seeded, "葱"), "allergens": []}
    assert client.post("/api/allergens/resolve", json=body).status_code == 403


def test_deleting_used_entry_returns_its_lines_to_unmatched(
    client, session, seeded, entry_used_by_dish
):
    assert client.delete(f"/api/ingredients/{entry_used_by_dish.id}").status_code == 204
    session.expire_all()
    assert all(line.ingredient_id is None for line in lines_named(session, "秘制酱"))


def test_entry_used_by_a_rule_or_as_a_parent_cannot_be_deleted(
    client, seeded, entry_used_by_allergy_rule, entry_with_child
):
    assert client.delete(f"/api/ingredients/{entry_used_by_allergy_rule.id}").status_code == 409
    assert client.delete(f"/api/ingredients/{entry_with_child.id}").status_code == 409


def test_seed_cookware_is_read_only_and_user_cookware_can_be_deleted(client, seeded):
    assert client.delete(f"/api/cookware/{cookware_id(seeded, '炒锅')}").status_code == 403
    created = post_cookware(client, name_zh="面包机", owned=False).json()
    assert client.delete(f"/api/cookware/{created['id']}").status_code == 204


def test_user_cookware_can_be_renamed(client, seeded):
    created = post_cookware(client, name_zh="面包机", owned=False).json()
    body = client.patch(f"/api/cookware/{created['id']}", json={"name_en": "bread maker"}).json()
    assert body["name_en"] == "bread maker"
    listed = client.get("/api/cookware?source=user").json()
    assert [item["name_zh"] for item in listed] == ["面包机"]


def test_match_returns_entries_for_known_names(client, session, seeded):
    entry = user_entry(session, "测试西兰花", user_aliases_zh=["测试西蓝花"])
    body = client.post(
        "/api/ingredients/match", json={"names": ["测试西兰花", "测试西蓝花", "火龙果皮"]}
    ).json()
    assert [match["name"] for match in body["matches"]] == ["测试西兰花", "测试西蓝花"]
    assert {match["entry"]["id"] for match in body["matches"]} == {entry.id}


def test_match_ignores_case_and_spaces(client, session, seeded):
    entry = user_entry(session, "测试西兰花", name_en="test broccoli")
    body = client.post("/api/ingredients/match", json={"names": [" Test Broccoli "]}).json()
    assert body["matches"][0]["entry"]["id"] == entry.id


def test_match_rejects_too_many_names(client, seeded):
    response = client.post("/api/ingredients/match", json={"names": ["a"] * 201})
    assert response.status_code == 422


def link_fixture(session):
    target = user_entry(session, "测试牛肉末")
    dish(session, "甲", [("测试牛肉馅", None)])
    dish(session, "乙", [("测试牛肉馅", None)])
    return target


def test_link_name_links_lines_without_remembering(client, session, seeded):
    target = link_fixture(session)
    response = client.post(
        "/api/allergens/link",
        json={"name": " 测试牛肉馅 ", "ingredient_id": target.id, "remember": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["linked_lines"] == 2 and body["remembered"] is False
    assert body["aliases_zh"] == [] and body["user_aliases_zh"] == []
    session.expire_all()
    assert all(line.ingredient_id == target.id for line in lines_named(session, "测试牛肉馅"))
    names = [item["name"] for item in client.get("/api/allergens/unresolved").json()["unmatched"]]
    assert "测试牛肉馅" not in names


def test_link_name_remembers(client, session, seeded):
    target = link_fixture(session)
    body = client.post(
        "/api/allergens/link",
        json={"name": "测试牛肉馅", "ingredient_id": target.id, "remember": True},
    ).json()
    assert body["remembered"] is True and body["linked_lines"] == 2
    assert body["aliases_zh"] == ["测试牛肉馅"]


def test_link_name_conflict(client, session, seeded):
    target = link_fixture(session)
    user_entry(session, "测试肉糜", aliases_zh=["测试牛肉馅"])
    body = client.post(
        "/api/allergens/link",
        json={"name": "测试牛肉馅", "ingredient_id": target.id, "remember": True},
    ).json()
    assert body["remembered"] is False and body["linked_lines"] == 2
    session.expire_all()
    assert all(line.ingredient_id == target.id for line in lines_named(session, "测试牛肉馅"))


def test_link_name_rejects_unknown_entry_and_blank_name(client, session, seeded):
    target = link_fixture(session)
    missing = client.post(
        "/api/allergens/link", json={"name": "甲", "ingredient_id": 10**9, "remember": False}
    )
    assert missing.status_code == 404
    blank = client.post(
        "/api/allergens/link", json={"name": "  ", "ingredient_id": target.id, "remember": False}
    )
    assert blank.status_code == 422
