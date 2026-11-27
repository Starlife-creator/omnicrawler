"""会话落盘的「无变化不重写」守卫（2026-11-27）。

## 为什么

`HTTPFetcher.fetch` 每取一页都调一次 `CookieSession.save()`。落盘要做：登录态 jar
序列化 → AES-GCM 加密 → 写临时文件 → `os.replace` → `chmod`，实测 **1.8 ms/次**。
600 页的翻页任务里约 **1.1 秒** 纯粹在重写一个字节都没变的文件；顺带让杀毒/索引
服务反复处理同一个路径。

## 这些用例守什么

★ 判据是「**内容没变就不写，变了立刻写**」两头都要守住：
只守"不写"会漏掉真正的 cookie 更新（登录态丢失 = 功能坏）；
只守"要写"则等于什么都没优化。
"""

from __future__ import annotations

import os
import time
from http.cookiejar import Cookie
from pathlib import Path

from omnicrawler.fetching.session import CookieSession


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        0, name, value, None, False, "example.org", False, False, "/", True,
        False, None, False, None, None, {},
    )


def _session(tmp_path: Path) -> CookieSession:
    return CookieSession(tmp_path / "s.cookies")


def test_second_save_without_changes_does_not_touch_the_file(tmp_path: Path) -> None:
    """★ 没变 ⇒ 不重写。判据用 **mtime**，不是"函数返回值" —— 返回值两边都是 True。"""
    session = _session(tmp_path)
    session.jar.set_cookie(_cookie("sid", "abc"))
    assert session.save() is True
    path = session.path
    assert path is not None and path.is_file()
    first_mtime = path.stat().st_mtime_ns
    time.sleep(0.01)
    assert session.save() is True
    assert path.stat().st_mtime_ns == first_mtime, "内容没变却重写了文件"


def test_changed_cookie_is_written_immediately(tmp_path: Path) -> None:
    """反方向：cookie 变了**必须**落盘，否则登录态会丢。"""
    session = _session(tmp_path)
    session.jar.set_cookie(_cookie("sid", "abc"))
    session.save()
    session.jar.set_cookie(_cookie("sid", "def"))
    assert session.save() is True
    reopened = CookieSession(session.path)
    values = {cookie.name: cookie.value for cookie in reopened.jar}
    assert values.get("sid") == "def", values


def test_new_cookie_triggers_a_write(tmp_path: Path) -> None:
    """新增 cookie（不是改值）同样要写 —— 指纹覆盖整个集合，不只是"某个值变了"。"""
    session = _session(tmp_path)
    session.jar.set_cookie(_cookie("sid", "abc"))
    session.save()
    session.jar.set_cookie(_cookie("csrf", "xyz"))
    session.save()
    reopened = CookieSession(session.path)
    names = {cookie.name for cookie in reopened.jar}
    assert {"sid", "csrf"} <= names, names


def test_explicit_save_after_load_does_not_rewrite(tmp_path: Path) -> None:
    """★ 重新加载已有文件后立刻 save：内容与磁盘一致，不该再写一次。

    这条防的是"指纹只在 save 里更新、不在 load 里初始化"的写法 ——
    那样每次新会话的第一次 save 都会多写一遍。
    """
    first = _session(tmp_path)
    first.jar.set_cookie(_cookie("sid", "abc"))
    first.save()
    path = first.path
    assert path is not None
    before = path.stat().st_mtime_ns

    reloaded = CookieSession(path)
    time.sleep(0.01)
    assert reloaded.save() is True
    assert path.stat().st_mtime_ns == before, "加载后立刻保存，内容没变却重写了"


def test_save_is_fast_when_nothing_changed(tmp_path: Path) -> None:
    """量级护栏：跳过写盘后单次 save 应远低于一次真实落盘（1.8 ms）。

    ★ 阈值刻意宽松（0.5 ms 对 1.8 ms 是 3.6 倍余量），只守"确实跳过了加密写盘"。
    """
    session = _session(tmp_path)
    for index in range(20):
        session.jar.set_cookie(_cookie(f"k{index}", f"v{index}"))
    session.save()
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        session.save()
        samples.append((time.perf_counter() - started) * 1000)
    assert min(samples) < 0.5, f"无变化时 save 仍要 {min(samples):.3f} ms"


def test_no_path_session_is_still_a_noop() -> None:
    """不持久化的会话（persist_cookies=false）行为不变。"""
    session = CookieSession(None)
    assert session.save() is False
