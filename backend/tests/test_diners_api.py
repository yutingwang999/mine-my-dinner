import pytest

from minemydinner.models import DietaryRule
from minemydinner.seed import ensure_self_diner
from tests.planningfakes import make_recipe
from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh, count


@pytest.fixture
def self_diner(session):
    return ensure_self_diner(session)


def add_rule_api(client, diner_id, **rule):
    return client.post(f"/api/diners/{diner_id}/rules", json=rule)


def post_rule(client, diner, rule):
    return add_rule_api(client, diner.id, **rule)


def resolve_at(session, rule: dict) -> dict:
    """Replace "@名称" with that dictionary entry's id."""
    value = rule.get("ingredient_id")
    if isinstance(value, str) and value.startswith("@"):
        return rule | {"ingredient_id": by_zh(session, value[1:]).id}
    return rule


def test_list_includes_self_first(client, self_diner):
    client.post("/api/diners", json={"display_name": "小李"})
    diners = client.get("/api/diners").json()
    assert diners[0]["is_self"] is True and diners[1]["display_name"] == "小李"


def test_friend_crud_and_delete_removes_rules(client, session):
    friend = client.post("/api/diners", json={"display_name": "小李"}).json()
    assert friend["is_self"] is False
    renamed = client.patch(f"/api/diners/{friend['id']}", json={"display_name": "李同学"}).json()
    assert renamed["display_name"] == "李同学"
    assert add_rule_api(client, friend["id"], rule_type="allergy", allergen="egg").status_code == 201
    assert client.delete(f"/api/diners/{friend['id']}").status_code == 204
    assert count(session, DietaryRule) == 0


def test_blank_name_is_rejected(client):
    assert client.post("/api/diners", json={"display_name": "  "}).status_code == 422


def test_self_diner_cannot_be_deleted(client, self_diner):
    assert client.delete(f"/api/diners/{self_diner.id}").status_code == 409


def test_missing_diner_is_404(client):
    assert client.delete("/api/diners/999999").status_code == 404
    assert add_rule_api(client, 999999, rule_type="allergy", allergen="egg").status_code == 404


@pytest.mark.parametrize(
    ("rule", "status"),
    [
        ({"rule_type": "allergy", "allergen": "peanut"}, 201),
        ({"rule_type": "allergy", "allergen": "shrimp"}, 422),
        ({"rule_type": "allergy", "text": "虾"}, 422),
        ({"rule_type": "allergy", "ingredient_id": "@芒果"}, 201),
        ({"rule_type": "allergy", "allergen": "egg", "ingredient_id": "@芒果"}, 422),
        ({"rule_type": "allergy"}, 422),
        ({"rule_type": "allergy", "ingredient_id": 999999}, 422),
        ({"rule_type": "dietary restriction", "text": "不吃内脏"}, 201),
        ({"rule_type": "dislike", "ingredient_id": "@香菜"}, 201),
        ({"rule_type": "hate", "text": "x"}, 422),
    ],
)
def test_rule_validation(client, seeded, self_diner, rule, status):
    assert post_rule(client, self_diner, resolve_at(seeded, rule)).status_code == status


def test_rules_list_and_delete(client, seeded, self_diner):
    created = post_rule(client, self_diner, {"rule_type": "allergy", "allergen": "peanut"}).json()
    assert [rule["id"] for rule in client.get(f"/api/diners/{self_diner.id}/rules").json()] == [
        created["id"]
    ]
    assert client.delete(f"/api/rules/{created['id']}").status_code == 204
    assert client.get(f"/api/diners/{self_diner.id}/rules").json() == []
    assert client.delete(f"/api/rules/{created['id']}").status_code == 404


@pytest.mark.parametrize("text", ["吃素", "Vegetarian", " 我是VEGAN "])
def test_diet_word_text_rule_refused(client, self_diner, text):
    response = add_rule_api(client, self_diner.id, rule_type="dietary restriction", text=text)
    assert response.status_code == 422
    assert response.json()["code"] == "diet_word"


def test_plain_text_rule_still_accepted(client, self_diner):
    response = add_rule_api(client, self_diner.id, rule_type="dislike", text="香菜")
    assert response.status_code == 201


def test_nickname_warning_single_cjk(client):
    friend = client.post("/api/diners", json={"display_name": "鱼"}).json()
    assert friend["nickname_warning"] is True


def test_nickname_warning_food_term(client, seeded):
    make_recipe(seeded, "鱼香肉丝")
    friend = client.post("/api/diners", json={"display_name": "鱼香"}).json()
    assert friend["nickname_warning"] is True
    listed = {d["display_name"]: d for d in client.get("/api/diners").json()}
    assert listed["鱼香"]["nickname_warning"] is True


def test_nickname_warning_normal(client, self_diner):
    friend = client.post("/api/diners", json={"display_name": "小王"}).json()
    assert friend["nickname_warning"] is False
    renamed = client.patch(f"/api/diners/{friend['id']}", json={"display_name": "鱼"}).json()
    assert renamed["nickname_warning"] is True
    assert client.get("/api/diners").json()[0]["nickname_warning"] is False
