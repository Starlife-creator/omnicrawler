"""首个任务旅程回归：建任务 → 试跑 → 运行 → 复核 → 导出。

**对应锚**：§二 第四阶段的验收要求是「创建、试跑、运行、复核、导出形成连贯体验」。
本文件承担其中的**自动回归**部分；「人工完整走查」是另一份证据，由维护者在实机完成
（两者缺一不可，不能互相替代）。

**覆盖范围与边界**（诚实声明）：
- 使用**真实的 GUI 组件与核心函数**：GUI 配置模型与序列化、`TaskCanvas`（画布与试跑回填）、
  `run_sample`（与 `SampleRunWorker` 同一函数）、`Pipeline`、`ResultTable`、`MarkdownExporter`。
- **不经真实点击与子进程**：全量运行在 GUI 里经 `_task_runner` 以子进程方式调用 CLI，
  在测试环境依赖外部命令可用性与进程时序，脆弱且慢；因此这里直接驱动同一份核心实现，
  断言"数据与状态是否连贯"，而不假装验证了"点击是否可用"。

因此本文件的断言重点是**环节之间的衔接**（试跑结果能否解锁运行、运行产物能否被复核与导出消费），
这正是旅程最容易断的地方。
"""

from __future__ import annotations

import csv
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from omnicrawler.core.config import load_config as load_core_config  # noqa: E402
from omnicrawler.export.markdown_exporter import MarkdownExporter  # noqa: E402
from omnicrawler.gui.core.config_model import CrawlConfig  # noqa: E402
from omnicrawler.gui.core.config_serializer import save_yaml  # noqa: E402
from omnicrawler.pipeline import Pipeline  # noqa: E402
from omnicrawler.pipeline_ops.preflight import run_sample  # noqa: E402

_PAGE = b"<html><head><title>Journey</title></head><body><h1>Item A</h1><p>body</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    """本地静态页服务：整条旅程离线可跑，不访问外网。"""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *_args):  # noqa: N802
        return


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui import i18n

    i18n.set_language("zh_CN")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def _build_task(tmp_path: Path, url: str) -> tuple[CrawlConfig, Path]:
    """① 建任务：GUI 配置模型 → YAML（与用户在工作台保存的是同一份产物）。"""
    config = CrawlConfig(
        project_name="journey",
        workspace=str(tmp_path / "work"),
        seed_urls=[url],
    )
    config.max_pages = 1
    yaml_path = tmp_path / "task.yaml"
    save_yaml(config, yaml_path)
    return config, yaml_path


def _offline_core_config(yaml_path: Path):
    """把配置调整成可在本机离线运行的形态（与既有 e2e 测试同一套开关）。"""
    core = load_core_config(yaml_path)
    core.raw["http"]["allow_private_network"] = True
    core.raw["http"]["respect_robots"] = False
    core.raw["http"]["delay_seconds"] = 0
    return core


def test_first_task_journey_is_connected_end_to_end(qt_app, tmp_path: Path, local_site: str) -> None:
    """五个环节串起来：每一步的产物都能被下一步消费。"""
    from omnicrawler.gui.views.result_table import CsvStreamModel
    from omnicrawler.gui.views.task_canvas import TaskCanvas

    config, yaml_path = _build_task(tmp_path, local_site)

    # ② 试跑：与 GUI 的 SampleRunWorker 调用同一个函数，独立工作区、不动正式断点
    sample_result = run_sample(_offline_core_config(yaml_path), pages=1)["sample"]
    assert sample_result.get("status") == "succeeded", sample_result
    assert int(sample_result.get("processed", 0)) >= 1, sample_result

    # ③ 试跑回填画布 → 解锁「开始全量运行」（旅程最易断的衔接点）
    canvas = TaskCanvas(config, project_root=str(tmp_path))
    assert canvas.trial_matches_fields() is False, "未试跑前不应放行全量运行"
    canvas.set_trial_result(True, "试跑通过", sample_result)
    assert canvas.trial_matches_fields() is True, "试跑通过后应解锁全量运行"

    # ④ 运行：产出记录
    core = _offline_core_config(yaml_path)
    with Pipeline(core) as pipeline:
        summary = pipeline.run()
    assert summary["status"] == "succeeded", summary
    assert int(summary["records"]) >= 1, summary

    workspace = Path(config.workspace)
    csv_path = next(
        (p for p in (workspace / "output" / "records.csv", workspace / "records.csv") if p.is_file()),
        None,
    )
    assert csv_path is not None, "运行后未产出 records.csv"
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "records.csv 无数据行"
    assert "url" in rows[0], f"records.csv 缺少 url 列：{list(rows[0])}"

    # ⑤ 复核：结果表的数据层能消费运行产物（与 MainWindow._auto_load_results 同一模型；
    # 用同步的 load_file 而非异步 load_csv，避免测试依赖事件循环时序）
    model = CsvStreamModel()
    assert model.load_file(csv_path) is True, "结果表未能加载运行产物"
    assert model.total_rows == len(rows), "结果表行数与产物不一致"
    assert model.columnCount() == len(rows[0]), "结果表列数与产物不一致"

    # ⑥ 导出：Markdown 交付物（MainWindow._export_markdown 的同一核心调用）
    target = csv_path.with_name("records.md")
    MarkdownExporter.export_results(
        csv_path=csv_path,
        jsonl_path=None,
        output_path=target,
        include_evidence=True,
    )
    assert target.is_file(), "导出未生成 records.md"
    text = target.read_text(encoding="utf-8")
    assert text.strip(), "records.md 为空"
    assert "url" in text, "records.md 未包含字段名（导出内容不完整）"


def test_run_gate_blocks_after_trial_and_field_change(qt_app, tmp_path: Path, local_site: str) -> None:
    """旅途边界：试跑通过后若改动采集范围/字段，全量运行的放行必须失效。

    这是「试跑不是形式」的保证——否则用户会带着未验证的配置直接跑全量。
    """
    from omnicrawler.gui.views.task_canvas import TaskCanvas

    config, yaml_path = _build_task(tmp_path, local_site)
    sample_result = run_sample(_offline_core_config(yaml_path), pages=1)["sample"]
    canvas = TaskCanvas(config, project_root=str(tmp_path))
    canvas.set_trial_result(True, "试跑通过", sample_result)
    assert canvas.trial_matches_fields() is True

    # 改动采集范围（等价于用户改了 URL 或页数）→ 试跑结论应失效
    canvas.restart()
    assert canvas.trial_matches_fields() is False, "采集范围变更后仍放行全量运行"
