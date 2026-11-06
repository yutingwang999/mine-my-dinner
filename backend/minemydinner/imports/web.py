"""Fetch the main text of one public page the user gave, without bypassing any block."""

import ipaddress
import socket
import time
from contextlib import closing
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

USER_AGENT = "MineMyDinnerBot/0.1"
ROBOTS_AGENT = "MineMyDinnerBot"
TIMEOUT_SECONDS = 15
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024
MIN_MAIN_TEXT_CHARS = 200
MAX_REDIRECTS = 5
HTML_TYPES = ("text/html", "application/xhtml+xml")


class WebImportError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def _check_url(url: str) -> None:
    """Reject URLs that are not http(s) or that point at a non-public address."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 (raises ValueError on an invalid port)
    except ValueError as error:
        raise WebImportError("invalid_url") from error
    if parts.scheme not in ("http", "https") or not host:
        raise WebImportError("invalid_url")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(address) for address in resolve_host(host)]
        except (OSError, ValueError) as error:
            raise WebImportError("fetch_failed") from error
    # Every address must be public, so a host with one private address is refused.
    if not addresses or not all(address.is_global for address in addresses):
        raise WebImportError("blocked_address")


class _Fetcher:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.deadline = time.monotonic() + TIMEOUT_SECONDS
        self.robots: dict[str, RobotFileParser | None] = {}

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise WebImportError("fetch_failed")
        return remaining

    def _read(self, response: httpx.Response, limit: int) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise WebImportError("too_large")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise WebImportError("too_large")
            self._remaining()
        return bytes(body)

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            self.robots[origin] = self._load_robots(f"{origin}/robots.txt")
        parser = self.robots[origin]
        return parser is None or parser.can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, robots_url: str) -> RobotFileParser | None:
        """None means no rules apply, because the site has no robots.txt."""
        try:
            with closing(self.get(robots_url, check_robots=False)) as response:
                if 400 <= response.status_code < 500:
                    return None
                if response.status_code != 200:
                    raise WebImportError("fetch_failed")
                text = self._read(response, MAX_ROBOTS_BYTES).decode("utf-8", "replace")
        except WebImportError as error:
            if error.reason == "too_large":
                raise WebImportError("fetch_failed") from error
            raise
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser

    def get(self, url: str, check_robots: bool = True) -> httpx.Response:
        """GET with redirects followed by hand, checking every hop."""
        for _ in range(MAX_REDIRECTS + 1):
            _check_url(url)
            if check_robots and not self._allowed_by_robots(url):
                raise WebImportError("robots_disallowed")
            try:
                request = self.client.build_request(
                    "GET", url, timeout=httpx.Timeout(self._remaining())
                )
                response = self.client.send(request, stream=True)
            except httpx.HTTPError as error:
                raise WebImportError("fetch_failed") from error
            if not response.is_redirect:
                return response
            response.close()
            location = response.headers.get("Location")
            if not location:
                raise WebImportError("fetch_failed")
            url = urljoin(url, location)
        raise WebImportError("fetch_failed")


def fetch_page_text(url: str) -> str:
    url = url.strip()
    _check_url(url)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
        fetcher = _Fetcher(client)
        with closing(fetcher.get(url)) as response:
            if response.status_code != 200:
                raise WebImportError("fetch_failed")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            if content_type and content_type.lower() not in HTML_TYPES:
                raise WebImportError("not_html")
            try:
                body = fetcher._read(response, MAX_PAGE_BYTES)
            except httpx.HTTPError as error:
                raise WebImportError("fetch_failed") from error
    text = trafilatura.extract(body, url=url, include_comments=False)
    if not text or len(text.strip()) < MIN_MAIN_TEXT_CHARS:
        raise WebImportError("no_main_text")
    return text.strip()
