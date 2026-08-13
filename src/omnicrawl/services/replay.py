"""限定重放：基于证据胶囊 + 归档 raw 重跑单个字段的提取动作（阶段 0 H3）。

设计决策（H3）：
- **不建 venv**：直接用当前解释器（``sys.executable``）跑子进程，脚本仅用项目自研
  stdlib 解析器（``html_tools``），子进程通过 stdin 收参数、stdout 收结果，避免 argv 转义。
- **完整性校验**：归档 HTML 的 sha256 必须等于胶囊 output.dom_hash，不一致视为
  ``dom_changed``（归档被改动/损坏），拒绝重放。
- **归档缺失**：responses.raw_path 为空或文件不存在 → ``archive_missing``。
- **10 秒超时**：防止病态 HTML/规则卡死；超时 → ``timeout``。

调用方（批 B-1 埋点）约定胶囊结构：
- action_type="extract_field"、action_name=字段名
- input = {"url", "item_selector"(可选), "rule"}（rule 为 _apply_rule 接受的规则 dict）
- output = {"dom_hash": sha256(html_bytes), "value", "trace"}
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..state.capsule_store import CapsuleStore
from ..state.state_store import StateStore

_REPLAY_TIMEOUT = 10.0

# 子进程脚本：sys.argv[1] = src 目录；stdin = {"html_path", "item_selector", "rule"}
# stdout = {"status": "ok"|"error", ...}。stdout 是唯一可靠通道，import 失败也走 error。
_REPLAY_SCRIPT = r"""
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
try:
    from omnicrawl.extraction.extractors import _apply_rule
    from omnicrawl.extraction.html_tools import parse_html, select_nodes
except Exception as exc:  # noqa: BLE001 —— 通道约定：任何失败都输出到 stdout
    print(json.dumps({"status": "error", "message": f"import_failed: {exc}"}))
    raise SystemExit(0)

params = json.load(sys.stdin)
try:
    html = pathlib.Path(params["html_path"]).read_text(encoding="utf-8", errors="replace")
    document = parse_html(html)
    context = document
    item_selector = params.get("item_selector") or ""
    if item_selector:
        items = select_nodes(document, item_selector)
        if not items:
            print(json.dumps({"status": "error", "message": "item_selector_no_match"}))
            raise SystemExit(0)
        context = items[0]
    value, trace = _apply_rule(context, params["rule"])
    print(json.dumps({"status": "ok", "value": value, "trace": trace}, ensure_ascii=False, default=str))
except Exception as exc:  # noqa: BLE001 —— 提取异常同样输出到 stdout
    print(json.dumps({"status": "error", "message": f"{type(exc).__name__}: {exc}"}))
"""


def _src_dir() -> str:
    """当前解释器下 omnicrawl 包的 src 根目录，供子进程 import 用。"""
    import omnicrawl

    return str(Path(omnicrawl.__file__).resolve().parent.parent)


def _dom_hash(html: bytes) -> str:
    return hashlib.sha256(html).hexdigest()


def replay_field(
    run_id: str,
    field: str,
    *,
    stage: str = "extract",
    store: StateStore,
    capsule_dir: Path | None = None,
    timeout: float = _REPLAY_TIMEOUT,
) -> dict[str, Any]:
    """限定重放：重放 run 中最近一次提取 ``field`` 的动作。

    流程：读胶囊 → 查归档 raw_path → dom_hash 完整性校验 → 子进程隔离提取。

    Args:
        run_id: 目标运行 ID。
        field: 要重放的字段名。
        stage: 胶囊阶段（默认 extract，生成 action_type="{stage}_field"）。
        store: StateStore，用于查 responses.raw_path。
        capsule_dir: 胶囊日志目录；None 时推断为 state 库同目录下 capsules/。
        timeout: 子进程超时秒数。

    Returns:
        dict：status 为 ok / no_capsule / archive_missing / dom_changed / timeout / error，
        附加 field / stage / run_id / url / dom_hash / value / trace / message 等字段。
    """
    base_dir = capsule_dir or (Path(store.path).parent / "capsules")
    capsules = CapsuleStore(base_dir).read(run_id)
    action_type = f"{stage}_field"
    matches = [
        capsule
        for capsule in capsules
        if capsule.action_type == action_type and capsule.action_name == field
    ]
    if not matches:
        return _result("no_capsule", run_id, field, stage)

    capsule = matches[-1]  # 最近一次提取动作
    input_data = capsule.input if isinstance(capsule.input, dict) else {}
    output_data = capsule.output if isinstance(capsule.output, dict) else {}
    url = input_data.get("url")
    rule = input_data.get("rule")
    expected_hash = output_data.get("dom_hash")

    if not isinstance(rule, dict) or not rule:
        return _result("error", run_id, field, stage, message="capsule_rule_missing")

    # ── 定位归档 raw ──────────────────────────────────────
    rows: list[dict[str, Any]] = []
    if url:
        rows = store.rows(
            "SELECT raw_path FROM responses WHERE final_url=? OR url=? "
            "ORDER BY id DESC LIMIT 1",
            (url, url),
        )
    raw_path = str(rows[0]["raw_path"]) if rows and rows[0].get("raw_path") else ""
    if not raw_path or not Path(raw_path).is_file():
        return _result("archive_missing", run_id, field, stage, url=url)

    # ── dom_hash 完整性校验 ────────────────────────────────
    html_bytes = Path(raw_path).read_bytes()
    current_hash = _dom_hash(html_bytes)
    if expected_hash and current_hash != expected_hash:
        return _result(
            "dom_changed", run_id, field, stage, url=url,
            dom_hash=current_hash, message="archive_hash_mismatch",
        )

    # ── 子进程隔离重放 ─────────────────────────────────────
    payload = json.dumps(
        {
            "html_path": raw_path,
            "item_selector": input_data.get("item_selector") or "",
            "rule": rule,
        },
        ensure_ascii=False,
        default=str,
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _REPLAY_SCRIPT, _src_dir()],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _result("timeout", run_id, field, stage, url=url, dom_hash=current_hash)
    except OSError as exc:
        return _result("error", run_id, field, stage, url=url, message=f"spawn_failed: {exc}")

    try:
        outcome = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _result(
            "error", run_id, field, stage, url=url,
            message=f"invalid_subprocess_output: {proc.stdout[:500]!r}",
        )
    if outcome.get("status") != "ok":
        return _result(
            "error", run_id, field, stage, url=url,
            message=outcome.get("message", "unknown"),
        )
    return _result(
        "ok", run_id, field, stage, url=url, dom_hash=current_hash,
        value=outcome.get("value"), trace=outcome.get("trace"),
    )


def _result(
    status: str,
    run_id: str,
    field: str,
    stage: str,
    *,
    url: str | None = None,
    dom_hash: str | None = None,
    value: Any = None,
    trace: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "field": field,
        "stage": stage,
    }
    if url is not None:
        result["url"] = url
    if dom_hash is not None:
        result["dom_hash"] = dom_hash
    if value is not None:
        result["value"] = value
    if trace is not None:
        result["trace"] = trace
    if message is not None:
        result["message"] = message
    return result


__all__ = ["replay_field", "_REPLAY_TIMEOUT"]
