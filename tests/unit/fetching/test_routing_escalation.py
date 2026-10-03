"""S2.5.10：SPA 根节点正则放宽 + 挑战页特征词收窄。"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.fetching.routing import browser_decision, needs_browser


def _result(body: bytes, *, content_type: str = "text/html") -> FetchResult:
    request = CrawlRequest("https://example.org/")
    return FetchResult(request, request.url, 200, {"content-type": content_type}, body, 0.1)


def test_spa_root_with_children_escalates() -> None:
    body = b"""
    <html><head><script src="a.js"></script><script src="b.js"></script><script src="c.js"></script></head>
    <body><div id="app"><header>Loading</header></div></body></html>
    """
    assert needs_browser(_result(body)) == (True, "页面主要内容需要 JavaScript 渲染")


def test_spa_root_empty_still_escalates() -> None:
    body = b"""
    <html><head><script>var x=1;</script><script>var y=2;</script><script>var z=3;</script></head>
    <body><main id="__next"></main></body></html>
    """
    assert needs_browser(_result(body)) == (True, "页面主要内容需要 JavaScript 渲染")


def test_spa_root_id_boundary_no_false_positive() -> None:
    body = b"""
    <html><head><script>var x=1;</script><script>var y=2;</script><script>var z=3;</script></head>
    <body><div id="app-footer">A normal footer with plenty of real content that is way longer than eighty characters so the page is clearly not an empty SPA shell</div></body></html>
    """
    assert needs_browser(_result(body)) == (False, "")


def test_weak_marker_inside_script_not_a_challenge() -> None:
    body = b"""
    <html><head><script>if (e.status === 'access denied') { retry(); }</script></head>
    <body><p>Welcome to the API documentation. This page contains a lot of useful text that is clearly longer than eighty characters and is definitely not a challenge page.</p></body></html>
    """
    assert needs_browser(_result(body)) == (False, "")


def test_weak_marker_in_visible_text_is_challenge() -> None:
    body = b"""
    <html><head></head><body><p>Access denied. Your request was blocked by the security system. Please contact support.</p></body></html>
    """
    assert needs_browser(_result(body)) == (True, "检测到验证页或访问挑战")


def test_strong_marker_anywhere_is_challenge() -> None:
    body = b"""
    <html><head><script>// cf-chl-widget</script></head><body><p>Checking your browser before accessing.</p></body></html>
    """
    assert needs_browser(_result(body)) == (True, "检测到验证页或访问挑战")


def test_challenge_documentation_keeps_http() -> None:
    body = b"<article>How captcha works. " + b"This is public reference material. " * 30 + b"</article>"
    assert browser_decision(_result(body)).code == "static_content"
    assert needs_browser(_result(body)) == (False, "")


def test_weak_marker_in_attribute_or_comment_is_not_visible():
    body = b'<p id="captcha">Welcome</p><!-- access denied -->'
    assert needs_browser(_result(body)) == (False, "")


def test_decision_codes_distinguish_shell_challenge_and_assets():
    shell = _result(b'<div id="root"></div><script></script><script></script><script></script>')
    assert browser_decision(shell).code == "javascript_shell"
    assert browser_decision(_result(b"<p>Access denied</p>")).code == "challenge_visible"
    assert browser_decision(_result(b"cf-chl-widget")).code == "challenge_signature"
    assert browser_decision(_result(b"captcha", content_type="application/json")).code == "non_html_or_asset"
