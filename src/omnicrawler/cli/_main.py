from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from ..core.logging_utils import configure_logging
from ..core.runtime_paths import configure_runtime_environment
from ..core.utils import user_agent
from ..services.benchmarking import BenchmarkHistory, BenchmarkRunner

# Source installs discover project-local .runtime assets, while frozen builds
# discover the browser/OCR runtime beside the companion executable.
configure_runtime_environment()


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _complete_seed_scheme(seed: str) -> str | None:
    """为入口网址补全 http(s) scheme。

    对 Windows 盘符路径 / 反斜杠路径 / 相对路径返回 None，避免把
    ``C:\\data\\page.html`` 误补全成 ``https://C:\\data\\page.html``（F688）。
    """
    if not seed or "\\" in seed or re.match(r"^[A-Za-z]:[\\/]", seed) or seed.startswith(("/", ".", "\\")):
        return None
    has_scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", seed) is not None
    return seed if has_scheme else f"https://{seed}"


def build_parser() -> argparse.ArgumentParser:
    """组装顶层 parser。

    FINAL-G4：参数定义按域拆分至 ``_parsers/`` 各模块（原 335 行单体函数），
    本函数只负责全局选项与组装；新增命令在对应域模块的 ``configure`` 内定义。
    """
    parser = argparse.ArgumentParser(prog="omnicrawler", description="模块化网站采集、附件下载与PDF字段抽取平台")
    parser.add_argument("--version", action="version", version=f"omnicrawler {__version__}")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", default="text", choices=["text", "json"])
    sub = parser.add_subparsers(dest="command", required=True)

    from ._parsers import data, extraction, ops, plugins, project, task, templates

    for section in (task, templates, project, plugins, extraction, data, ops):
        section.configure(sub)
    return parser



def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 统一 UTF-8 输出：Windows 管道/重定向下 Python 默认按 locale（cp1252/GBK）
    # 编码 stdout，打印含中文的 JSON（_json ensure_ascii=False）会抛
    # UnicodeEncodeError——CI（capabilities --verify-imports）与任何重定向
    # 场景都受影响。交互控制台（PEP 528）本就是 UTF-8，此处只修正管道形态。
    stdout = sys.stdout
    stderr = sys.stderr
    if isinstance(stdout, io.TextIOWrapper) and isinstance(stderr, io.TextIOWrapper):
        stdout.reconfigure(encoding="utf-8", errors="replace")
        stderr.reconfigure(encoding="utf-8", errors="replace")
    # PDF sub-commands use their own entry points
    if argv and argv[0] in {"pdf", "pdf-process", "pdf-extract"}:
        _dispatch_pdf(argv)
        return
    if not argv:
        _print_welcome()
        return
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        # argparse emits its own usage on --help / invalid args; add a friendly nudge
        if not any(a in argv for a in ("-h", "--help")):
            print("\n💡 提示: 运行 omnicrawler wizard 开始交互创建配置，或 omnicrawler --help 查看全部命令", file=sys.stderr)
        raise
    configure_logging(args.log_level, args.log_format)
    # S4.2 ③：启动第一行日志打印关键路径（data_dir/config_path），排障不迷路
    import logging as _logging

    _root_logger = _logging.getLogger("omnicrawler")
    _root_logger.info("omnicrawler %s 启动; data_dir=%s", __version__, _data_dir_hint())
    if getattr(args, "config", None):
        _root_logger.info("config_path=%s", args.config)
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        print("已中断；已完成状态保存在SQLite中，可用resume继续。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # CLI error boundary.
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        _print_error_hint(exc)
        raise SystemExit(1)


def _data_dir_hint() -> str:
    """S4.2 ③：数据目录提示——便携数据根/本地数据根，不指向安装目录。"""
    from ..core.runtime_paths import portable_data_root

    try:
        return str(portable_data_root())
    except Exception:  # noqa: BLE001 - 兜底不阻断启动
        return "（数据目录解析失败）"


def _print_welcome() -> None:
    print(f"""
╔══════════════════════════════════════════════════╗
║       OmniCrawler {__version__} — 模块化网站采集平台      ║
╚══════════════════════════════════════════════════╝

  快速开始:
    omnicrawler wizard              交互创建配置文件
    omnicrawler workbench           启动图形工作台
    omnicrawler init <名称>         从模板创建项目
      --template static_html

  常用命令:
    omnicrawler run    -c <配置>    运行采集任务
    omnicrawler status -c <配置>    查看任务状态
    omnicrawler doctor -c <配置>    诊断配置和环境
    omnicrawler templates list      浏览内置模板库

  详细帮助: omnicrawler -h
  文档: OmniCrawler-用户指南.md
""")


def _print_error_hint(exc: Exception) -> None:
    """追加针对性修复建议到 stderr。"""
    msg = str(exc).lower()
    hints: list[str] = []
    if isinstance(exc, FileNotFoundError):
        hints.append("请确认文件路径正确，或运行 omnicrawler init 创建新配置")
    elif isinstance(exc, (ValueError, KeyError)):
        if "seeds" in msg or "入口" in str(exc):
            hints.append("配置中缺少入口 URL；请在 source.seeds 中至少填写一个网址")
        elif "template" in msg or "模板" in str(exc):
            hints.append("模板不存在；运行 omnicrawler templates list 查看可用模板")
        else:
            hints.append("请运行 omnicrawler doctor -c <配置> 检查配置有效性")
    elif isinstance(exc, PermissionError):
        hints.append("请确认目标网站允许自动访问，或检查网络代理设置")
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        hints.append("网络连接失败；请检查目标网址是否可访问，或尝试降低并发数")
    else:
        hints.append("请运行 omnicrawler doctor 检查环境，或查看 OmniCrawler-用户指南.md")
    if hints:
        print(f"  💡 修复建议: {hints[0]}", file=sys.stderr)


def _dispatch_pdf(argv: list[str]) -> None:
    cmd, rest = argv[0], argv[1:]
    if cmd == "pdf":
        from ..pdfx.cli import main as pdf_main

        old = sys.argv
        try:
            sys.argv = ["omnicrawler pdf", *rest]
            pdf_main()
        finally:
            sys.argv = old
    elif cmd == "pdf-process":
        from ..apps.pdf_processor import main as processor_main

        raise SystemExit(processor_main(rest))
    elif cmd == "pdf-extract":
        from ..apps.field_extractor import main as extractor_main

        raise SystemExit(extractor_main(rest))


def _dispatch(args: argparse.Namespace) -> None:
    """Route via ``_handlers.lookup()`` registry.

    All commands are dispatched through the typed registry in
    ``_handlers.py``. Unknown commands raise an error (should not happen
    since argparse validates command names before dispatch).
    """
    from ._handlers import lookup

    handler = lookup(args.command)
    if handler is None:
        raise ValueError(f"未注册的命令: {args.command}")
    handler(args)


def _print_plan_summary(name: str, kind: str, seed: str, max_pages: int, formats: list[str], email: str) -> None:
    """输出自然语言可读的任务计划摘要。"""
    kind_desc = {
        "static_html": "普通静态网页", "crawl": "站内遍历爬取", "focused": "关键词聚焦采集",
        "rest": "REST API 接口", "browser": "浏览器动态渲染", "feed": "RSS/Atom 订阅",
        "sitemap": "Sitemap 地图",
    }
    plan_lines = [
        f"📋 任务计划: {name}",
        f"   目标: 从 {seed} 采集 {'最多' if max_pages < 1000 else '约'} {max_pages} 页",
        f"   方式: {kind_desc.get(kind, kind)}",
        f"   输出: {', '.join(formats).upper()}",
        f"   联系: {email} (写入 User-Agent)",
    ]
    if kind in ("crawl", "focused"):
        plan_lines.append("   ⚠ 将会跟随站内链接，请确保遵守 robots.txt")
    if kind == "browser":
        plan_lines.append("   ⚠ 需要 Playwright 浏览器（首次使用需 playwright install chromium）")
    print("\n".join(plan_lines))


def _wizard(output: Path) -> None:
    """交互式配置向导：通过多步骤问答创建采集配置文件。"""
    import urllib.parse

    SOURCE_KINDS: dict[str, str] = {
        "static_html": "普通静态网页，无需浏览器渲染",
        "crawl": "从入口开始逐页遍历同站链接（广度优先）",
        "focused": "只采集与关键词相关的页面",
        "rest": "REST API 接口（JSON 数据）",
        "browser": "需要 Playwright 浏览器渲染的动态页面",
        "feed": "RSS/Atom 订阅源",
        "sitemap": "网站的 sitemap.xml 地图",
    }

    print("╔══════════════════════════════════════════════╗")
    print("║     OmniCrawler 交互式配置向导               ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    print("本向导将通过几个简单问题帮你生成采集配置。")
    print("所有问题都可以直接回车使用默认值。按 Ctrl+C 随时退出。")
    print()

    # Step 1: Project name（拒绝路径穿越字符并净化输入）
    _NAME_RE = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]")
    while True:
        raw_name = input("① 项目名称 [my_project]: ").strip() or "my_project"
        if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
            print("   ⚠ 项目名称不能包含 '..' '/' '\\' 等路径字符，请重新输入")
            continue
        name = _NAME_RE.sub("_", raw_name).strip("_") or "my_project"
        if name != raw_name:
            print(f"   → 名称已净化为: {name}")
        break
    print(f"   → 工作目录将创建在 work/{name}/\n")

    # Step 2: Source type
    print("② 选择来源类型（输入编号或名称）:")
    kinds_list = list(SOURCE_KINDS.items())
    for i, (key, desc) in enumerate(kinds_list, 1):
        print(f"   {i}. {key:14s} — {desc}")
    while True:
        choice = input(f"   请选择 [1-{len(kinds_list)}, 默认 crawl]: ").strip()
        if not choice:
            kind = "crawl"
            break
        if choice.isdigit() and 1 <= int(choice) <= len(kinds_list):
            kind = kinds_list[int(choice) - 1][0]
            break
        if choice in SOURCE_KINDS:
            kind = choice
            break
        print(f"   ⚠ 请输入 1-{len(kinds_list)} 之间的数字，或直接输入名称")
    print(f"   → 已选择: {kind} ({SOURCE_KINDS[kind]})\n")

    # Step 3: Seed URL(s)
    print("③ 入口网址（要采集的第一个页面）")
    while True:
        try:
            seed = input("   网址: ").strip()
        except (EOFError, StopIteration) as exc:
            raise ValueError("入口网址不能为空") from exc
        if not seed:
            print("   ⚠ 入口网址不能为空")
            continue
        # F688：不要把 Windows 盘符路径/相对路径误补成 https://C:\data\page.html
        candidate = _complete_seed_scheme(seed)
        if candidate is None:
            print("   ⚠ 网址格式无效，请以 https:// 开头")
            continue
        parsed = urllib.parse.urlparse(candidate)
        if parsed.netloc:
            print(f"   → 已识别域名: {parsed.netloc}")
            break
        print("   ⚠ 网址格式无效，请以 https:// 开头")
    print()

    # Step 4: Max pages
    print("④ 采集上限（防止失控）")
    maximum = input("   最大页面数 [100]: ").strip() or "100"
    try:
        max_pages = int(maximum)
        if max_pages < 1:
            max_pages = 100
    except ValueError:
        max_pages = 100
    print(f"   → 最多采集 {max_pages} 个页面\n")

    # Step 5: Contact email
    print("⑤ 维护者联系方式（填入 User-Agent，方便网站管理员联系你）")
    email = input("   邮箱 [crawler@example.com]: ").strip() or "crawler@example.com"
    print(f"   → User-Agent 将包含: {email}\n")

    # Step 6: Output format
    print("⑥ 输出格式（可多选，逗号分隔）")
    try:
        formats_raw = input("   jsonl / csv / xlsx [默认 jsonl,csv]: ").strip().lower()
    except (EOFError, StopIteration):
        formats_raw = ""
    formats = [f.strip() for f in (formats_raw.split(",") if formats_raw else ["jsonl", "csv"]) if f.strip()]

    # Build config
    data: dict[str, Any] = {
        "project": {"name": name, "workspace": f"work/{name}"},
        "source": {"kind": kind, "seeds": [seed]},
        "crawl": {"max_pages": max_pages, "max_depth": 3, "same_host": True, "concurrency": 4},
        "http": {
            "user_agent": user_agent(f"+contact: {email}"),
            "respect_robots": True,
            "delay_seconds": 1.0,
        },
        "extract": {"mode": "auto", "fields": {"title": {"selector": "title"}, "heading": {"selector": "h1"}}},
        "outputs": {fmt: True for fmt in formats},
    }

    output_path = output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"目标已存在，不会覆盖: {output_path}")
    output_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"\n✅ 配置已生成: {output_path}")
    print()
    # -- 生成自然语言计划摘要 --
    _print_plan_summary(name, kind, seed, max_pages, formats, email)
    print()
    print("下一步建议操作:")
    print(f"  1. omnicrawler doctor  -c {output_path}     # 检查环境和配置")
    print(f"  2. omnicrawler sample  -c {output_path}     # 试跑 3 页验证")
    print(f"  3. omnicrawler run     -c {output_path}     # 正式采集")
    print()
    print("💡 提示: 如需更精准的字段配置，可以:")
    print(f"   omnicrawler auto-analyze {seed} -o {output_path}")
    print("   omnicrawler visual-select        # 在浏览器中可视化点选元素")
    print()
    _json({"created": str(output_path), "next": f"omnicrawler doctor -c {output_path}"})


def _key_values(items: list[str], separator: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if separator not in item:
            raise ValueError(f"参数必须使用 NAME{separator}VALUE 格式: {item}")
        key, value = item.split(separator, 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数名不能为空: {item}")
        result[key] = value.strip()
    return result


def _run_benchmark(args: argparse.Namespace) -> None:
    """Run performance benchmarks and compare against historical baselines.

    Supports --profile low/standard/high/all and outputs JSON history.
    """
    from pathlib import Path

    profiles_to_run: list[str]
    if args.profile == "all":
        profiles_to_run = ["low", "standard", "high"]
    else:
        profiles_to_run = [args.profile]

    history_path = Path(args.history or args.output)
    history = BenchmarkHistory(history_path)
    runner = BenchmarkRunner()

    print("OmniCrawler 性能基准测试")
    print(f"配置: {args.config}")
    print(f"用例: {', '.join(profiles_to_run)}")
    print(f"历史: {history_path}")
    print()

    failed: list[str] = []
    for profile in profiles_to_run:
        print(f"[{profile}] 运行中...", end=" ", flush=True)
        try:
            result = runner.run(profile, config_path=args.config)
            history.add(result)
            check = history.check_regression(
                result, threshold=float(args.regression_threshold)
            )
            print(f"完成 — {result.pages} 页, {result.pages_per_second:.1f} 页/秒")
            if check.get("regression"):
                change = float(str(check.get("throughput_change", 0))) * 100
                print(f"  ⚠ 性能退化: 吞吐量下降 {abs(change):.1f}% (阈值 {args.regression_threshold * 100:.0f}%)")
            elif check.get("reason") == "no_baseline":
                print("  📊 首次基准记录（无历史基线）")
        except Exception as exc:  # noqa: BLE001
            print(f"失败: {exc}")
            failed.append(profile)

    summary = history.all_results()
    print(f"\n共 {len(summary)} 条基准记录保存至 {history_path}")
    if failed:
        # E10：失败不再静默吞掉——全部 profile 失败时以非零退出码结束
        raise SystemExit(f"基准运行失败: {', '.join(failed)}")


if __name__ == "__main__":
    main()
