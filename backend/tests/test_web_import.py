import pytest

from minemydinner.imports.web import USER_AGENT, WebImportError, fetch_page_text
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
