from __future__ import annotations

import argparse
import importlib
import io
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar, overload

from .._version import __version__
from ..core.logging_utils import configure_logging
from ..core.runtime_paths import configure_runtime_environment
from ..core.utils import atomic_write_text, user_agent
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


#: CLI 帮助里的 section 顺序（**稳定顺序常量**，W6.7-②）。
#:
#: 由来：这个顺序此前是 `build_parser()` 里的一行元组字面量 —— 新加一个 section 时它自然落在
#: **末尾**（`pdf` 就是这么排到最后去的），于是"顺序"成了注册动作的副产品、而不是一个决定。
#: 现在显式化，规则是**用户旅程**：先做任务 → 模板/项目/插件 → 抽取与数据 → 运维 → 专项（pdf）。
#: 守卫见 `tests/unit/cli/test_cli_sections.py`：`_parsers/` 下每个模块都必须在表里，
#: 且 `build_parser()` 只按这张表注册（不再出现元组字面量）。
SECTION_ORDER: tuple[str, ...] = (
    "task",
    "templates",
    "project",
    "plugins",
    "extraction",
    "data",
    "ops",
    "pdf",
)

#: **面向用户的**命令分组（按「我要干什么」，不是按代码文件归属）。
#:
#: 为什么不用 SECTION_ORDER：那是**代码组织**（`_parsers/task.py` 里混装了
#: validate / doctor / export / benchmark 等 15 个命令），与用户心智模型不符。
#: 2026-11-23 实测 `--help` 按 section 分组后，第一段有 19 个命令 —— 比不分还乱。
#:
#: 这里按「用户想做什么」重排，**只影响 help 展示**，不改变注册顺序与
#: 任何行为。未列出的命令归入「其它」，由守卫保证不会有命令被漏掉。
USER_COMMAND_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("开始一个新任务", ("task", "init", "wizard", "validate", "tutorial")),
    ("运行与试跑", ("sample", "run", "resume", "control", "preflight")),
    ("模板", ("templates", "gen-templates")),
    ("查看结果与导出", ("status", "export", "reprocess", "compare-runs", "timeline")),
    ("诊断与修复", ("doctor", "capabilities", "recovery", "repair", "regression",
                   "runtime-verify", "security-report", "plan", "benchmark")),
    ("能力与组件", ("components", "browser-kernels", "models", "plugins",
                   "notifications")),
    ("工程与配置", ("migrate", "cleanup", "backup", "import-easyspider",
                   "transform", "convert", "workspace", "scene", "queue",
                   "schedule", "serve", "workbench", "self-update")),
    ("抽取与字段", ("analyze-archive", "api-discover", "auto-analyze", "c4a-fetch",
                   "field-suggest", "record-actions", "replay", "visual-select",
                   "stealth-fingerprint", "research-package")),
    ("PDF 专项", ("pdf", "worker")),
)

#: 每个 section 在 `--help` 里的**中文段落名**。
#:
#: 为什么需要：55 个子命令平铺一屏，人和 agent 都找不到入口（2026-11-23 实测）。
#: `SECTION_ORDER` 早就按用户旅程排好了序，但 argparse 默认把子命令
#: 一股脑列在 positional arguments 里，这个顺序看不见。
#: `epilog` 按 SECTION_SECTION_TITLES 重新分组展示，顺序仍以 SECTION_ORDER 为准。
SECTION_TITLES: dict[str, str] = {
    "task": "任务生命周期（开始→运行→恢复）",
    "templates": "模板与项目",
    "project": "工程与配置",
    "plugins": "插件与扩展",
    "extraction": "抽取与字段",
    "data": "数据与导出",
    "ops": "运维与诊断",
    "pdf": "PDF 专项",
}

#: `--help` 末尾的**示例**。clig.dev 明确要求「lead with examples」——
#: agent 与人都先看示例、再看参数表。
CLI_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("把一句需求编译成任务", "omnicrawler task \"抓取某站点的文章标题与正文\""),
    ("试跑 5 页确认效果", "omnicrawler sample -c task.yaml --pages 5"),
    ("正式运行并导出", "omnicrawler run -c task.yaml && omnicrawler export -c task.yaml"),
    ("诊断环境", "omnicrawler doctor -c task.yaml"),
    ("机器可读输出", "omnicrawler status -c task.yaml --json"),
)

#: 退出码契约。写在 `--help` 末尾，让 agent 不必读源码就能分支判断。
#:
#: 与 `check_agent_surface.py --json` 导出的 `exit_code_convention` 同源；
#: 改这里要同步改那边（守卫见 tests/unit/cli/test_cli_help_surface.py）。
EXIT_CODE_HELP: tuple[tuple[str, str], ...] = (
    ("0", "成功（结果已达成）"),
    ("1", "运行期失败（结果未达成；可读 stderr 的 `修复建议`）"),
    ("2", "用法或校验失败（参数/配置不合法；先修输入再重试）"),
)


def _usage_suggestion(message: str) -> str:
    """从 argparse 的错误文案里抽出「缺什么/错什么」，给出具体建议。"""
    text = (message or "").strip()
    lowered = text.casefold()
    if "required" in lowered:
        # 形如 `the following arguments are required: --output/-o, --config/-c`
        _head, _sep, tail = text.partition(":")
        names = tail.strip()
        if names:
            return f"缺少必填参数 {names}；加 `--help` 查看完整用法"
        return "缺少必填参数；加 `--help` 查看完整用法"
    if "unrecognized arguments" in lowered:
        _head, _sep, tail = text.partition(":")
        return f"参数不被识别 {tail.strip()}；检查拼写或用 `--help` 查看支持的选项"
    if "invalid choice" in lowered:
        return "参数取值不在允许范围内；用 `--help` 查看可选项"
    return "检查参数拼写与必填项；用 --help 查看用法"


_NamespaceT = TypeVar("_NamespaceT")


class _ArgumentParser(argparse.ArgumentParser):
    @overload
    def parse_args(self, args: Iterable[str] | None = None, namespace: None = None) -> argparse.Namespace: ...

    @overload
    def parse_args(self, args: Iterable[str] | None, namespace: _NamespaceT) -> _NamespaceT: ...

    @overload
    def parse_args(self, *, namespace: _NamespaceT) -> _NamespaceT: ...

    def parse_args(
        self, args: Iterable[str] | None = None, namespace: Any = None,
    ) -> Any:
        raw = list(sys.argv[1:] if args is None else args)
        # **`--json` 位置无关**：argparse 的父解析器只认前置形式
        # （`omnicrawler --json run`），而 clig.dev 要求 flag 顺序无关。
        # 这里把**任何位置**出现的 `--json` 提到最前，再交给 argparse。
        #
        # 为什么不在每个子解析器上挂 `parents=[...]`：`_parsers/` 下有 84 处
        # `add_parser`，逐个改既易漏又会把 help 文本重复一遍。集中在这里
        # 处理，一处生效、且不影响任何现有 --json 实现（子命令自己声明的
        # 同名 flag 仍会被 argparse 接受，这里只是保证父级也能看到）。
        wants_json = "--json" in raw
        # 记录**本次解析**用到的 argv：`error()` 需要据此判断要不要出 envelope。
        # 只认 `sys.argv` 会让库调用（argv 里没有 --json）失效。
        self._seen_argv = list(raw)
        # ★ 子解析器是**独立对象**，`error()` 在子命令层触发时它的 `_seen_argv` 为空。
        # 把本次 argv 下发给所有子解析器，避免「父级有 JSON、子级没有」的不一致。
        self._propagate_seen_argv(raw)
        if wants_json:
            raw = [tok for tok in raw if tok != "--json"]
            raw.insert(0, "--json")
        parsed = super().parse_args(raw, namespace)
        if wants_json and not getattr(parsed, "json", False):
            # 子命令里可能没有 `json` 属性（父级加的在 namespace 上应该有）
            parsed.json = True
        if getattr(parsed, "command", None) == "workspace":
            if parsed.action == "import":
                if not parsed.target or not parsed.destination:
                    self.error("workspace import requires --target and --destination")
            elif not parsed.config:
                self.error("workspace requires --config except for import")
        return parsed

    def _propagate_seen_argv(self, argv: list[str]) -> None:
        """把本次 argv 下发给所有子解析器（含多层的）。

        `error()` 依赖它判断「要不要输出 JSON envelope」。子解析器没有自己的
        argv 来源，只能从父级拿 —— 否则 `omnicrawler components install x --json`
        这种**子命令层**的错误永远不会产出 envelope。
        """
        for action in self._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for child in action.choices.values():
                if isinstance(child, _ArgumentParser):
                    child._seen_argv = argv  # noqa: SLF001 - 同类内部状态
                    child._propagate_seen_argv(argv)  # noqa: SLF001


    def error(self, message: str) -> Any:
        """参数错误。`--json` 时输出 envelope，而不是 usage 文本。

        **为什么要覆盖**：argparse 默认把用法 + 错误打到 stderr 再 `exit(2)`。
        对 agent 来说，`--json` 模式下它期待 stdout 是合法 JSON —— 而参数
        错误恰恰是最常见的失败（缺 `--config`、拼错子命令），此时却只拿到
        一段人读 usage，`json.loads` 直接失败。

        保留原行为（仍 exit 2、仍打 usage 到 stderr），只**额外**在 stdout
        给一份结构化错误。
        """
        # ★ 判据取 `parse_args` 实际收到的 argv，而不是 `sys.argv`：
        # 库调用（`build_parser().parse_args([...])`）时 `sys.argv` 里没有 `--json`，
        # 于是「`--json` 时输出 envelope」的承诺在那种用法下失效 —— 实测确认为空。
        # `_seen_argv` 由 `parse_args` 在解析前记录，进程内随之更新。
        seen = getattr(self, "_seen_argv", None)
        argv = list(seen) if seen is not None else sys.argv[1:]
        if "--json" in argv:
            import json as _json

            payload = {
                "ok": False,
                "command": (getattr(self, "prog", "") or "").split()[-1],
                "exit_code": 2,
                "output": "",
                "error": {
                    "type": "UsageError",
                    "message": message,
                    # **建议要指向具体缺失项**，不能只说「检查参数拼写」。
                    # argparse 的 message 已经把参数名列出来了
                    # （如 `the following arguments are required: --output/-o`），
                    # 直接点名比重读一遍 help 快得多 —— agent 尤其如此。
                    "suggestion": _usage_suggestion(message),
                    "is_retryable": False,
                },
            }
            print(_json.dumps(payload, ensure_ascii=False))
        super().error(message)
        return None


#: section → 该 section 注册的命令名（**运行时填充**，不手工维护）。
#:
#: 手工维护必然会漂移（这是本项目反复踩过的模式：靠人手同步的清单都会过时）。
#: 这里由 `_remember_section()` 在注册时记录，`_build_epilog()` 只读它。
_SECTION_COMMANDS: dict[str, list[str]] = {}

#: 全部已注册子命令名（运行时填充，供 help 分组用）。
_ALL_COMMANDS: set[str] = set()


def _remember_section(section: str, before: set[str], sub: object) -> None:
    """记录某个 section 新增了哪些子命令（按注册顺序）。"""
    choices = getattr(getattr(sub, "_name_parser_map", None), "keys", None)
    after = set(choices()) if choices else set()
    _SECTION_COMMANDS[section] = sorted(after - before)


def _build_epilog() -> str:
    """构造 `--help` 末尾的分组导航 + 示例 + 退出码。

    三段各自解决一个具体问题（2026-11-23 调研 clig.dev / gh CLI / tooli 后定）：

    1. **命令分组** —— 55 个子命令平铺一屏找不到入口；
    2. **示例优先** —— clig.dev 要求 "lead with examples"，人先看例子；
    3. **退出码契约** —— agent 据此分支（0 重试无意义 / 1 可重试 / 2 先改输入）。
    """
    lines: list[str] = ["", "常用示例:"]
    width = max(len(label) for label, _ in CLI_EXAMPLES)
    for label, command in CLI_EXAMPLES:
        lines.append(f"  {label:<{width}}  {command}")

    lines.extend(["", "退出码:"])
    for code, meaning in EXIT_CODE_HELP:
        lines.append(f"  {code}  {meaning}")

    lines.extend(["", "命令分组（按「我要干什么」）:"])
    listed: set[str] = set()
    for title, names in USER_COMMAND_GROUPS:
        exists = [n for n in names if n in _ALL_COMMANDS]
        listed.update(exists)
        if exists:
            lines.append(f"  {title}:")
            lines.append(f"    {', '.join(exists)}")
    others = sorted(_ALL_COMMANDS - listed)
    if others:
        lines.append(f"  其它: {', '.join(others)}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """组装顶层 parser。

    FINAL-G4：参数定义按域拆分至 ``_parsers/`` 各模块（原 335 行单体函数），
    本函数只负责全局选项与组装；新增命令在对应域模块的 ``configure`` 内定义。
    """
    parser = _ArgumentParser(
        prog="omnicrawler",
        description="模块化网站采集、附件下载与PDF字段抽取平台",
        # epilog 在注册**之后**才能算（要读 _SECTION_COMMANDS），
        # 所以先留空、末尾再赋值。
        epilog=None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"omnicrawler {__version__}")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（写 stderr；DEBUG 会输出更多排查信息）",
    )
    parser.add_argument(
        "--log-format", default="text", choices=["text", "json"],
        help="日志格式：text=人读；json=结构化（便于日志系统采集）",
    )
    # **全局 `--json`**：机器可读输出。
    #
    # clig.dev：「Anything that is machine readable should also go to stdout」。
    # 此前只有 3/55 个命令各自实现了 `--json`（stealth-fingerprint /
    # browser-kernels / models），agent 对其它 52 个只能解析人读文本 ——
    # 既脆弱又浪费上下文。
    #
    # 位置无关：父解析器收前置形式（`omnicrawler --json run`），子解析器经
    # `parents=[_GLOBAL_OPTIONS]` 收后置形式（`omnicrawler run --json`）。
    # 这两种都生效，符合 clig.dev「make flags order-independent」。
    parser.add_argument("--json", action="store_true", default=False,
                        help="输出机器可读 JSON（缺省为人读文本）")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in SECTION_ORDER:
        # 顺序的唯一来源是 SECTION_ORDER（不再写元组字面量）——
        # 新加 section 时不会"自然落在末尾"，而必须显式决定它排在哪。
        _before = set(getattr(sub, "_name_parser_map", {}) or {})
        module = importlib.import_module(f"._parsers.{name}", __package__)
        module.configure(sub)
        _remember_section(name, _before, sub)
    _ALL_COMMANDS.update(getattr(sub, "_name_parser_map", {}) or {})
    parser.epilog = _build_epilog()
    return parser



#: 内部 dispatch 命令：**不出现在 `--help`**，只供宿主自己启动子进程用。
#:
#: 为什么需要它：冻结包里 `sys.executable` 是宿主 exe，`python -m <module>`
#: 这种形态不可用（exe 不认 `-m`，argparse 会报 invalid choice）。
#: 所以给宿主开一个内部命令，把模块入口变成 `omnicrawler __component_worker__`。
INTERNAL_WORKER_COMMAND = "__component_worker__"

#: 运行期 worker（`runtime/worker_main.py`）的内部命令。
INTERNAL_RUNTIME_WORKER_COMMAND = "__runtime_worker__"

#: 内部命令 → 要执行的模块（`python -m` 的等价物）。
#: 加新条目时**必须**同时确认该模块的 main() 能接受 argv（worker_main 可以，
#: component_worker 不行 —— 它从环境变量与 stdin 读参数）。
_INTERNAL_COMMANDS = {
    INTERNAL_WORKER_COMMAND: "omnicrawler.services.component_worker",
    INTERNAL_RUNTIME_WORKER_COMMAND: "omnicrawler.runtime.worker_main",
}


def _dispatch_internal(argv: list[str]) -> bool:
    """若 argv[0] 是内部命令则就地执行对应模块，返回是否已处理。

    **在 argparse 之前拦截**：内部命令不进 subparsers，所以 `--help` 里看不到它，
    argparse 也不会因为不认识它而报错。
    """
    # ★ 2026-11-24：判断前**剥掉前置的全局 flag**。
    # 此前写成 `argv[0] not in _INTERNAL_COMMANDS`，于是 `omnicrawler --json
    # __component_worker__` 匹配失败 ⇒ 内部命令落到 argparse ⇒ 报「未注册的命令」，
    # 而它本该被执行。与第 25 轮 `pdf` 分支是**同一个形态**（`--json` 位置无关
    # 是明确承诺，但 `argv[0]` 判断没考虑前置 flag）。
    head = [tok for tok in argv if tok != "--json"]
    if not head or head[0] not in _INTERNAL_COMMANDS:
        return False
    module_name = _INTERNAL_COMMANDS[head[0]]
    import importlib

    module = importlib.import_module(module_name)
    entry = getattr(module, "main", None)
    if entry is None:
        print(f"内部命令 {head[0]} 的模块缺少 main()", file=sys.stderr)
        raise SystemExit(2)
    # 有的 worker 从环境变量/stdin 读参数（component_worker），有的要 argv
    # （worker_main 的 --session）。按签名决定，避免两处各写一套。
    import inspect

    try:
        _accepts_args = bool(inspect.signature(entry).parameters)
    except (TypeError, ValueError):  # pragma: no cover - 内建函数无签名
        _accepts_args = False
    # 传给模块的是**剥离全局 flag 后**的参数（内部模块不认识 `--json`）。
    raise SystemExit(entry(head[1:]) if _accepts_args else entry())


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    _dispatch_internal(argv)
    # 统一 UTF-8 输出：Windows 管道/重定向下 Python 默认按 locale（cp1252/GBK）
    # 编码 stdout，打印含中文的 JSON（_json ensure_ascii=False）会抛
    # UnicodeEncodeError——CI（capabilities --verify-imports）与任何重定向
    # 场景都受影响。交互控制台（PEP 528）本就是 UTF-8，此处只修正管道形态。
    stdout = sys.stdout
    stderr = sys.stderr
    if isinstance(stdout, io.TextIOWrapper) and isinstance(stderr, io.TextIOWrapper):
        stdout.reconfigure(encoding="utf-8", errors="replace")
        stderr.reconfigure(encoding="utf-8", errors="replace")
    # PDF 子系统的**执行**入口（转发型）。
    #
    # `pdf-process` / `pdf-extract` 是已发布的 console script 别名：它们不是
    # `omnicrawler` 的子命令，但历史命令行习惯允许这样调用，保留为兼容入口。
    #
    # `pdf` **是真子命令**（见 _parsers/pdf.py），注册照旧保留 —— 所以
    # `omnicrawler --help` 与 CLI 文档契约仍然看得到它，这一点没有变。
    # 但**执行**时不能再经 argparse 二次解析它的参数：转发层用 `REMAINDER` 捕获，
    # 而 REMAINDER **不捕获以选项开头的参数**；偏偏 PDF 子系统的 `--config` 必须位于
    # 其子命令**之前**。于是 `omnicrawler pdf --config X doctor` 会在顶层直接报
    # "unrecognized arguments: --config"，自定义 PDF 项目从顶层 CLI 完全不可达
    # （只能用内置模板）。因此这里把 `pdf` 之后的 token 原样交给子系统：
    # **发现路径与执行路径分离，子命令定义仍是唯一真源**。
    # ★ 2026-11-24：判断时**跳过前置的全局 flag**。
    # 此前写成 `argv[0] in {...}`，于是 `omnicrawler --json pdf --help` 匹配失败 ——
    # `pdf` 落到普通子命令路径（argparse），得到 `ok=false` / `is_retryable=true`，
    # 与 `omnicrawler pdf --json --help`（rc=0 / ok=true）**不一致**。
    # 而「`--json` 位置无关」是本仓明确承诺的（见 `_ArgumentParser.parse_args` 的
    # 同类处理）。这里同样要先剥掉前置 flag 再判断。
    _pdf_head = [tok for tok in argv if tok != "--json"]
    if _pdf_head and _pdf_head[0] in {"pdf", "pdf-process", "pdf-extract"}:
        # W6.7-④：这条**转发路径**此前既不配置日志、也不打启动行 ⇒ 与统一入口行为不一致
        # （同一次运行，走 `omnicrawler pdf …` 与走 `omnicrawler …` 看到的排障信息不一样）。
        # 这里补齐同一套：配置日志（默认级别/格式，别名无顶层开关）+ 打关键路径。
        configure_logging("INFO", "text")
        _log_startup_paths()
        try:
            # 传给子系统的应是**剥掉前置 `--json` 之后**的 argv：
            # 否则 `omnicrawler --json pdf --help` 会把 `--json` 当 pdf 的参数，
            # 且下面 `"--json" in argv` 的判断与子系统看到的参数不一致。
            _dispatch_pdf(_pdf_head)
        except RuntimeError as exc:
            # **这条是"转发路径"，绕过 `_handlers` 注册表**（第 138 行直接调用）。
            # 插件未安装时 facade 抛的 RuntimeError 会以裸 traceback 呈现——
            # 用户跑 `omnicrawler pdf --help` 只是想看功能，不该看到栈。
            #
            # 转成干净提示 + 非零退出码：保留"这不是正常成功"的语义，
            # 又不吓到人。
            message = str(exc).strip()
            if "插件" not in message and "plugin" not in message.lower():
                raise
            hint = (
                "提示：PDF 工作台的子命令与选项由插件提供。"
                "装好 document-workbench 后\n  omnicrawler pdf --help   即可看到完整子命令列表。"
            )
            # 退出码保持**非零**：`pdf --help` 的「结果」是**看到子命令列表**，
            # 而缺插件时并没有看到 —— 帮助请求本身未被满足。
            # （2026-11-24 我一度改成 0，被既有测试 `test_pdf_help_without_plugin_is_a_clean_message`
            #  当场驳回，理由「未装插件却退码 0 ⇒ 脚本会误以为成功」成立。）
            exit_code = 1
            print(f"omnicrawler {' '.join(argv)}: {message}", file=sys.stderr)
            print(f"\n{hint}", file=sys.stderr)
            # ★ `--json` 时 stdout 也必须有 envelope（2026-11-24 实测：此前这条分支
            # 只往 stderr 打文本，stdout 完全为空 ⇒ agent 拿不到任何结构化结果）。
            # 这条路径绕过了 `_handlers` 注册表，所以也绕过了 `run_with_envelope`；
            # 需要在这里补上，否则「`--json` 时 stdout 是合法 JSON」对 `pdf` 不成立。
            if "--json" in argv:
                from ._json_envelope import build_payload

                print(
                    json.dumps(
                        build_payload(
                            "pdf",
                            ok=False,
                            output=f"{message}\n\n{hint}",
                            exit_code=exit_code,
                            exc=RuntimeError(message),
                            # 插件未安装是**配置/安装问题**：原样重试永远不会成功，
                            # 必须装插件。默认按类型推断会把 RuntimeError 判成可重试。
                            is_retryable=False,
                        ),
                        ensure_ascii=False,
                    )
                )
            raise SystemExit(exit_code) from None
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
    _log_startup_paths(getattr(args, "config", None))
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        print("已中断；已完成状态保存在SQLite中，可用resume继续。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # CLI error boundary.
        # A-P3-8：错误出口统一走脱敏管道；仅调试模式（--log-level DEBUG）保留原始异常文本。
        debug = getattr(args, "log_level", "INFO") == "DEBUG"
        if debug:
            import traceback

            print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        else:
            print(f"错误: {_format_cli_error(exc)}", file=sys.stderr)
        _print_error_hint(exc)
        raise SystemExit(1)


def _log_startup_paths(config_path: str | None = None) -> None:
    """启动首行日志：打印关键路径，排障不迷路（S4.2 ③；W6.7-④ 起两条入口共用）。

    统一入口与 `pdf` 转发路径都调用它 —— 同一次运行不该因为入口不同而少一行排障信息。
    """
    import logging as _logging

    root_logger = _logging.getLogger("omnicrawler")
    root_logger.info("omnicrawler %s 启动; data_dir=%s", __version__, _data_dir_hint())
    if config_path:
        root_logger.info("config_path=%s", config_path)


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
      --template generic/single_page

  常用命令:
    omnicrawler run    -c <配置>    运行采集任务
    omnicrawler status -c <配置>    查看任务状态
    omnicrawler doctor -c <配置>    诊断配置和环境
    omnicrawler templates list      浏览内置模板库

  详细帮助: omnicrawler -h
  文档: OmniCrawler-用户指南.md
""")


def _format_cli_error(exc: Exception, *, debug: bool = False) -> str:
    """A-P3-8：CLI 错误出口消息格式化。

    非调试模式经 :func:`redact_value` 脱敏管道，URL 内嵌凭据不外泄；
    调试模式才保留原始异常类型与消息（堆栈由调用方在 ``main`` 内打印）。
    """
    if debug:
        return f"{type(exc).__name__}: {exc}"
    from ..security.redaction import redact_value

    return f"{type(exc).__name__}: {redact_value(str(exc))}"


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
        # **必须区分「文件权限」与「网络权限」。**
        #
        # 早先这条不区分解读，任何 PermissionError 都提示「检查网络代理设置」——
        # 而真实场景里绝大多数是**本地文件不可写**（数据目录只读、
        # `components/.registry.lock` 被占用、便携包解压到只读位置）。
        # 2026-11-23 我就在这条上被误导过：`omnicrawler components list`
        # 报 registry.lock 的 PermissionError，提示却是「检查网络代理」。
        # **错误的建议比没有建议更糟** —— 它让人往完全无关的方向排查。
        filename = getattr(exc, "filename", None)
        if filename:
            hints.append(
                f"无法访问 {filename}；请确认该路径可读写"
                "（若在便携包/只读介质中，请解压到可写位置）"
            )
        else:
            hints.append("请确认目标网站允许自动访问，或检查网络代理设置")
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        hints.append("网络连接失败；请检查目标网址是否可访问，或尝试降低并发数")
    else:
        hints.append("请运行 omnicrawler doctor 检查环境，或查看 OmniCrawler-用户指南.md")
    if hints:
        print(f"  💡 修复建议: {hints[0]}", file=sys.stderr)


def _dispatch_pdf(argv: list[str]) -> None:
    # 第 2 批：文档子系统挂点统一经由 `services.document_facade`。
    # 好处是组件化时只改 facade 一处；且缺组件时给的是可操作提示而非 ImportError 栈。
    from ..services import document_facade

    cmd, rest = argv[0], argv[1:]
    if cmd == "pdf":
        # pdfx.cli.main() 读 sys.argv；facade 负责按需替换（避免调用方各写一遍）。
        document_facade.pdf_main(rest)
    elif cmd == "pdf-process":
        raise SystemExit(document_facade.pdf_processor_main(rest))
    elif cmd == "pdf-extract":
        raise SystemExit(document_facade.field_extractor_main(rest))


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

    if getattr(args, "json", False):
        # **统一 JSON 包装**：命令自身没实现 `--json` 时，把它的 stdout
        # 收进结构化 envelope 再输出，而不是要求 56 个 handler 各改一遍。
        #
        # 这是折中：`output` 字段仍是文本（人读内容原样保留），但
        # `ok` / `command` / `exit_code` 是结构化的 —— agent 至少能可靠判断
        # 成败，不必解析中文日志。真正结构化的命令（已实现 --json 的那 8 个）
        # 会写自己的 JSON，这里检测到输出已是 JSON 就原样透传。
        from ._json_envelope import run_with_envelope

        # **退出码必须与 envelope 里的 `exit_code` 一致。**
        # 早先这里丢弃了返回值，于是一个真实失败（envelope 说 exit_code=1）
        # 进程却以 0 退出 —— agent 会把它当成功，脚本 `&&` 链也不会中断。
        # 2026-11-23 实测 `--json status -c nope.yaml` 就是这样。
        code = run_with_envelope(args.command, handler, args)
        if code:
            raise SystemExit(code)
        return

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
    # ★ 2026-11-27：`yaml` 改为**用到时**才导入。
    #
    #   实测 `import yaml` 约 17 ms，而它**只被 `omnicrawler init` 这一条命令用到**。
    #   此前它挂在 `cli/_main.py` 顶端，于是 `--help` / `run` / `status` …
    #   每一次启动都付这 17 ms。CLI 冷启动是用户感知最强的一处（`--help` 是
    #   第一条命令），「只为一条命令 import 一个包」不值得全量付账。
    import yaml

    atomic_write_text(output_path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
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
            # 不可用运行（未取到页面或未成功结束）会入库但不成为基线；
            # 若不显式说明，用户会误以为「首次基准记录」是正常状态。
            if not result.usable:
                print(f"  ⚠ 本次运行不可作为基准（status={result.status or '异常'}，pages={result.pages}）——已记录，但不参与基线对比")
            elif check.get("comparable") is False:
                # W6.6：四维（场景/输入快照/有效配置/依赖环境）没对齐就**不下退化结论**，
                # 但要明确报出"为什么不可比"——静默跳过等于把可比性问题藏起来。
                raw_reasons = check.get("incomparable_reasons")
                reasons = "；".join(
                    str(item) for item in (raw_reasons if isinstance(raw_reasons, list) else [])
                )
                print(f"  ⚠ 与历史基线不可比，不下退化结论：{reasons}")
            elif check.get("regression"):
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
