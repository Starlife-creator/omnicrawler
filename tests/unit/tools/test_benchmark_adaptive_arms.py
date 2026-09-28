"""`tools/benchmark_adaptive_arms.py` 的入口契约与实验设计守卫。

这个工具回答的是「自适应引擎到底有没有用」，所以它自己的判据必须先站得住：

* **空集合判红**：``--task`` 没匹配到 ⇒ 退出码 2；一个任务都没跑起来 ⇒ 3。
* **★ 无差异判红**：三臂结果完全一致 ⇒ 退出码 1。此时这次运行**没有回答任何问题**
  （语料缺少区分力 / 基因没生效 / 页面没抓到），若按"没报错"判绿，空实验会被当成
  正结果 —— 这是基准工具最危险的失败模式。
* **有差异判绿**：臂间出现差异 ⇒ 0。
* **轮次隔离**：轮次之间必须保留基因库（含 SQLite 的 ``-wal``/``-shm`` 伴生文件），
  否则适应度丢失或直接 ``disk I/O error``；同时必须清掉抓取状态，否则管线按增量
  语义认为页面已处理、第 2 轮起产出 0 条记录。
* **语料自身**：随包语料必须能加载、字段与真值一致、且**有区分力**（working 与
  stale 两类任务都在）。

本文件**不联网、不跑真实管线**（那需要外网与快照），全部靠注入。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "benchmark_adaptive_arms.py"
_CORPUS = _REPO_ROOT / "benchmarks" / "adaptive_arms.yaml"

_GENE_METRICS = ("omnicrawler_gene_augment_hits_total", "omnicrawler_gene_augment_misses_total")


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_arms_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load_tool()


def _score(task: str, accuracy: float, mean_field: float):
    from omnicrawler.services.quality_benchmark import QualityScore

    return QualityScore(
        task=task,
        expected_records=1,
        found_records=1,
        matched_records=1,
        completeness=1.0,
        accuracy=accuracy,
        evidence_ratio=1.0,
        mean_field_completeness=mean_field,
    )


def _fake_run(
    tool: ModuleType, task: str, arm: str, accuracy: float, mean_field: float, **kw
) -> object:
    run = tool.ArmRun(task=task, arm=arm, accuracy_history=[accuracy], status="scored")
    run.score = _score(task, accuracy, mean_field)
    for key, value in kw.items():
        setattr(run, key, value)
    return run


# ── 语料 ────────────────────────────────────────────────────────────────────


def test_shipped_corpus_loads_and_has_discriminating_tasks(tool: ModuleType) -> None:
    """随包语料必须同时含"有可用候选"与"候选全失效"两类任务。

    只有一类 ⇒ 实验跑不出差异（全部任务 A==B==C），工具会判红，而那是**语料**的问题，
    不该等到跑完 15 次管线运行才发现。
    """
    tasks = tool.load_corpus(_CORPUS)
    assert len(tasks) >= 2
    kinds = {task.gene_seed for task in tasks}
    assert kinds == {"working", "stale"}, f"语料缺少区分力，两类 seed 都在才有对照：{kinds}"
    for task in tasks:
        assert task.expected, f"{task.name} 真值为空"
        assert task.genes, f"{task.name} 没有基因候选"
        for slot, candidates in task.genes.items():
            assert slot in task.fields, f"{task.name} 的基因槽位 {slot} 不在 fields 里"
            assert len(candidates) >= 2, (
                f"{task.name}/{slot} 只给了 {len(candidates)} 个候选："
                "单候选测不到「择优」，只能测到「有没有基因」"
            )


def test_corpus_truth_covers_every_declared_field(tool: ModuleType) -> None:
    for task in tool.load_corpus(_CORPUS):
        for expected in task.expected:
            missing = set(task.fields) - set(expected)
            assert missing == set(), f"{task.name} 真值缺字段 {sorted(missing)}"


def test_corpus_rejects_incomplete_task(tool: ModuleType, tmp_path: Path) -> None:
    """缺 `expected` / `identity_fields` 必须在加载时判红，不静默跑出无意义结果。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\ntasks:\n  - name: x\n    seeds: ['https://e.org/']\n    fields: {a: {selector: h1}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="缺字段"):
        tool.load_corpus(bad)


def test_corpus_rejects_field_absent_from_truth(tool: ModuleType, tmp_path: Path) -> None:
    bad = tmp_path / "bad2.yaml"
    bad.write_text(
        "version: 1\n"
        "tasks:\n"
        "  - name: x\n"
        "    seeds: ['https://e.org/']\n"
        "    identity_fields: [t]\n"
        "    fields: {t: {selector: h1}, ghost: {selector: h2}}\n"
        "    expected:\n      - {t: 'A'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="真值里不存在的字段"):
        tool.load_corpus(bad)


# ── 退出码：空集合与无差异都必须判红 ───────────────────────────────────────


def test_unknown_task_is_red(tool: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--task", "no-such-task"]) == 2
    assert "没有匹配的任务" in capsys.readouterr().err


def test_missing_corpus_is_red(tool: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--corpus", "/nonexistent/corpus.yaml"]) == 3
    assert "语料不可用" in capsys.readouterr().err


def test_all_arms_identical_is_red(tool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 无差异判红：这次运行没回答任何问题，不能当正结果。"""
    task = tool.load_corpus(_CORPUS)[0]

    def _same(task_, **_kw):
        return [_fake_run(tool, task_.name, arm, 0.333, 0.333) for arm in tool.ARMS]

    monkeypatch.setattr(tool, "run_task", _same)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name]) == 1


@pytest.mark.parametrize(
    ("accuracies", "expected_exit"),
    [((0.333, 1.0, 1.0), 0), ((1.0, 1.0, 1.0), 1)],
    ids=["gene-pool-helps", "no-effect"],
)
def test_exit_code_tracks_observed_divergence(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    accuracies: tuple[float, float, float],
    expected_exit: int,
) -> None:
    """★ 反向断言：退出码必须跟着**观察到的臂间差异**走。

    `no-effect` 那条是 fail-open 的反向用例——若实现只按"没抛异常"判绿，
    差异为 0 也会返回 0，这条必须转红。
    """
    task = tool.load_corpus(_CORPUS)[0]

    def _runs(task_, **_kw):
        return [
            _fake_run(tool, task_.name, arm, accuracy, accuracy)
            for arm, accuracy in zip(tool.ARMS, accuracies, strict=True)
        ]

    monkeypatch.setattr(tool, "run_task", _runs)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name]) == expected_exit


def test_mean_field_alone_counts_as_divergence(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 差异判定不能只看 `accuracy`。

    补提把字段**填上**时 `mean_field_completeness` 会先动；若值与真值不一致，
    `accuracy` 可以纹丝不动（实测：基因命中但值对不上）。只看 accuracy 会把
    "补提确实起了作用"误判成"完全无差异"。
    """
    task = tool.load_corpus(_CORPUS)[0]

    def _runs(task_, **_kw):
        return [
            _fake_run(tool, task_.name, "A", 0.333, 0.333),
            _fake_run(tool, task_.name, "B", 0.333, 1.0),
            _fake_run(tool, task_.name, "C", 0.333, 1.0),
        ]

    monkeypatch.setattr(tool, "run_task", _runs)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name]) == 0


def test_all_arms_failed_is_red(tool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    task = tool.load_corpus(_CORPUS)[0]

    def _broken(task_, **_kw):
        run = tool.ArmRun(task=task_.name, arm="-", status="error", error="boom")
        return [run]

    monkeypatch.setattr(tool, "run_task", _broken)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name]) == 3


# ── 快照：三臂必须看到同一份字节 ────────────────────────────────────────────


def test_snapshot_is_cached_and_fingerprinted(tool: ModuleType, tmp_path: Path) -> None:
    """抓一次、缓存复用、`sha256` 稳定 —— 没有输入快照，三臂数字不可比。"""
    import hashlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    body = b"<html><body><h1>cached</h1></body></html>"
    served = {"count": 0}

    class _H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            served["count"] += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        first = tool._fetch(url, tmp_path / "cache", refresh=False)
        second = tool._fetch(url, tmp_path / "cache", refresh=False)
    finally:
        server.shutdown()
        server.server_close()

    assert served["count"] == 1, "第二次取用应命中缓存，不得重新抓取"
    assert first.body == second.body == body
    assert first.sha256 == second.sha256 == hashlib.sha256(body).hexdigest()


# ── 轮次隔离 ────────────────────────────────────────────────────────────────


def test_reset_workspace_keeps_gene_db_and_its_wal_sidecars(tool: ModuleType, tmp_path: Path) -> None:
    """基因库必须留存（含 ``-wal``/``-shm``），否则适应度丢失或直接 disk I/O error。"""
    workspace = tmp_path / "work"
    (workspace / "output").mkdir(parents=True)
    for name in ("scene.sqlite3", "scene.sqlite3-wal", "scene.sqlite3-shm",
                 "state.sqlite3", "run_control.json"):
        (workspace / name).write_text("x", encoding="utf-8")
    (workspace / "output" / "records.jsonl").write_text("{}", encoding="utf-8")

    tool._reset_workspace(workspace)

    for name in ("scene.sqlite3", "scene.sqlite3-wal", "scene.sqlite3-shm"):
        assert (workspace / name).is_file(), f"{name} 被清掉了"
    # 抓取状态必须清：管线是增量的，留着会让第 2 轮起产出 0 条记录
    assert not (workspace / "state.sqlite3").exists()
    assert not (workspace / "run_control.json").exists()
    assert not (workspace / "output").exists()


# ── 报告形状 ────────────────────────────────────────────────────────────────


def test_json_report_records_runs_and_fingerprints(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    task = tool.load_corpus(_CORPUS)[0]
    report = tmp_path / "report.json"

    def _runs(task_, **_kw):
        return [
            _fake_run(tool, task_.name, "A", 0.333, 0.333, input_sha256="deadbeef"),
            _fake_run(tool, task_.name, "B", 1.0, 1.0, input_sha256="deadbeef"),
            _fake_run(tool, task_.name, "C", 1.0, 1.0, input_sha256="deadbeef"),
        ]

    monkeypatch.setattr(tool, "run_task", _runs)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name, "--json", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert [item["arm"] for item in payload["runs"]] == list(tool.ARMS)
    assert {item["input_sha256"] for item in payload["runs"]} == {"deadbeef"}
    assert payload["diverged_tasks"] == [task.name]
    assert payload["runs"][0]["accuracy_history"] == [0.333]


def test_llm_absent_is_reported_not_silently_equal(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """C 臂因未配 LLM 而等于 B 臂时，必须**显式说明**，不得让读者误读成"LLM 无用"。"""
    task = tool.load_corpus(_CORPUS)[0]

    def _runs(task_, **_kw):
        return [
            _fake_run(tool, task_.name, "A", 0.333, 0.333),
            _fake_run(tool, task_.name, "B", 1.0, 1.0),
            _fake_run(tool, task_.name, "C", 1.0, 1.0, llm_proposals=0),
        ]

    monkeypatch.setattr(tool, "run_task", _runs)
    monkeypatch.setattr(tool, "_fetch", lambda *_a, **_k: None)

    assert tool.main(["--task", task.name]) == 0
    out = capsys.readouterr().out
    assert "C ≡ B" in out
    assert "未配 OMNICRAWL_AI_*" in out
    assert "据此说「LLM 无用」" in out
