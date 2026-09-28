"""三臂对照实验：量化自适应引擎各层的**边际贡献**。

A = L0 检测 + L1 幂等规范化 ｜ B = A + 基因池 fitness 择优 ｜ C = B + LLM 提新选择器
三臂只差在 scene 库内容，其余（页面字节 / 抽取配置 / 真实 Pipeline / 打分）完全相同。

实验设计、语料口径与一次实测结论见 `docs/BENCHMARKING.md` 的「三臂对照」一节。
未配 `OMNICRAWL_AI_*` 时 C 臂必然等于 B 臂（`AdaptiveExtractor` fail-closed），
工具会显式写明，不把「没差别」包装成「LLM 无用」。

```bash
python tools/benchmark_adaptive_arms.py
python tools/benchmark_adaptive_arms.py --task book-a-light-in-the-attic --json arms.json
python tools/benchmark_adaptive_arms.py --rounds 5 --refresh
```

退出码：0 观察到臂间差异（有结论）｜1 三臂一致（**无结论**，判红）｜2 选中 0 个任务
｜3 一个都没跑起来。详见 `docs/BENCHMARKING.md`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omnicrawler.core.config import load_config  # noqa: E402
from omnicrawler.core.models import ExtractedRecord  # noqa: E402
from omnicrawler.pipeline import Pipeline  # noqa: E402
from omnicrawler.services.quality_benchmark import (  # noqa: E402
    BenchmarkTask,
    QualityScore,
    score_records,
)
from omnicrawler.state.scene_store import SceneStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "benchmarks" / "adaptive_arms.yaml"

#: 三臂的规范顺序（报告与表格都按它排）
ARMS: tuple[str, ...] = ("A", "B", "C")

ARM_LABELS: dict[str, str] = {"A": "L0+L1", "B": "+基因池", "C": "+LLM"}

_COLUMNS = (
    ("task", 22), ("arm", 10), ("rec", 4), ("accuracy", 8), ("mean_field", 10),
    ("hit", 4), ("miss", 5), ("fitness", 8), ("逐轮accuracy", 22), ("择优结果", 40),
)


# ── 语料 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CorpusTask:
    """一个真实站点的对照任务（页面来自外网，真值由人工标注）。"""

    name: str
    site: str
    seeds: tuple[str, ...]
    source_kind: str
    identity_fields: tuple[str, ...]
    fields: dict[str, dict[str, Any]]
    gene_seed: str
    genes: dict[str, tuple[dict[str, str], ...]]
    expected: tuple[dict[str, str], ...]
    #: 逐条列表任务才需要；页面级单值槽位留空（基因池是页面级的，见语料文件头）
    item_selector: str = ""

    def score_task(self) -> BenchmarkTask:
        """转成打分用的 `BenchmarkTask`（`pages` 不参与打分，只喂真值与字段名）。"""
        return BenchmarkTask(
            name=self.name,
            pages=(),
            item_selector=self.item_selector,
            fields=tuple((name, str(rule.get("selector", ""))) for name, rule in self.fields.items()),
            expected=self.expected,
            identity_fields=self.identity_fields,
            source_kind=self.source_kind,
        )


def load_corpus(path: Path) -> list[CorpusTask]:
    """读语料并做形状校验（缺 `expected` / `identity_fields` 立刻判红，不静默跑）。"""
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    tasks: list[CorpusTask] = []
    for index, item in enumerate(raw.get("tasks") or []):
        missing = [
            key
            for key in ("name", "seeds", "identity_fields", "fields", "expected")
            if not item.get(key)
        ]
        if missing:
            raise ValueError(f"语料第 {index + 1} 个任务缺字段：{missing}")
        unknown = set(map(str, item["fields"])) - {str(k) for e in item["expected"] for k in e}
        if unknown:
            raise ValueError(f"语料任务 {item['name']} 声明了真值里不存在的字段：{sorted(unknown)}")
        tasks.append(
            CorpusTask(
                name=str(item["name"]),
                site=str(item.get("site", "")),
                seeds=tuple(str(seed) for seed in item["seeds"]),
                source_kind=str(item.get("source_kind", "static_html")),
                item_selector=str(item.get("item_selector", "")),
                identity_fields=tuple(str(x) for x in item["identity_fields"]),
                fields={str(k): dict(v) for k, v in dict(item["fields"]).items()},
                gene_seed=str(item.get("gene_seed", "working")),
                genes={
                    str(slot): tuple(dict(candidate) for candidate in candidates)
                    for slot, candidates in dict(item.get("genes") or {}).items()
                },
                expected=tuple(dict(e) for e in item["expected"]),
            )
        )
    return tasks


# ── 页面快照：抓一次，三臂共用 ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Snapshot:
    """一次抓取的结果：正文 + 内容指纹（可比性的锚）。"""

    url: str
    body: bytes
    sha256: str


def _fetch(url: str, cache: Path, *, refresh: bool) -> Snapshot:
    """抓页面并落缓存；命中缓存则复用（保证三臂输入逐字节相同）。"""
    import yaml

    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = cache / f"{key}.bin"
    meta = cache / f"{key}.json"
    if path.is_file() and meta.is_file() and not refresh:
        info = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        return Snapshot(url=url, body=path.read_bytes(), sha256=str(info.get("sha256", "")))

    request = urllib.request.Request(url, headers={"User-Agent": "omnicrawler-adaptive-arms@example.org"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - 语料 URL 是人工审过的
        body = response.read()
    digest = hashlib.sha256(body).hexdigest()
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    meta.write_text(yaml.safe_dump({"url": url, "sha256": digest}), encoding="utf-8")
    return Snapshot(url=url, body=body, sha256=digest)


class _SnapshotSite(BaseHTTPRequestHandler):
    """把快照字节喂给管线的本地站点（三臂共用同一份内容）。"""

    snapshot: Snapshot

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.snapshot.body)))
        self.end_headers()
        self.wfile.write(self.snapshot.body)

    def log_message(self, *_args: object) -> None:
        return


def _serve(snapshot: Snapshot) -> tuple[ThreadingHTTPServer, str]:
    handler = type("_ArmSite", (_SnapshotSite,), {"snapshot": snapshot})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


# ── 单臂执行 ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ArmRun:
    """一臂一次运行的全部读数。"""

    task: str
    arm: str
    score: QualityScore | None = None
    gene_hits: int = 0
    gene_misses: int = 0
    #: 基因库自己的账：平均适应度 + 择优结果（比 pipeline 指标更接近被研究对象）
    gene_fitness: float = 0.0
    gene_top: list[str] = field(default_factory=list)
    llm_proposals: int = 0
    llm_diverged: bool = False
    #: 逐轮 accuracy —— 收敛过程本身就是结论的一部分（fitness 何时起效）
    accuracy_history: list[float] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "task": self.task, "arm": self.arm, "status": self.status, "error": self.error,
            "input_sha256": self.input_sha256, "config_sha256": self.config_sha256,
            "gene_hits": self.gene_hits, "gene_misses": self.gene_misses,
            "gene_fitness": self.gene_fitness, "gene_top": self.gene_top,
            "llm_proposals": self.llm_proposals, "llm_diverged": self.llm_diverged,
            "accuracy_history": self.accuracy_history,
            "score": self.score.to_mapping() if self.score else None,
        }
    status: str = ""
    error: str = ""
    input_sha256: str = ""
    config_sha256: str = ""


def _config_for(task: CorpusTask, base_url: str, workspace: Path, *, scene: str) -> dict[str, Any]:
    """由任务生成配置——除 `scene` 外三臂逐字节相同（`config_sha256` 会暴露差异）。"""
    extract: dict[str, Any] = {
        "mode": "html",
        "scene": scene,
        "fields": {name: dict(rule) for name, rule in task.fields.items()},
    }
    if task.item_selector:
        extract["item_selector"] = task.item_selector
    return {
        "project": {"name": f"arms-{task.name}", "workspace": str(workspace)},
        "source": {"kind": task.source_kind, "seeds": [base_url]},
        "crawl": {
            "max_pages": 1,
            "max_depth": 1,
            "concurrency": 1,
            "same_host": True,
            "allow_domains": ["127.0.0.1"],
        },
        "http": {
            "user_agent": "omnicrawler-adaptive-arms@example.org",
            "respect_robots": False,
            "delay_seconds": 0,
            "allow_private_network": True,
            "retries": 0,
        },
        "extract": extract,
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }


def _gene_ledger(db: Path, scene: str) -> tuple[int, int, float, list[str]]:
    """读基因库**自己**的账：命中数、落空数、平均适应度，以及择优结果。

    ★ 刻意不去读 pipeline 指标，直接查 `selector_genes`：
      那才是被研究对象（基因池学到什么），而 pipeline 侧的计数器属于"被测代码自己
      的说法"。另外这样本工具就不依赖任何新增指标，可在任意基线上跑。
    """
    if not db.is_file():
        return 0, 0, 0.0, []
    from omnicrawler.quality.gene_pool import GenePool

    genes = GenePool(SceneStore(db)).top_genes(scene)
    if not genes:
        return 0, 0, 0.0, []
    hits = sum(gene.hits for gene in genes)
    misses = sum(gene.misses for gene in genes)
    picked = [f"{gene.slot_key}:{gene.selector}={gene.fitness:.2f}" for gene in genes[:3]]
    return hits, misses, round(sum(g.hits for g in genes) / max(1, hits + misses), 4), picked


def _records_of(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "output" / "records.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_genes(db: Path, task: CorpusTask, extra: dict[str, str] | None = None) -> None:
    """把语料基因（以及 C 臂的 LLM 提议）种进场景库。"""
    from omnicrawler.quality.gene_pool import GenePool

    store = SceneStore(db)
    pool = GenePool(store)
    for slot, candidates in task.genes.items():
        for candidate in candidates:
            pool.seed(
                task.name,
                slot,
                str(candidate.get("selector", "")),
                selector_type=str(candidate.get("selector_type", "css")),
            )
    for slot, selector in (extra or {}).items():
        pool.seed(task.name, slot, selector, selector_type="css")


def _run_pipeline(
    task: CorpusTask, base_url: str, workdir: Path, *, arm: str
) -> tuple[QualityScore, int, int, str, float, list[str]]:
    """跑一次真实管线并打分，返回 (得分, 基因命中, 基因落空, 配置指纹)。"""
    import yaml

    workspace = workdir / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    scene = "" if arm == "A" else task.name
    config = _config_for(task, base_url, workspace, scene=scene)
    config_path = workdir / "task.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    with Pipeline(load_config(config_path)) as pipeline:
        pipeline.run()
    hits, misses, fitness, picked = _gene_ledger(workspace / "scene.sqlite3", task.name)
    records = _records_of(workspace)
    score = score_records(
        task.score_task(),
        records,
        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        source_origin=base_url,
    )
    return score, hits, misses, score.config_sha256, fitness, picked


def _propose_with_llm(task: CorpusTask, base_url: str, workdir: Path) -> dict[str, str]:
    """C 臂的开拓步：B 臂跑完后，把仍然失效的字段交给 LLM 提新选择器。

    只收**经本地验证通过**的建议（`propose_repairs` 自身 fail-closed：验证不过不产出）。
    返回 字段 → 新选择器，供下一步作为新基因灌入。
    """
    from omnicrawler.extraction.adaptive_extractor import AdaptiveExtractor

    records = [
        ExtractedRecord(
            source_url=str(row.get("source_url", base_url)),
            record_type=str(row.get("record_type", "item")),
            data=dict(row.get("data") or {}),
            evidence=dict(row.get("evidence") or {}),
        )
        for row in _records_of(workdir / "work")
    ]
    if not records:
        return {}

    proposals = AdaptiveExtractor().propose_repairs("", records, {k: dict(v) for k, v in task.fields.items()})
    out: dict[str, str] = {}
    for proposal in proposals:
        if proposal.verified and proposal.rule_type == "css":
            out[proposal.field] = proposal.new_rule
    return out


# ── 主流程 ──────────────────────────────────────────────────────────────────


#: 轮次之间要保留下来的文件：基因适应度存在 `scene.sqlite3` 里，跨轮必须留存。
#: `-wal` / `-shm` 是 SQLite 的 WAL 伴生文件，**必须一起保留**——只留主文件会在下一轮
#: `PRAGMA journal_mode=WAL` 处报 `disk I/O error`（实测）。
#: 其余（`state.sqlite3` 的 URL 已处理集合、`run_control.json`、产物、日志）每轮清空 ——
#: 管线是**增量**的，同 workspace 再跑会认为页面已处理而产出 0 条记录（实测第 2 轮起
#: 记录数归零）。该累积的是基因适应度，不是抓取状态。
_ROUND_PERSIST = ("scene.sqlite3", "scene.sqlite3-wal", "scene.sqlite3-shm")


def _reset_workspace(workspace: Path) -> None:
    """清空 workspace，但保留基因库。"""
    if not workspace.is_dir():
        return
    for item in workspace.iterdir():
        if item.name in _ROUND_PERSIST:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def run_task(
    task: CorpusTask,
    *,
    arms: tuple[str, ...],
    cache: Path,
    workroot: Path,
    refresh: bool,
    rounds: int,
) -> list[ArmRun]:
    """跑一个任务的全部选中臂。

    ★ `rounds` 不是"多跑几次取平均"，而是**闭环的一部分**：基因池的 fitness 存在
      `scene.sqlite3` 里、跨轮累积，`recommend()` 按 `fitness DESC, hits DESC` 排序。
      单轮运行时所有基因 fitness 都是 0，排序退化为任意 ⇒ 测不到"择优"这个核心
      能力，只能测到"有没有基因"。所以每臂在**同一 workspace** 里连跑若干轮，
      让反馈真正积累。
    """
    snapshot = _fetch(task.seeds[0], cache, refresh=refresh)
    server, base_url = _serve(snapshot)
    runs: list[ArmRun] = []
    try:
        for arm in arms:
            run = ArmRun(task=task.name, arm=arm, input_sha256=snapshot.sha256)
            workdir = workroot / task.name / arm
            if workdir.exists():
                shutil.rmtree(workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                if arm != "A":
                    _seed_genes(workdir / "work" / "scene.sqlite3", task)
                history: list[float] = []
                score = None
                for index in range(max(1, rounds)):
                    if index:
                        _reset_workspace(workdir / "work")
                    score, hits, misses, config_sha, fitness, picked = _run_pipeline(
                        task, base_url, workdir, arm=arm
                    )
                    history.append(round(score.accuracy, 6))
                assert score is not None
                run.score, run.gene_hits, run.gene_misses = score, hits, misses
                run.accuracy_history = history
                run.gene_fitness = fitness
                run.gene_top = picked
                run.status = "scored"
                run.config_sha256 = config_sha

                if arm == "C":
                    extra = _propose_with_llm(task, base_url, workdir)
                    run.llm_proposals = len(extra)
                    if extra:
                        # 把 LLM 提议作为**新基因**灌进同一个补提路径，再跑一轮
                        _seed_genes(workdir / "work" / "scene.sqlite3", task, extra=extra)
                        score2, hits2, misses2, _, fitness, picked = _run_pipeline(
                            task, base_url, workdir, arm=arm
                        )
                        run.llm_diverged = score2.accuracy != score.accuracy
                        run.score, run.gene_hits, run.gene_misses = score2, hits2, misses2
                        run.gene_fitness, run.gene_top = fitness, picked
                        run.accuracy_history.append(round(score2.accuracy, 6))
            except Exception as exc:  # noqa: BLE001 — 单臂失败不阻断其它臂
                run.status, run.error = "error", f"{type(exc).__name__}: {exc}"
            runs.append(run)
    finally:
        server.shutdown()
        server.server_close()
    return runs


def _row(run: ArmRun) -> str:
    label = f"{run.arm}({ARM_LABELS.get(run.arm, run.arm)})"
    if run.score is None:
        return run.task.ljust(22) + label.ljust(10) + "失败".ljust(9) + run.error[:40]
    return (
        run.task.ljust(22)
        + label.ljust(10)
        + str(run.score.found_records).ljust(9)
        + f"{run.score.accuracy:.3f}".ljust(10)
        + f"{run.score.completeness:.3f}".ljust(13)
        + f"{run.score.mean_field_completeness:.3f}".ljust(12)
        + str(run.gene_hits).ljust(10)
        + str(run.gene_misses).ljust(11)
        + "→".join(f"{value:.3f}" for value in run.accuracy_history).ljust(26)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三臂对照：自适应引擎各层的边际贡献")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="语料 YAML 路径")
    parser.add_argument("--task", action="append", help="只跑指定任务（可重复；默认全部）")
    parser.add_argument("--arm", action="append", choices=ARMS, help="只跑指定臂（可重复；默认全部）")
    parser.add_argument("--json", dest="json_path", help="把报告写入该 JSON 文件")
    parser.add_argument("--cache", help="页面快照缓存目录（默认 <workdir>/snapshots）")
    parser.add_argument("--workdir", help="中间产物目录（默认系统临时目录）")
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="每臂在同一 workspace 连跑几轮，让 fitness 跨轮累积（默认 3；1 轮测不到择优）",
    )
    parser.add_argument("--refresh", action="store_true", help="强制重抓页面（默认复用缓存）")
    args = parser.parse_args(argv)

    try:
        corpus = load_corpus(Path(args.corpus))
    except (OSError, ValueError) as exc:
        print(f"语料不可用：{exc}", file=sys.stderr)
        return 3

    selected = [task for task in corpus if not args.task or task.name in args.task]
    if not selected:
        print(f"没有匹配的任务；可用：{[task.name for task in corpus]}", file=sys.stderr)
        return 2
    arms = tuple(arm for arm in ARMS if not args.arm or arm in args.arm)

    header = "".join(name.ljust(width) for name, width in _COLUMNS)
    print(header)
    print("-" * len(header))

    runs: list[ArmRun] = []
    with tempfile.TemporaryDirectory() as temp:
        base = Path(args.workdir) if args.workdir else Path(temp)
        cache = Path(args.cache) if args.cache else base / "snapshots"
        for task in selected:
            try:
                runs.extend(
                    run_task(
                        task,
                        arms=arms,
                        cache=cache,
                        workroot=base,
                        refresh=args.refresh,
                        rounds=args.rounds,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — 单任务失败不阻断其它任务
                runs.append(ArmRun(task=task.name, arm="-", status="error", error=f"{type(exc).__name__}: {exc}"))
            for run in runs[-len(arms) :]:
                print(_row(run))

    scored = [run for run in runs if run.score is not None]
    if not scored:
        print("\n没有任何一臂跑出结果：空集合不得判绿。", file=sys.stderr)
        return 3

    # 结论只看一件事：**臂间是否真的有差异**。全一致 ⇒ 这次运行没回答任何问题 ⇒ 判红。
    def fingerprint(run: ArmRun) -> tuple[float, float]:
        assert run.score is not None
        return (round(run.score.accuracy, 6), round(run.score.mean_field_completeness, 6))

    by_task: dict[str, list[ArmRun]] = {}
    for run in scored:
        by_task.setdefault(run.task, []).append(run)
    diverged = sorted(
        name for name, group in by_task.items() if len({fingerprint(r) for r in group}) > 1
    )

    print()
    print(f"跑出结果：{len(scored)}/{len(runs)} 次（臂 × 任务）")
    print(
        f"观察到臂间差异的任务：{diverged}" if diverged
        else "三臂结果完全一致：本次运行**没有回答任何问题**（语料可能缺少区分力）"
    )
    llm_runs = [run for run in scored if run.arm == "C"]
    if llm_runs and not sum(run.llm_proposals for run in llm_runs):
        print(
            "C 臂未产出任何 LLM 建议：未配 OMNICRAWL_AI_* 或建议未通过本地验证。"
            "此时 C ≡ B，**不能**据此说「LLM 无用」。"
        )
    elif llm_runs:
        improved = [r.task for r in llm_runs if r.llm_diverged]
        print(
            f"C 臂采纳 LLM 建议 {sum(r.llm_proposals for r in llm_runs)} 条；"
            f"其中实际改变准确率的任务：{improved or '无'}"
        )

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "runs": [run.to_mapping() for run in runs],
                    "diverged_tasks": diverged,
                    "selected_tasks": [task.name for task in selected],
                    "arms": list(arms),
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"报告：{target}")

    return 0 if diverged else 1


if __name__ == "__main__":
    raise SystemExit(main())
