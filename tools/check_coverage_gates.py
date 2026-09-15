from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

Matcher = Callable[[str], bool]


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _is_one_of(*paths: str, prefixes: tuple[str, ...] = ()) -> Matcher:
    """精确路径集合，外加可选前缀族（P1-3 拆分后的「模块族」用前缀覆盖）。"""
    expected = {_normalise(path) for path in paths}
    expanded = tuple(_normalise(prefix) for prefix in prefixes)
    return lambda path: path in expected or path.startswith(expanded)


def _starts_with(*prefixes: str) -> Matcher:
    expected = tuple(_normalise(prefix) for prefix in prefixes)
    return lambda path: path.startswith(expected)


GATES: dict[str, tuple[float, Matcher]] = {
    "security_and_state": (
        90.0,
        # state_store 自 P1-3 起是「门面 + 6 个 Mixin」，用前缀纳入全部模块，
        # 否则门禁只能看到 103 行门面、约 900 行实现漏出视野。
        _is_one_of(
            "src/omnicrawler/security/policy.py",
            "src/omnicrawler/security/egress.py",  # 出口管控核心（S37 补漏）
            "src/omnicrawler/fetching/archives.py",
            "src/omnicrawler/core/migrations.py",
            prefixes=("src/omnicrawler/state/state_store",),
        ),
    ),
    "pipeline_http_sources": (
        80.0,
        _starts_with(
            "src/omnicrawler/pipeline/",
        ),
    ),
    "pipeline_http_client": (
        86.0,
        _is_one_of(
            # 曾把不存在的 src/omnicrawler/http_client.py 列在此处——门禁在查空集合
            # （S37 死路径），已移除；真实路径为 fetching/http_client.py
            "src/omnicrawler/fetching/http_client.py",
            "src/omnicrawler/sources/sources.py",
        ),
    ),
    "browser_and_api": (
        74.0,
        # browser_fetcher 自 P1-3 起拆出 browser_engines/guards/pool，同样用前缀纳入。
        _is_one_of(
            "src/omnicrawler/extraction/api_discovery.py",
            prefixes=("src/omnicrawler/fetching/browser_",),
        ),
    ),
    "pdf_and_ocr": (
        72.0,
        lambda path: path.startswith("src/omnicrawler/pdfx/")
        or path
        in {
            "src/omnicrawler/pipeline_ops/pdf_integration.py",
            "src/omnicrawler/pipeline_ops/pdf_region.py",
            "src/omnicrawler/apps/pdf_processor.py",
            "src/omnicrawler/apps/field_extractor.py",
        },
    ),
    "desktop_core": (
        77.0,
        lambda path: path.startswith("src/omnicrawler/gui/core/")
        or path
        in {
            "src/omnicrawler/gui/runner/worker_task_runner.py",
            "src/omnicrawler/gui/wizard/step1_source.py",
            "src/omnicrawler/gui/wizard/step2_urls.py",
            "src/omnicrawler/gui/wizard/step4_download.py",
            "src/omnicrawler/gui/wizard/step5_preview.py",
        },
    ),
}

OVERALL_COVERAGE_GATE = 73.0

# 按顶层子包设「只降不升」下限（P2-1 ratchet）。
#
# ★ **取值口径（2026-09-15 定）：CI 三平台实测的最小值 − 2**。
#   旧口径是「本地实测 − 8」，其前提是「CI 少了 browser/extras ⇒ 系统性低于本地」；
#   该前提**已被实测推翻**：CI 现在装了 storage extra、GUI 端到端用例也不再被跳过
#   （含拆掉的掩盖型 skip），`all_source` 实测 75.53%，与本地 74.46% 无系统性差距。
#   实测（同日 green run，三个平台同时跑）：
#     all_source            75.55 / 75.53 / 75.55   → min 75.53
#     security_and_state    92.01 / 92.01 / 92.01   → min 92.01
#     pipeline_http_sources 82.67 / 82.67 / 82.67   → min 82.67
#     pipeline_http_client  88.37 / 88.37 / 88.37   → min 88.37
#     browser_and_api       76.75 / 76.75 / 76.75   → min 76.75
#     pdf_and_ocr           74.58 / 74.58 / 74.43   → min 74.43
#     desktop_core          79.73 / 79.66 / 79.73   → min 79.66
#   跨平台离散 ≤ 0.15 点 ⇒ 余量取 **2 点**（≈ 13× 实测离散），既不 flaky 又不再留 8 点空档。
#   **只升不降**：以后每次收紧都要按新的实测重算并把数字写在这里。
# 先覆盖方案点名的三个「非 GUI、测试更便宜」的包，其余包待后续批次逐个纳入。
PACKAGE_FLOORS: dict[str, float] = {
    "core": 87.0,      # CI 三平台实测 89.33~89.36%（见下方口径说明）
    "state": 92.0,     # CI 三平台实测 94.96%
    "fetching": 73.0,  # CI 三平台实测 75.78%
}

# 单文件下限：分组门禁是加权聚合，关键模块可以被同组高覆盖率"赎买"
# （审查报告 S37③）。对最关键的文件单独设下限——低于即失败，不许借道。
# 取值贴近 2026-08 实测水平，作为"禁止继续下滑"的护栏；
# 提高这些下限需要配套补齐测试（见审查报告 §8 修复优先级）。
_FILE_FLOORS: dict[str, float] = {
    "src/omnicrawler/security/policy.py": 84.0,  # 实测 86.05%
    "src/omnicrawler/security/egress.py": 89.0,  # 实测 91.75%
    "src/omnicrawler/fetching/http_client.py": 82.0,  # 实测 84.81%
    # --- P1-3 拆分产出的关键模块（2026-09-11 新增；同样按“本地实测 −8”预留 CI 余量）---
    "src/omnicrawler/plugins/plugins.py": 98.0,  # 纯门面（8 行语句）；实测 100%
    "src/omnicrawler/state/state_store_records.py": 97.0,  # 实测 99.03%
    "src/omnicrawler/state/state_store_runs.py": 85.0,  # 实测 87.18%
    "src/omnicrawler/plugins/plugin_loader.py": 82.0,  # 实测 84.91%
    "src/omnicrawler/gui/views/plugin_market_install.py": 30.0,  # 实测 32.50%
    "src/omnicrawler/gui/views/pdf_workbench_worker.py": 18.0,  # 实测 20.48%
    "src/omnicrawler/apps/field_extractor.py": 34.0,  # 实测 36.92%
    "src/omnicrawler/apps/pdf_processor.py": 42.0,  # 实测 44.83%
}

#: **需要浏览器运行时**才能达标的下限（2026-09-15 新增，按环境分离）。
#:
#: 由来：`quality` 的 `test` job **不装 browser extras** ⇒ 浏览器相关用例被跳过 ⇒
#: `browser_engines.py` 在该环境实测 **73.47%**，而此前写死的下限是 **86%**（取自装了
#: playwright 的**全量**环境的实测 93.9% − 余量）—— 于是门禁在 test job 里**永远不可能通过**。
#: 这些下限**不是被删除**，而是挪到**能达成它们的环境**（`gui-and-browser` job 装了 chromium）。
#:
#: ★ **2026-09-15 按实测校准**（用户明确许可：多种方式试过仍不行就调低，别钻牛角尖）：
#: 实测发现**没有任何 CI job 会跑"全量 + 带 chromium"**（`test` job 无浏览器；
#: `gui-and-browser` 只跑浏览器子集；`windows-full-dependency-matrix` 装 `.[full]` 但**不跑 pytest**）。
#: 在**真正执行这条门禁的环境**（chromium 子集）里实测：`browser_engines 44.90%`、`browser_pool 47.31%`。
#: 因此下限改为**该环境实测 − 3**（子集运行确定性高，故余量小于别处的 −8）：
#: ⇒ 语义仍是「**禁止在这套口径上继续下滑**」，只是口径换成了它真正被度量的环境。
#: **若将来出现跑"全量 + chromium"的 job**，应把这两条下限抬回 86 / 57（届时实测校准）。
#: 评估过的替代方案与代价：给三平台 `test` job 装 browser extras + chromium（3× 下载 ≈150MB，
#: 且与专用浏览器 job 重复）；或让 `gui-and-browser` 跑全量（+13 分钟/次）—— 均判为不划算。
_BROWSER_FILE_FLOORS: dict[str, float] = {
    "src/omnicrawler/fetching/browser_engines.py": 41.0,  # 全量环境实测 93.9%；本环境实测 44.90%
    "src/omnicrawler/fetching/browser_pool.py": 44.0,  # 全量环境实测 65.3%；本环境实测 47.31%
}

#: 环境档位：
#: * ``auto``（默认）—— 有 playwright 时 = ``full``，否则 = ``core``（本地"分层可降级"）
#: * ``core``  —— CI 的 `test` job：总/分组/通用单文件下限；浏览器下限**跳过并打印**
#: * ``browser`` —— 只检查浏览器专属下限（该 job 只跑浏览器子集，总/分组不适用）
#: * ``full``  —— 全部检查（本地装了 browser extras 时）
PROFILES = ("auto", "core", "browser", "full")


def _normalise_for_match(path: str) -> str:
    """把 coverage.json 的 key 归一成 `src/...` 形式。

    coverage 的 json reporter 在部分环境（如 CI runner）输出**绝对路径**，
    而 GATES 的 matcher 写的是仓库相对路径 `src/omnicrawler/...`；此处统一
    截取到 `/src/` 之后，避免“报告里明明有该文件、门禁却报 missing”的误报
    （_file_coverage 早先已单独做过同类兜底，这里补齐分组门禁）。
    """
    norm = _normalise(path)
    marker = "/src/"
    if not norm.startswith("src/") and marker in norm:
        return "src/" + norm.split(marker, 1)[1]
    return norm


def _coverage(files: dict[str, Any], matcher: Matcher) -> tuple[int, int, float]:
    statements = covered = matched = 0
    for raw_path, details in files.items():
        path = _normalise_for_match(raw_path)
        if not matcher(path):
            continue
        matched += 1
        summary = details["summary"]
        statements += int(summary["num_statements"])
        covered += int(summary["covered_lines"])
    if not matched or not statements:
        raise ValueError("coverage report does not contain the required source group")
    return covered, statements, covered * 100.0 / statements


def _file_coverage(files: dict[str, Any], raw_path: str) -> float | None:
    """单文件覆盖率；报告里不存在该文件时返回 None（视为缺失）。

    coverage.json 的 key 是**绝对路径**（CI runner 上各不相同），因此先按
    相对路径精确匹配，再按「以 src/... 结尾」兜底匹配，避免不同机器上
    覆盖率报告路径前缀差异导致的漏检（曾导致 Windows CI 全报 missing）。
    """
    norm = _normalise_for_match(raw_path)
    details = files.get(norm)
    if details is None:
        for key, value in files.items():
            candidate = _normalise(key)
            if candidate == norm or candidate.endswith("/" + norm):
                details = value
                break
    if details is None:
        return None
    summary = details["summary"]
    statements = int(summary["num_statements"])
    if not statements:
        return 100.0
    return int(summary["covered_lines"]) * 100.0 / statements


def _resolve_profile(requested: str) -> tuple[str, str]:
    """把 ``auto`` 解析成实际档位并给出理由（打印用，避免"为什么跳过"不可见）。

    ``auto``：装了 playwright ⇒ ``full``（能查浏览器下限）；否则 ⇒ ``core``（如实降级）。
    这是「分层可降级」：**缺依赖给明确降级并说明**，而不是整体不可用或假装可用。
    """
    if requested != "auto":
        return requested, "explicit"
    try:
        import importlib.util

        has_browser = importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        has_browser = False
    return ("full", "auto: playwright detected") if has_browser else ("core", "auto: playwright missing")


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce OmniCrawler subsystem coverage gates")
    parser.add_argument("report", nargs="?", default="coverage.json", type=Path)
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="auto",
        help="environment profile: auto (default) / core (CI test job, no browser) / browser (browser floors only) / full",
    )
    args = parser.parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    files: dict[str, Any] = payload.get("files", {})
    failures: list[str] = []

    profile, reason = _resolve_profile(args.profile)
    check_overall = profile in {"core", "full"}
    check_groups = profile in {"core", "full"}
    print(f"profile={profile} ({reason})")
    print("coverage gate                 covered/lines   actual   minimum")
    print("-" * 67)
    total = payload.get("totals", {})
    total_actual = float(total.get("percent_covered", 0.0))
    total_minimum = OVERALL_COVERAGE_GATE
    print(
        f"{'all_source':29} {int(total.get('covered_lines', 0)):5}/"
        f"{int(total.get('num_statements', 0)):<7} {total_actual:7.2f}% {total_minimum:7.2f}%"
    )
    if check_overall and total_actual < total_minimum:
        failures.append(f"all_source {total_actual:.2f}% < {total_minimum:.2f}%")

    # browser 档位只跑浏览器子集 ⇒ 总/分组指标不适用（该档只查浏览器专属单文件下限）
    for name, (minimum, matcher) in (GATES if check_groups else {}).items():
        try:
            covered, statements, actual = _coverage(files, matcher)
        except ValueError as exc:
            failures.append(f"{name}: {exc}")
            print(f"{name:29} {'missing':>13} {'--':>8} {minimum:7.2f}%")
            continue
        print(f"{name:29} {covered:5}/{statements:<7} {actual:7.2f}% {minimum:7.2f}%")
        if actual < minimum:
            failures.append(f"{name} {actual:.2f}% < {minimum:.2f}%")

    # 单文件下限（S37③）：分组聚合掩盖关键模块，逐文件兜底
    def _check_file_floors(floors: dict[str, float]) -> None:
        for raw_path, floor in sorted(floors.items()):
            actual = _file_coverage(files, raw_path)
            if actual is None:
                failures.append(f"{raw_path}: missing from coverage report (file-level floor {floor:.2f}%)")
                print(f"{raw_path:29} {'missing':>13} {'--':>8} {floor:7.2f}%")
                continue
            print(f"{raw_path:29} {'':>5}{'':<7} {actual:7.2f}% {floor:7.2f}%")
            if actual < floor:
                failures.append(f"{raw_path} {actual:.2f}% < {floor:.2f}%")

    if profile != "browser":
        _check_file_floors(_FILE_FLOORS)
    if profile in {"browser", "full"}:
        _check_file_floors(_BROWSER_FILE_FLOORS)
    else:
        # 跳过必须**可见**（打印出来），不做静默跳过
        print(
            f"{'(skipped: needs browser)':29} {len(_BROWSER_FILE_FLOORS)} browser floors"
            " are checked in --profile browser (the job with chromium installed)"
        )

    # 按顶层子包下限（P2-1 ratchet）：与分组门禁互补 —— 分组是跨包的功能视图，
    # 这里按“包”整体看待，防止某包整体滑落被其它包的高覆盖平均掉。
    for package, floor in sorted((PACKAGE_FLOORS if check_groups else {}).items()):
        prefix = "src/omnicrawler/" + package + "/"
        label = "pkg:" + package
        try:
            covered, statements, actual = _coverage(
                files, lambda path, _p=prefix: path.startswith(_p)
            )
        except ValueError as exc:
            # 与分组循环同款处理：报告里缺该包时**记为失败并继续**，不让脚本中断
            # （2026-09-15：此前这里没有捕获，报告不完整时脚本会直接崩，看不到其它结论）
            failures.append(f"{label}: {exc}")
            print(f"{label:29} {'missing':>13} {'--':>8} {floor:7.2f}%")
            continue
        print(f"{label:29} {covered:5}/{statements:<7} {actual:7.2f}% {floor:7.2f}%")
        if actual < floor:
            failures.append(f"package {package} {actual:.2f}% < {floor:.2f}%")

    if failures:
        print("\nCoverage gates failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nAll coverage gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
