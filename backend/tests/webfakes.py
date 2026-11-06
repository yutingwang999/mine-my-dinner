from dataclasses import dataclass

import httpx
import pytest
import respx

PUBLIC_ADDRESS = "93.184.216.34"
RECIPE_HTML = """<html><head><title>番茄炒蛋</title></head><body>
<nav>首页 | 菜谱 | 登录</nav>
<article><h1>番茄炒蛋的做法</h1>
<p>番茄炒蛋的做法很简单。准备两个番茄、三个鸡蛋、少量葱花和适量的盐。番茄洗净后切成小块，鸡蛋打散并加一点盐。</p>
<p>锅里放油烧热，先倒入蛋液炒到凝固后盛出。再放一点油，下番茄块翻炒出汁，加入少量白糖调味。</p>
<p>最后把炒好的鸡蛋倒回锅里和番茄一起翻炒均匀，撒上葱花即可出锅。整个过程大约十五分钟，适合两个人吃。</p>
<p>小贴士：番茄选熟透的更容易出汁；鸡蛋不要炒得太老，凝固后马上盛出口感更嫩；喜欢汤汁多的可以加两勺清水。</p>
</article><footer>版权所有</footer></body></html>"""
LOGIN_HTML = "<html><body><form>请登录后查看完整菜谱</form></body></html>"


@dataclass
class MockSite:
    url: str
    page_route: respx.Route


def fake_resolve(host: str) -> list[str]:
    return ["127.0.0.1"] if host == "localhost" else [PUBLIC_ADDRESS]


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve test hosts to a public address without real DNS."""
    monkeypatch.setattr("minemydinner.imports.web.resolve_host", fake_resolve)


def site(router, host, page_response, robots_response=None) -> MockSite:
    base = f"https://{host}"
    router.get(f"{base}/robots.txt").mock(
        return_value=robots_response or httpx.Response(200, text="User-agent: *\nAllow: /\n")
    )
    page_route = router.get(f"{base}/recipe")
    if isinstance(page_response, Exception):
        page_route.mock(side_effect=page_response)
    else:
        page_route.mock(return_value=page_response)
    return MockSite(f"{base}/recipe", page_route)


def html(body: str = RECIPE_HTML) -> httpx.Response:
    return httpx.Response(200, html=body)


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def mock_site(router):
    return site(router, "recipes.example", html())


@pytest.fixture
def mock_site_disallowed(router):
    robots = httpx.Response(200, text="User-agent: MineMyDinnerBot\nDisallow: /\n")
    return site(router, "closed.example", html(), robots)


@pytest.fixture
def mock_site_robots_404(router):
    return site(router, "norobots.example", html(), httpx.Response(404))


@pytest.fixture
def mock_site_robots_500(router):
    return site(router, "brokenrobots.example", html(), httpx.Response(500))


@pytest.fixture
def mock_redirect_to_private(router):
    redirect = httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
    return site(router, "redirect.example", redirect)


@pytest.fixture
def mock_redirect_to_public(router):
    target = site(router, "target.example", html())
    redirect = httpx.Response(301, headers={"Location": target.url})
    return site(router, "moved.example", redirect)


@pytest.fixture
def mock_pdf(router):
    return site(router, "pdf.example", httpx.Response(200, content=b"%PDF-1.7", headers={
        "Content-Type": "application/pdf"
    }))


@pytest.fixture
def mock_huge(router):
    body = RECIPE_HTML + "<p>" + "很长" * (1024 * 1024) + "</p>"
    return site(router, "huge.example", html(body))


@pytest.fixture
def mock_login_wall(router):
    return site(router, "login.example", html(LOGIN_HTML))


@pytest.fixture
def mock_timeout(router):
    return site(router, "slow.example", httpx.ConnectTimeout("timed out"))
