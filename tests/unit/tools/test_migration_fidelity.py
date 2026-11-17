"""迁移完整性：插件代码必须与宿主归档的原始实现**逐行一致**（除导入路径）。

## 这条守卫防的是什么

2026-11-22 的实测：`pdfx/llm.py` 迁移时**漏掉了一行**

```python
user_agent=user_agent("PDF LLM extraction"),
```

后果是 LLM 出网不再带可识别 User-Agent——机器人不再诚实自报身份，
这是**合规回退**，不是简化。

而这个缺口**跑测试发现不了**：因为使用处先被删掉，
`from ..compat.utils import user_agent` 在导入期不报错。
"删掉了调用所以导入通过"的假绿，只能靠**比对原始实现**发现。

## 判据

对每个迁移过来的模块，与 `archive/document-workbench-host/` 里的原始文件比对，
只允许两类差异：

1. **导入语句**（承载方从 `omnicrawler.*` 变成 `omnicrawler_docs.*` / 相对导入）；
2. **注释与文档字符串**（记录迁移决策是必要的）。

其余任何差异都判红——那意味着业务逻辑被改动或遗漏。

## 为什么不放在插件仓

插件仓拿不到宿主归档目录（那是主仓的内容）。所以这条守卫留在主仓，
它的"被测对象"在 market 仓。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
ARCHIVE = _REPO / "archive" / "document-workbench-host"
PLUGIN = (
    _REPO.parent
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "document-workbench"
    / "omnicrawler_docs"
)

#: 允许的导入改写：`from omnicrawler…` / `from ..…` / `import omnicrawler…`
_IMPORT_LINE = re.compile(r"^\s*(from|import)\s+[\w.]")

#: 允许的纯注释行
_COMMENT_LINE = re.compile(r"^\s*#")

#: **有意重写**的模块：迁移时不是搬运而是改实现，允许与归档不同。
#:
#: 每一条都必须给出理由，且理由要能被独立核对。列入此表等于声明
#: "这个文件的差异是设计决定，不是遗漏"。
_INTENTIONAL_REWRITES: dict[str, str] = {
    "pdfx/component_ocr.py": (
        "宿主版自己读信任根验签、拿跨进程注册表锁做租赁、裁剪环境变量后启组件子进程；"
        "插件版把这三件事交回宿主（经 `omnicrawler_sdk` 的 component.ocr.recognize 能力），"
        "自己只负责交 PNG/收文本。安全边界因此集中在宿主侧，是本次迁移的设计决定。"
        "文件头部有完整说明。"
    ),
}


def _code_lines(text: str) -> list[str]:
    """抽出**纯代码行**：去掉注释、docstring、空行与导入。

    为什么这样比：迁移允许改动"说明性内容"（注释/docstring，例如
    `component_ocr.py` 里整块解释插件与宿主分工），但**不允许**改动
    可执行逻辑。逐行 diff 会把 docstring 的增删算成差异，噪声太大；
    剥掉非代码行后，剩下的差异就只可能是真实逻辑变化。

    用 `ast` 而不是正则：正则判断多行字符串边界不可靠。
    """
    import ast
    import io
    import tokenize

    try:
        tree = ast.parse(text)
    except SyntaxError:
        # 解析不了就退回朴素行过滤，宁可多报也不漏报
        return [
            line.strip() for line in text.splitlines()
            if line.strip() and not _COMMENT_LINE.match(line)
            and not _IMPORT_LINE.match(line)
        ]

    # 收集所有 docstring 节点覆盖的行号
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            start = first.lineno
            end = getattr(first, "end_lineno", start)
            docstring_lines.update(range(start, end + 1))

    # 注释行号（tokenize 没有 ast，按 token 取）
    comment_lines: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                comment_lines.add(token.start[0])
    except (tokenize.TokenError, IndentationError):
        pass

    result: list[str] = []
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or index in docstring_lines or index in comment_lines:
            continue
        if _IMPORT_LINE.match(line):
            continue
        result.append(stripped)
    return result


def _collect_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for group in ("pdfx", "convertx", "document_ir", "apps"):
        src_dir = ARCHIVE / group
        dst_dir = PLUGIN / group
        if not src_dir.is_dir() or not dst_dir.is_dir():
            continue
        for src in sorted(src_dir.rglob("*.py")):
            dst = dst_dir / src.relative_to(src_dir)
            if dst.is_file():
                pairs.append((src, dst))
    return pairs


@pytest.mark.skipif(
    not ARCHIVE.is_dir() or not PLUGIN.is_dir(),
    reason="需要主仓 archive/ 与 market 仓插件目录同时可读",
)
def test_migrated_modules_match_archive_except_imports() -> None:
    """除导入/注释外，插件代码必须与归档原件逐行一致。"""
    pairs = _collect_pairs()
    assert pairs, "没找到任何可比对的文件对——路径推算可能过期"

    problems: list[str] = []
    for src, dst in pairs:
        key = src.relative_to(ARCHIVE).as_posix()
        if key in _INTENTIONAL_REWRITES:
            continue
        a = _code_lines(src.read_text(encoding="utf-8"))
        b = _code_lines(dst.read_text(encoding="utf-8"))
        if a == b:
            continue
        diff = [
            line for line in difflib.unified_diff(a, b, lineterm="", n=0)
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        ]
        if diff:
            rel = src.relative_to(ARCHIVE)
            problems.append(
                f"{rel}（{len(diff)} 行代码差异）:\n"
                + "\n".join(f"    {line[:150]}" for line in diff[:8])
            )

    assert not problems, (
        f"{len(problems)} 个模块与归档原件存在非导入差异——"
        "迁移可能改动或遗漏了业务逻辑:\n\n" + "\n\n".join(problems)
    )


@pytest.mark.skipif(
    not ARCHIVE.is_dir() or not PLUGIN.is_dir(),
    reason="需要主仓 archive/ 与 market 仓插件目录同时可读",
)
def test_no_archive_module_missing_in_plugin() -> None:
    """归档里的每个模块都必须在插件里存在（不能丢文件）。"""
    missing: list[str] = []
    for group in ("pdfx", "convertx", "document_ir", "apps"):
        src_dir = ARCHIVE / group
        dst_dir = PLUGIN / group
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.rglob("*.py")):
            rel = src.relative_to(src_dir)
            if not (dst_dir / rel).is_file():
                missing.append(f"{group}/{rel}")
    assert not missing, f"以下模块在插件里缺失：{missing}"


@pytest.mark.skipif(not PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_llm_client_sends_user_agent() -> None:
    """LLM 出网必须带可识别 User-Agent（合规要求）。

    这条单独测，因为它曾经被漏掉且不影响导入，是最隐蔽的一类回归。
    """
    llm = PLUGIN / "pdfx" / "llm.py"
    if not llm.is_file():
        pytest.skip("找不到 llm.py")
    source = llm.read_text(encoding="utf-8")
    assert "user_agent=user_agent(" in source, (
        "LLM 请求没有传 user_agent —— 机器人不再诚实自报身份（合规回退）"
    )
