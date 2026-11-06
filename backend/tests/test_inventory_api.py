import pytest

from tests.recipefakes import *  # noqa: F403
from tests.recipefakes import by_zh, cookware_id


def id_of(session, name: str) -> int:
    return by_zh(session, name).id


def put_inventory(client, name_or_id, body):
    ingredient_id = name_or_id if isinstance(name_or_id, int) else None
    return client.put("/api/inventory", json={"ingredient_id": ingredient_id, **body})


@pytest.fixture
def put_named(client, seeded):
    return lambda name, body: put_inventory(client, id_of(seeded, name), body)


def always_in_stock_names(client) -> set[str]:
    return {item["name_zh"] for item in client.get("/api/always-in-stock").json()}


def owned_ids(client) -> list[int]:
    return [item["id"] for item in client.get("/api/cookware-owned").json()]


@pytest.mark.parametrize(
    ("name", "body", "status"),
    [
        ("鸡蛋", {"count": 3}, 200),
        ("鸡蛋", {"level": "low"}, 422),
        ("鸡蛋", {}, 422),
        ("鸡蛋", {"count": -1}, 422),
        ("猪肉", {"level": "plenty", "grams": 500}, 200),
        ("猪肉", {"level": "low"}, 200),
        ("猪肉", {"count": 2}, 422),
        ("猪肉", {"grams": 500}, 422),
        ("猪肉", {"level": "lots"}, 422),
        ("盐", {}, 200),
        ("盐", {"count": 1}, 422),
    ],
)
def test_fields_follow_stock_mode(put_named, name, body, status):
    assert put_named(name, body).status_code == status


def test_put_replaces_the_row(client, put_named, seeded):
    put_named("鸡蛋", {"count": 3, "expires_on": "2030-01-01"})
    put_named("鸡蛋", {"count": 5})
    [row] = client.get("/api/inventory").json()
    assert (row["count"], row["expires_on"], row["name_zh"]) == (5, None, "鸡蛋")


def test_unknown_ingredient_is_404(client, seeded):
    assert put_inventory(client, 999999, {"count": 1}).status_code == 404


def test_expiry_is_optional_and_sorted(client, put_named):
    put_named("猪肉", {"level": "low"})
    put_named("鸡蛋", {"count": 3, "expires_on": "2030-01-01"})
    rows = client.get("/api/inventory?sort=expiry").json()
    assert [row["expires_on"] for row in rows] == ["2030-01-01", None]


def test_availability_item_is_deleted_to_mean_none(client, put_named, seeded):
    put_named("盐", {})
    assert client.delete(f"/api/inventory/{id_of(seeded, '盐')}").status_code == 204
    assert client.get("/api/inventory").json() == []
    assert client.delete(f"/api/inventory/{id_of(seeded, '盐')}").status_code == 404


def test_always_in_stock_add_and_remove(client, seeded):
    assert "醋" in always_in_stock_names(client)
    assert client.delete(f"/api/always-in-stock/{id_of(seeded, '醋')}").status_code == 204
    assert "醋" not in always_in_stock_names(client)
    assert client.get(f"/api/always-in-stock/{id_of(seeded, '醋')}").status_code == 404
    assert client.put(f"/api/always-in-stock/{id_of(seeded, '醋')}").status_code == 200
    assert client.put(f"/api/always-in-stock/{id_of(seeded, '醋')}").status_code == 200
    assert client.get(f"/api/always-in-stock/{id_of(seeded, '醋')}").status_code == 200


def test_cookware_owned_add_and_remove(client, seeded):
    wok = cookware_id(seeded, "炒锅")
    assert client.put(f"/api/cookware-owned/{wok}").status_code == 200
    assert wok in owned_ids(client)
    assert client.get(f"/api/cookware-owned/{wok}").status_code == 200
    assert client.delete(f"/api/cookware-owned/{wok}").status_code == 204
    assert wok not in owned_ids(client)
    assert client.put("/api/cookware-owned/999999").status_code == 404
