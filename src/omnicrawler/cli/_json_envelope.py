"""统一 JSON 输出 envelope —— 让每个命令都能被 agent 可靠消费。

## 为什么需要（2026-11-23 调研结论）

clig.dev：「Anything that is machine readable should also go to `stdout`」。
而实测本项目 **55 个命令里只有 3 个支持 `--json`**（后加全局后 8 个 handler
真正读它）。对其它命令，agent 只能解析中文日志 —— 既脆弱又浪费上下文。

## 设计（借鉴 tooli 的 envelope，按本项目需要裁剪）

```json
{
  "ok": true,
  "command": "status",
  "exit_code": 0,
  "output": "原始人读文本（原样保留）",
  "error": null
}
```

失败时：

```json
{
  "ok": false,
  "command": "run",
  "exit_code": 1,
  "output": "...",
  "error": {
    "message": "配置缺少入口 URL",
    "suggestion": "在 source.seeds 中至少填写一个网址",
    "is_retryable": false
  }
}
```

**`is_retryable` 是关键字段**：agent 据此决定「原样重试 / 改输入 / 放弃」。
此前没有这个信息，agent 只能盲目重试。

## 为什么 `output` 保留原始文本而不是全结构化

真正的结构化需要每个 handler 自己定义字段（`status` 有任务状态、`export`
有导出计数……）。在 56 个 handler 上一次性做完风险太高，且会改变现有
用户可见输出。折中是：**envelope 保证「成败可机读」，内容暂留文本**，
由各 handler 逐步升级（已升级的会输出自己的 JSON，本模块检测到就透传）。

## 边界

* **不改 stdout 的正文语义**：`output` 就是原来会打到 stdout 的内容。
  人在终端看到的仍是它（不带 envelope 时）。
* 只在 `--json` 时启用；缺省行为完全不变。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from typing import Any

#: 判定「不可重试」的异常类型名（改输入才能解决）。
_INPUT_ERRORS = frozenset({"ValueError", "KeyError", "TypeError", "AttributeError"})


def _suggest(exc: BaseException) -> str:
    """给出可执行的下一步（与 `_print_error_hint` 同源思路）。"""
    name = type(exc).__name__
    if name == "FileNotFoundError":
        return "确认路径存在，或运行 omnicrawler init 创建配置"
    if name == "PermissionError":
        filename = getattr(exc, "filename", None)
        if filename:
            return f"确认 {filename} 可读写（只读介质请解压到可写位置）"
        return "确认目标站点允许访问，或检查网络策略"
    if name in _INPUT_ERRORS:
        return "修正参数或配置后重试（此类失败重试原样输入不会成功）"
    return "运行 omnicrawler doctor 诊断环境，或用 --log-level DEBUG 复现"


def _is_retryable(exc: BaseException) -> bool:
    """原样重试是否可能成功。

    输入类错误（ValueError/KeyError/…）**不可重试** —— 必须改输入；
    网络/超时/系统资源类**可重试**。
    """
    name = type(exc).__name__
    if name in _INPUT_ERRORS:
        return False
    if name in {"FileNotFoundError", "IsADirectoryError"}:
        return False
    return True


def _passthrough(captured: str) -> bool:
    """若 `captured` 本身已是合法 JSON 对象，原样打到 stdout 并返回 True。

    判定放宽到「以 `{` 开头」即可 —— `json.loads` 自己容忍首尾空白。
     早先要求 `endswith("}")`，遇到带尾随换行的输出就判不过。
    """
    stripped = (captured or "").strip()
    if not stripped.startswith("{"):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    print(stripped)
    return True


def build_payload(
    command: str,
    *,
    ok: bool,
    output: str,
    exit_code: int,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """构造 envelope。失败时带 `error` 三件套（message / suggestion / is_retryable）。"""
    payload: dict[str, Any] = {
        "ok": ok,
        "command": command,
        "exit_code": exit_code,
        "output": output,
        "error": None,
    }
    if exc is not None:
        payload["error"] = {
            "type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
            "suggestion": _suggest(exc),
            "is_retryable": _is_retryable(exc),
        }
    return payload


def run_with_envelope(command: str, handler: Any, args: argparse.Namespace) -> int:
    """执行 handler 并把结果包成 JSON 写到 stdout。

    返回退出码（调用方决定是否 `raise SystemExit`）。
    """
    buffer = io.StringIO()
    exc: BaseException | None = None
    try:
        with contextlib.redirect_stdout(buffer):
            handler(args)
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
        captured = buffer.getvalue()
        # **同样先试透传**：很多 handler 正常结束时会 `raise SystemExit(0)`，
        # 而它们可能已经输出了自己的 JSON。早先这里直接套 envelope，
        # 结果把 `capabilities` 的整段 JSON 塞进了 `output` 字符串
        # （agent 得多解析一层）。
        if _passthrough(captured):
            raise
        if not captured and code == 0:
            payload = build_payload(command, ok=True, output="", exit_code=0)
        else:
            payload = build_payload(
                command, ok=(code == 0), output=captured, exit_code=code,
                exc=None if code == 0 else RuntimeError(f"命令以 {code} 退出"),
            )
        print(json.dumps(payload, ensure_ascii=False))
        raise
    except BaseException as error:  # noqa: BLE001 - 要如实包进 envelope
        exc = error

    captured = buffer.getvalue()
    if _passthrough(captured):
        return 0 if exc is None else 1
    # 命令自己已输出结构化 JSON ⇒ **原样透传**，不再套一层 envelope。
    #
    # 判定要宽松：`json.loads` 本身就能容忍首尾空白，所以只要求「以 { 开头」，
    # 不要求 `endswith("}")` —— 早先用了后者，遇到带尾随换行/空格的输出就
    # 判不过，把整段 JSON 塞进了 envelope 的 `output` 字符串里（2026-11-23
    # 实测 `capabilities` 就是这样，agent 得多解析一层）。
    if exc is None:
        print(json.dumps(
            build_payload(command, ok=True, output=captured, exit_code=0),
            ensure_ascii=False,
        ))
        return 0

    print(json.dumps(
        build_payload(command, ok=False, output=captured, exit_code=1, exc=exc),
        ensure_ascii=False,
    ), file=sys.stdout)
    return 1
