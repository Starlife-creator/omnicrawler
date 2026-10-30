#!/usr/bin/env python3
"""i18n 覆盖率实测统计（C-#4 / C-#27）。

不相信 README 散文里的口径数字，只解析 ``locale/`` 下的 ``.pot`` 模板与
``.po`` 语言包条目，实测：

* ``pot_entries`` —— 模板中待翻译的 msgid 总数（不含头部空条目）；
* ``po_entries`` —— 语言包中已登记的 msgid 数；
* ``translated`` —— 语言包中 msgstr 非空、且命中模板的条目数；
* ``coverage`` —— ``translated / pot_entries``；
* ``missing`` —— 模板中尚未提供译文的条目数。

用法::

    python tools/i18n_stats.py                     # 打印实测统计（人类可读）
    python tools/i18n_stats.py --json             # 同一份统计的 JSON 形态
    python tools/i18n_stats.py --check --min-coverage 0.40   # 覆盖率门禁，低于阈值退出 1

解析仅依赖标准库，支持 gettext 单行与 ``"..."`` 续行拼接；不依赖 polib / msgfmt。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POT_PATH = PROJECT_ROOT / "locale" / "omnicrawler-gui.pot"
PO_PATH = PROJECT_ROOT / "locale" / "en_US" / "LC_MESSAGES" / "omnicrawler-gui.po"


def _unquote(token: str) -> str:
    """解析 gettext 字符串字面量（去引号并解码标准转义）。"""
    token = token.strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        token = token[1:-1]
    return (
        token.replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\\\", "\\")
    )


def _parse_messages(path: Path) -> list[tuple[str, str]]:
    """解析 ``.pot`` / ``.po``，返回 ``[(msgid, msgstr), ...]``（去掉头部空条目）。"""
    text = path.read_text(encoding="utf-8")
    messages: list[tuple[str, str]] = []
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []
    mode: str | None = None

    def flush() -> None:
        nonlocal mode, msgid_parts, msgstr_parts
        if mode is None:
            return
        messages.append(("".join(msgid_parts), "".join(msgstr_parts)))
        mode = None
        msgid_parts = []
        msgstr_parts = []

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            flush()
            mode = "msgid"
            msgid_parts = [_unquote(line[6:])]
        elif line.startswith("msgstr "):
            mode = "msgstr"
            msgstr_parts = [_unquote(line[7:])]
        elif line.startswith('"') and mode is not None:
            if mode == "msgid":
                msgid_parts.append(_unquote(line))
            else:
                msgstr_parts.append(_unquote(line))
        elif line == "":
            flush()
    flush()

    # 头部空条目（msgid ""）不计入业务条目。
    return [(msgid, msgstr) for msgid, msgstr in messages if msgid != ""]


def measure(
    pot_path: Path | None = None, po_path: Path | None = None
) -> dict[str, object]:
    """返回实测统计字典；文件缺失时给出可判读的错误，不静默归零。"""
    pot_path = pot_path or POT_PATH
    po_path = po_path or PO_PATH
    if not pot_path.is_file():
        raise FileNotFoundError(f"缺少翻译模板: {pot_path}")
    pot_messages = _parse_messages(pot_path)
    pot_ids = [msgid for msgid, _ in pot_messages if msgid]

    po_map: dict[str, str] = {}
    po_entries = 0
    if po_path.is_file():
        po_messages = _parse_messages(po_path)
        po_entries = len(po_messages)
        po_map = {msgid: msgstr for msgid, msgstr in po_messages}

    translated = sum(1 for msgid in pot_ids if po_map.get(msgid, "").strip())
    total = len(pot_ids)
    coverage = (translated / total) if total else 0.0
    pot_id_set = set(pot_ids)
    stale = sorted(msgid for msgid in po_map if msgid not in pot_id_set)
    try:
        pot_label = str(pot_path.relative_to(PROJECT_ROOT))
    except ValueError:
        pot_label = str(pot_path)
    return {
        "pot_path": pot_label,
        "pot_entries": total,
        "po_entries": po_entries,
        "translated": translated,
        "missing": total - translated,
        "stale": len(stale),
        "coverage": round(coverage * 100, 1),
        "coverage_ratio": round(coverage, 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OmniCrawler i18n 覆盖率实测统计")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计结果")
    parser.add_argument(
        "--check",
        action="store_true",
        help="覆盖率门禁：低于 --min-coverage 时退出码 1",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.0,
        metavar="RATIO",
        help="最低覆盖率（0~1 比例），配合 --check 使用",
    )
    args = parser.parse_args(argv)

    stats = measure()

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(
            f"模板条目 {stats['pot_entries']} · 语言包条目 {stats['po_entries']} · "
            f"已译 {stats['translated']} · 未译 {stats['missing']} · "
            f"陈旧条目 {stats['stale']} · 覆盖率 {stats['coverage']}%"
        )

    if args.check:
        if float(stats["coverage_ratio"]) < args.min_coverage:
            print(
                f"i18n 覆盖率 {stats['coverage']}% 低于门禁 "
                f"{args.min_coverage * 100:.1f}%（缺 {stats['missing']} 条译文）",
                file=sys.stderr,
            )
            return 1
        print(f"i18n 覆盖率门禁通过（≥ {args.min_coverage * 100:.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
