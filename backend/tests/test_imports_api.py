import copy

import pytest
from sqlalchemy import func, select

from minemydinner.api.deps import get_llm_client
from minemydinner.llm.client import AllModelsFailed, MonthlyCapExceeded, StructuredResult
from minemydinner.main import app
from minemydinner.models import ExtractionLog, Recipe
from minemydinner.imports.service import first_url
from minemydinner.seed import seed_all
from tests.webfakes import *  # noqa: F403

PAYLOAD = {
    "lang": "zh",
    "title": "葱花蛋",
    "dish_type": "vegetable dish",
    "time_min": 10,
    "time_source": "stated",
    "servings": 2,
    "tags": [],
    "steps_summary": "鸡蛋炒熟后撒葱花。",
    "ingredients": [
        {
            "raw": "少量葱花",
            "name": "葱花",
            "amount": None,
            "unit": None,
            "amount_type": "vague",
            "role": "secondary",
            "optional": False,
        },
        {
            "raw": "两个鸡蛋",
            "name": "鸡蛋",
            "amount": 2,
            "unit": "piece",
            "amount_type": "count",
            "role": "main",
            "optional": False,
        },
    ],
    "cookware": ["炒锅", "神奇锅"],
}


class FakeLLM:
    def __init__(self, payload=PAYLOAD, error=None, fail_on=None):
        self.payload = payload
        self.error = error
        self.fail_on = fail_on
        self.tasks = []

    def invoke_structured(self, task, messages, schema):
        self.tasks.append(task)
        text = str(messages[-1].content)
        if self.error is not None:
            raise self.error
        if self.fail_on is not None and self.fail_on in text:
            raise AllModelsFailed("down")
        return StructuredResult(schema.model_validate(self.payload), "gemini", False)


def use_llm(llm: FakeLLM) -> FakeLLM:
    app.dependency_overrides[get_llm_client] = lambda: llm
    return llm


@pytest.fixture
def fake_extraction(client):
    return use_llm(FakeLLM())


@pytest.fixture
def seeded(session):
    seed_all(session)
    session.flush()
    return session


@pytest.fixture
def fake_extraction_with_unknown(client):
    payload = copy.deepcopy(PAYLOAD)
    payload["ingredients"][1] |= {"raw": "秘制酱", "name": "秘制酱", "amount_type": "missing"}
    return use_llm(FakeLLM(payload))


@pytest.fixture
def fake_extraction_fails_on_second(client):
    return use_llm(FakeLLM(fail_on="乙"))


@pytest.fixture
def llm_over_cap(client):
    return FakeLLM(error=MonthlyCapExceeded("over"))


@pytest.fixture
def llm_down(client):
    return FakeLLM(error=AllModelsFailed("down"))


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def ids_by_name(lines) -> dict:
    return {line["name"]: line["ingredient_id"] for line in lines}


def post_text(client, llm, text="番茄炒蛋"):
    use_llm(llm)
    return client.post("/api/imports/text", json={"text": text})


def post_images(client, files):
    return client.post(
        "/api/imports/screenshots",
        files=[("images", (name, data, mime)) for name, data, mime in files],
    )


def post_batch(client, text: str, encoding="utf-8"):
    return client.post(
        "/api/imports/batch", files={"file": ("dishes.txt", text.encode(encoding), "text/plain")}
    )


def test_text_returns_draft_without_writing_a_recipe(client, session, fake_extraction):
    response = client.post("/api/imports/text", json={"text": "番茄炒蛋"})
    assert response.status_code == 200
    body = response.json()
    assert body["source_type"] == "text" and body["source_url"] is None
    assert body["provenance"]["input_text"] == "番茄炒蛋"
    assert body["provenance"]["prompt_version"] == "extract_v2"
    assert body["provenance"]["model_output"]["title"] == "葱花蛋"
    assert count(session, Recipe) == 0 and count(session, ExtractionLog) == 0


def test_draft_carries_recipe_count(client):
    use_llm(FakeLLM(payload=PAYLOAD | {"recipe_count": 2}))
    response = client.post("/api/imports/text", json={"text": "番茄炒蛋"})
    assert response.json()["recipe_count"] == 2


def test_draft_recipe_count_defaults_to_one(client, fake_extraction):
    assert client.post("/api/imports/text", json={"text": "番茄炒蛋"}).json()["recipe_count"] == 1


def test_pasted_text_is_trimmed_for_the_model_and_the_stored_source(client, fake_extraction):
    body = post_text(client, fake_extraction, "\n\n  \n番茄炒蛋\n鸡蛋 番茄\n\n\n").json()
    assert body["provenance"]["input_text"] == "番茄炒蛋\n鸡蛋 番茄"


def test_draft_lines_carry_the_linked_entry_names(client, seeded, fake_extraction_with_unknown):
    lines = {
        line["name"]: line
        for line in client.post("/api/imports/text", json={"text": "x"}).json()["ingredients"]
    }
    # 葱花 is an alias, so the entry's own names differ from the line's wording.
    assert (lines["葱花"]["linked_name_zh"], lines["葱花"]["linked_name_en"]) == ("葱", "scallion")
    assert (lines["秘制酱"]["linked_name_zh"], lines["秘制酱"]["linked_name_en"]) == (None, None)


def test_draft_marks_matched_and_unmatched(client, seeded, fake_extraction_with_unknown):
    body = client.post("/api/imports/text", json={"text": "x"}).json()
    assert ids_by_name(body["ingredients"])["葱花"] is not None
    assert ids_by_name(body["ingredients"])["秘制酱"] is None
    cookware = {item["name"]: item["cookware_id"] for item in body["cookware"]}
    assert cookware["炒锅"] is not None and cookware["神奇锅"] is None


def test_text_with_a_link_keeps_the_first_link_as_source(client, fake_extraction):
    text = "番茄炒蛋，来自 https://a.example/1。还有 https://b.example/2"
    body = client.post("/api/imports/text", json={"text": text}).json()
    assert (body["source_type"], body["source_url"]) == ("text", "https://a.example/1")


def test_pasted_single_url_is_imported_as_web(client, fake_extraction, mock_site):
    body = client.post("/api/imports/text", json={"text": f"  {mock_site.url} "}).json()
    assert (body["source_type"], body["source_url"]) == ("web", mock_site.url)
    assert "番茄炒蛋的做法" in body["provenance"]["input_text"]


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("看这个 www.example.com/recipe/1 很好", "https://www.example.com/recipe/1"),
        ("xhslink.com/a/AbC12", "https://xhslink.com/a/AbC12"),
        ("https://example.com/x，然后", "https://example.com/x"),
        ("www.example.com/r", "https://www.example.com/r"),
        ("xhslink.com/a/AbC12好吃", "https://xhslink.com/a/AbC12"),
    ],
)
def test_first_url_recognizes_links_without_scheme(text, url):
    assert first_url(text) == url


@pytest.mark.parametrize("text", [
        "盐1.5/2勺",
        "比例 3.5/1",
        "v1.2 版本",
        "盐2.5kg/份",
        "1.5kg/只",
        "0.5ml/次",
        "1.5tsp/人",
    ])
def test_first_url_ignores_numbers_and_versions(text):
    assert first_url(text) is None


def test_single_www_link_is_imported_as_web(client, fake_extraction, monkeypatch):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return "番茄炒蛋的做法。" * 20

    monkeypatch.setattr("minemydinner.imports.service.fetch_page_text", fake_fetch)
    body = client.post("/api/imports/text", json={"text": " www.example.com/r "}).json()
    assert fetched == ["https://www.example.com/r"]
    assert (body["source_type"], body["source_url"]) == ("web", "https://www.example.com/r")


def test_text_with_a_scheme_less_link_keeps_it_as_source(client, fake_extraction):
    body = client.post("/api/imports/text", json={"text": "番茄炒蛋 xhslink.com/a/AbC12 好吃"}).json()
    assert (body["source_type"], body["source_url"]) == ("text", "https://xhslink.com/a/AbC12")


def test_url_import(client, fake_extraction, mock_site):
    body = client.post("/api/imports/url", json={"url": mock_site.url}).json()
    assert (body["source_type"], body["source_url"]) == ("web", mock_site.url)


def test_web_failure_suggests_pasting(client, mock_login_wall):
    response = client.post("/api/imports/url", json={"url": mock_login_wall.url})
    assert response.status_code == 422
    assert response.json() == {"code": "no_main_text", "suggest_paste": True}


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_text_is_rejected(client, fake_extraction, text):
    response = client.post("/api/imports/text", json={"text": text})
    assert response.status_code == 422 and response.json()["code"] == "empty_text"
    assert fake_extraction.tasks == []


def test_overlong_text_is_rejected_before_calling_a_model(client, fake_extraction):
    response = client.post("/api/imports/text", json={"text": "菜" * 20_001})
    assert response.status_code == 413 and response.json()["code"] == "text_too_large"
    assert fake_extraction.tasks == []


def test_screenshot_type_size_and_no_storage(client, fake_extraction, session):
    assert post_images(client, [("a.gif", b"x", "image/gif")]).status_code == 415
    too_big = [("a.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")]
    assert post_images(client, too_big).status_code == 413
    assert post_images(client, [("a.png", b"x", "image/png")] * 6).status_code == 422
    assert fake_extraction.tasks == []
    body = post_images(client, [("a.png", b"x", "image/png")] * 5).json()
    assert body["provenance"]["input_text"] == "[screenshot]"
    assert (body["source_type"], body["source_url"]) == ("screenshot", None)
    assert fake_extraction.tasks == ["image_understanding"]


def test_batch_keeps_other_items_when_one_fails(client, fake_extraction_fails_on_second):
    items = post_batch(client, "甲\n---\n乙\n---\n丙").json()["items"]
    assert [item["index"] for item in items] == [0, 1, 2]
    assert [bool(item["draft"]) for item in items] == [True, False, True]
    assert items[1]["error"] == "models_unavailable" and items[0]["error"] is None
    assert items[0]["draft"]["source_type"] == "batch"


def test_batch_never_fetches_links(client, fake_extraction, mock_site):
    items = post_batch(client, mock_site.url).json()["items"]
    assert items[0]["draft"]["source_url"] == mock_site.url
    assert mock_site.page_route.called is False


def test_batch_reads_gbk_file(client, fake_extraction):
    items = post_batch(client, "甲\n---\n乙", encoding="gb18030").json()["items"]
    assert len(items) == 2


def test_batch_file_errors_are_clear(client, fake_extraction):
    response = post_batch(client, "  ")
    assert response.status_code == 422
    assert response.json() == {"code": "empty", "suggest_paste": False}


def test_cap_and_outage_return_clear_errors(client, llm_over_cap, llm_down):
    over_cap = post_text(client, llm_over_cap)
    assert (over_cap.status_code, over_cap.json()["code"]) == (429, "monthly_cap")
    down = post_text(client, llm_down)
    assert (down.status_code, down.json()["code"]) == (503, "models_unavailable")
