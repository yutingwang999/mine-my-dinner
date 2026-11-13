import httpx
import pytest

from minemydinner.imports.web import (
    USER_AGENT,
    WebImportError,
    fetch_page_text,
    min_main_text_chars,
)
from tests.webfakes import *  # noqa: F403


def assert_web_error(url: str, reason: str) -> None:
    with pytest.raises(WebImportError) as caught:
        fetch_page_text(url)
    assert caught.value.reason == reason


def test_extracts_main_text(mock_site):
    text = fetch_page_text(mock_site.url)
    assert "番茄炒蛋的做法" in text
    assert "版权所有" not in text


def test_sends_an_honest_user_agent(mock_site):
    fetch_page_text(mock_site.url)
    assert mock_site.page_route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert USER_AGENT == "MineMyDinnerBot/0.1"


def test_robots_disallow_stops_before_fetching_page(mock_site_disallowed):
    assert_web_error(mock_site_disallowed.url, "robots_disallowed")
    assert mock_site_disallowed.page_route.called is False


def test_robots_404_allows_and_robots_500_blocks(mock_site_robots_404, mock_site_robots_500):
    assert fetch_page_text(mock_site_robots_404.url)
    assert_web_error(mock_site_robots_500.url, "fetch_failed")
    assert mock_site_robots_500.page_route.called is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/x",
        "http://[::1]/x",
        "http://0.0.0.0/x",
    ],
)
def test_private_addresses_are_blocked(url):
    assert_web_error(url, "blocked_address")


def test_redirect_to_private_address_is_blocked(mock_redirect_to_private):
    assert_web_error(mock_redirect_to_private.url, "blocked_address")


def test_redirect_to_public_page_is_followed(mock_redirect_to_public):
    assert "番茄炒蛋的做法" in fetch_page_text(mock_redirect_to_public.url)


@pytest.mark.parametrize("url", ["ftp://x.com/a", "file:///etc/passwd", "not a url", ""])
def test_invalid_urls(url):
    assert_web_error(url, "invalid_url")


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("mock_pdf", "not_html"),
        ("mock_huge", "too_large"),
        ("mock_login_wall", "no_main_text"),
        ("mock_timeout", "fetch_failed"),
    ],
)
def test_failures_have_reasons(request, fixture, reason):
    assert_web_error(request.getfixturevalue(fixture).url, reason)


def test_short_chinese_page_is_accepted(router):
    body = f"<html><body><article><p>{'番茄炒蛋很好吃。' * 13}</p></article></body></html>"
    site_ = site(router, "short-zh.example", html(body))
    assert fetch_page_text(site_.url)


def test_short_english_page_is_still_rejected(router):
    body = f"<html><body><article><p>{'a tasty dish. ' * 8}</p></article></body></html>"
    site_ = site(router, "short-en.example", html(body))
    assert_web_error(site_.url, "no_main_text")


def test_empty_content_type_is_not_html(router):
    response = httpx.Response(200, content=b"<html></html>", headers={"Content-Type": ""})
    assert_web_error(site(router, "empty-type.example", response).url, "not_html")


def test_absent_content_type_still_tries_html(router):
    response = httpx.Response(200, content=RECIPE_HTML.encode("utf-8"))
    response.headers.pop("Content-Type", None)
    assert "番茄炒蛋的做法" in fetch_page_text(site(router, "no-type.example", response).url)


def test_min_main_text_chars_depends_on_cjk_share():
    assert min_main_text_chars("a" * 100) == 200
    assert min_main_text_chars("菜" * 100) == 80
    assert min_main_text_chars("菜" * 30 + "a" * 70) == 80
    assert min_main_text_chars("菜" * 29 + "a" * 71) == 200
    assert min_main_text_chars("菜 " * 30 + "a" * 70) == 80
    assert min_main_text_chars("") == 200
