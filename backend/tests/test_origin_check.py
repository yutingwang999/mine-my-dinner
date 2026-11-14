import pytest

from minemydinner.llm.client import AllModelsFailed
from tests.recipefakes import FakeLLMClient, use_llm

TEXT = "Scrambled eggs with scallion: beat two eggs, fry, and add chopped scallion."


@pytest.fixture
def down_llm(client):
    """A model that always fails, so no test reaches a real provider."""
    return use_llm(FakeLLMClient(error=AllModelsFailed("down")))


def test_cross_site_post_is_rejected(client):
    r = client.post(
        "/api/imports/text", json={"text": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403 and r.json()["code"] == "forbidden_origin"


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_other_write_methods_with_foreign_origin_are_rejected(client, method):
    r = getattr(client, method)("/api/diners/1", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "forbidden_origin"


def test_same_site_post_passes_the_check(client, down_llm):
    r = client.post(
        "/api/imports/text", json={"text": TEXT}, headers={"Origin": "http://localhost:3000"}
    )
    assert r.status_code != 403


def test_post_without_origin_passes(client, down_llm):
    assert client.post("/api/imports/text", json={"text": TEXT}).status_code != 403


def test_get_with_foreign_origin_is_allowed(client):
    r = client.get("/api/diners", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_rebound_host_is_rejected(client):
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 400 and r.json()["code"] == "bad_host"


def test_allowed_host_with_port_passes(client):
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost"}).status_code == 200


def test_ipv6_host_is_compared_without_port(client):
    r = client.get("/api/health", headers={"Host": "[::1]:8000"})
    assert r.status_code == 400 and r.json()["code"] == "bad_host"


def test_empty_host_is_rejected(client):
    r = client.get("/api/health", headers={"Host": ""})
    assert r.status_code == 400 and r.json()["code"] == "bad_host"
