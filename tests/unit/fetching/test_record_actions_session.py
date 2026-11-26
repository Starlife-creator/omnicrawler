"""录制动作时**同时保存登录态**（2026-11-26 补的能力）。

## 缺口

`record-actions` 原来只管录点击/输入，浏览器的 `storage_state` 随手关掉就丢了 ——
用户在里面登录一次，下次抓取仍然要重新登录。

而产品**本来就有**这套机制：`fetching/session_state.py` 是路径唯一真源，
`PlaywrightPool._new_context` 会按 context_key 自动加载快照。
缺的只是「录制这条路把快照存下来」这一步。

## 为什么路径必须走 `session_state`

若录制端自己拼一个路径，就会与爬取端指向两个文件 ⇒ **登录了也白登**，
而用户看不出区别（两边都显示成功）。所以这里钉的是「两端同一个路径」。
"""

from __future__ import annotations

import inspect

from omnicrawler.fetching import action_recorder


def test_record_with_playwright_accepts_save_session() -> None:
    sig = inspect.signature(action_recorder.record_with_playwright)
    assert "save_session" in sig.parameters
    assert sig.parameters["save_session"].default is True


def test_recorder_uses_the_shared_session_path_source() -> None:
    """★ 必须走 `session_state`，不能自己拼路径。

    这条是源码级断言：路径规则一旦复制成两份，`bug` 的表现是「登录了没用」，
    而不是任何异常 —— 单测如果只测「保存成功」，这种分叉永远发现不了。
    """
    source = inspect.getsource(action_recorder.record_with_playwright)
    assert "session_state.session_state_path" in source, "录制端必须复用共享路径真源"
    assert "session_crypto.save_storage_state" in source, "落盘必须走加密信封"


def test_session_failure_is_reported_not_swallowed() -> None:
    """存不了要报出来 —— 用户不该以为「登录态已经存下了」。"""
    source = inspect.getsource(action_recorder.record_with_playwright)
    assert "session_error" in source
    assert "logger.warning" in source, "保存失败必须有告警"
