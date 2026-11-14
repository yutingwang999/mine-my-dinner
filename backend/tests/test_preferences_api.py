import pytest


def test_request_style_round_trip(client):
    assert client.get("/api/preferences/request-style").json() == {"value": None}
    client.put("/api/preferences/request-style", json={"value": "ask_first"})
    assert client.get("/api/preferences/request-style").json() == {"value": "ask_first"}
    client.put("/api/preferences/request-style", json={"value": "plan_then_adjust"})
    assert client.get("/api/preferences/request-style").json() == {"value": "plan_then_adjust"}
    client.put("/api/preferences/request-style", json={"value": None})
    assert client.get("/api/preferences/request-style").json() == {"value": None}


@pytest.mark.parametrize("body", [{"value": "sometimes"}, {}, {"value": 3}])
def test_request_style_rejects_unknown_values(client, body):
    assert client.put("/api/preferences/request-style", json=body).status_code == 422
