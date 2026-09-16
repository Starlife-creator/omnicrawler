"""审计 medium/low 按「影响正确交付」筛选后的**修复验收**（W6.4 / §6.4 W6.4）。

## 背景

`docs/archive/audit-20260805/` 有 67 条 medium/low。方案要求**不做全量复核**，
只核「影响正确交付」的三类：**数据正确性 / 权限 / 输出保护**。筛出并核验后，
其中 5 条当时仍未闭环（其余已在早期批次修掉或属性能/资源主题、明确排除）：

| 条目 | 类别 | 本次处置 |
|---|---|---|
| `exporters.py` 扁平化键与基础字段同名互相覆盖 | 数据正确性 | 改「改名保留」`data.<key>` + 告警 |
| `_exports.py` 汇总阶段 `int(endpoints)` 可被插件值炸掉 | 数据正确性 | 宽容取整 `_as_int` |
| `plan_compiler.py` seeds 用 `str()` 强转出垃圾 URL | 数据正确性 | 显式拒绝非字符串元素 |
| `change_detector.py` `save_rules` 非原子写 | 输出保护 | 改用既有 `atomic_write` |
| `pdf_integration.py` 复用已存在的 project.yaml 不校验 | 输出保护 | 复用 `pdfx.load_config` 做 schema 校验 |

本文件逐条钉住修复行为（**不含**已闭环项的重复覆盖）。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline._exports import _as_int
from omnicrawler.pipeline.exporters import export_all
from omnicrawler.state import StateStore


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: exp, workspace: {str(tmp_path / 'work').replace(chr(92), '/')}}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract:\n  mode: html\n  fields:\n    title: {selector: title}\n"
        "outputs:\n  jsonl: true\n  csv: true\n  xlsx: false\n",
        encoding="utf-8",
    )
    return path


def _seed_record_with_colliding_key(tmp_path: Path) -> tuple[Path, str]:
    """种一条 data 里带 `record_id` 的记录 —— 它以前会**静默覆盖**基础列。"""
    config_path = _config(tmp_path)
    config = load_config(config_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", str(config_path))
        with state.conn:
            state.conn.execute(
                "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("real-id", run_id, "fp", "https://example.org/page", "item",
                 json.dumps({"record_id": "from-page", "title": "Alpha"}),
                 json.dumps({"raw": "Alpha"}), "now"),
            )
    return config_path, run_id


# ── ① 交付列不被静默覆盖 ────────────────────────────────────────────────


def test_data_keys_do_not_overwrite_base_columns(tmp_path: Path) -> None:
    config_path, run_id = _seed_record_with_colliding_key(tmp_path)
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        result = export_all(config, state, run_id)

    import csv as _csv

    csv_path = config.workspace / "output" / "records.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        values = next(iter(_csv.DictReader(handle)))

    assert values["record_id"] == "real-id", "基础列 record_id 被页面数据覆盖了"
    assert values["data.record_id"] == "from-page", "冲突字段应以 data.<key> 保留"
    assert values["title"] == "Alpha"

    # 冲突必须**可见**（不是静默改名）
    warnings = " ".join(str(w) for w in (result.get("warnings") or []))
    assert "record_id" in warnings, f"应给出冲突告警，实际：{result.get('warnings')}"


# ── ② 汇总阶段不被插件的非数字值打断 ────────────────────────────────────


def test_endpoint_sum_tolerates_non_numeric_values() -> None:
    assert _as_int(3) == 3
    assert _as_int("12") == 12
    assert _as_int("not-a-number") == 0
    assert _as_int(None) == 0
    assert _as_int({"nested": 1}) == 0
    assert _as_int(True) == 0, "布尔不是计数（True 会变成 1，语义错）"


# ── ③ 种子元素类型显式校验 ──────────────────────────────────────────────


def test_plan_compiler_rejects_non_string_seeds() -> None:
    import pytest

    from omnicrawler.pipeline_ops.plan_compiler import compile_task_plan
    from omnicrawler.pipeline_ops.task_ir import TaskIR

    ir = TaskIR(source={"kind": "static_html", "seeds": [{"url": "https://example.org/"}]})
    with pytest.raises(ValueError, match="种子元素必须是字符串"):
        compile_task_plan(ir)


# ── ④ 规则文件原子写 ───────────────────────────────────────────────────


def test_save_rules_writes_atomically(tmp_path: Path) -> None:
    """写入后：文件内容完整可解析、**不留临时文件**（原子替换的可见痕迹）。"""
    from omnicrawler.scheduling.change_detector import ChangeDetector

    target = tmp_path / "monitor_rules.json"
    ChangeDetector(tmp_path).save_rules(target)

    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == []
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"原子写不该留下临时文件：{leftovers}"


def test_save_rules_source_uses_atomic_write() -> None:
    """源码守卫：不得再退回直接 `write_text`（那正是"中断留半个 JSON"的成因）。"""
    source = (
        Path(__file__).resolve().parents[3]
        / "src" / "omnicrawler" / "scheduling" / "change_detector.py"
    ).read_text(encoding="utf-8")
    assert "atomic_write(target," in source
    assert "target.write_text(json.dumps" not in source


# ── ⑤ 复用已存在的 PDF 项目配置前先校验 ─────────────────────────────────


def test_pdf_project_config_is_validated_before_reuse(tmp_path: Path) -> None:
    import pytest

    from omnicrawler.pipeline_ops.pdf_integration import ensure_pdf_project

    config_path = _config(tmp_path)
    config = load_config(config_path)
    project = config.workspace / "pdf" / "project.yaml"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text("这不是合法的 PDF 项目配置: [未闭合\n", encoding="utf-8")

    with pytest.raises(ValueError, match="PDF项目配置无法加载"):
        ensure_pdf_project(config)
