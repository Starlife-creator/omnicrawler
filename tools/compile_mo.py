"""最小 .mo 编译器（纯标准库）——S4.3.2：把 locale/*/LC_MESSAGES/*.po 编译为 .mo。

用法: python tools/compile_mo.py [locale_dir]
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path


def parse_po(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    current_id: str | None = None
    current_str: str | None = None
    filling: str | None = None  # "id" / "str"

    def _join(value: str | None) -> str:
        if value is None:
            return ""
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', value)
        return "".join(
            part.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            .replace("\\\\", "\\")
            for part in parts
        )

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid_plural"):
            # 复数条目（msgid 含 \x00）不写入单数 .mo，避免 gettext 解析崩溃
            current_id = None
            current_str = None
            filling = None
            continue
        match = re.match(r"msgid\s+(.+)", line)
        if match:
            if current_id is not None and current_str is not None:
                entries.append((current_id, current_str))
            current_id = _join(match.group(1))
            current_str = None
            filling = "id"
            continue
        match = re.match(r"msgstr\s+(.+)", line)
        if match:
            current_str = _join(match.group(1))
            filling = "str"
            continue
        match = re.match(r'"(.*)"\s*$', line)
        if match:
            if filling == "str" and current_str is not None:
                current_str += _join(line)
            elif filling == "id" and current_id is not None:
                current_id += _join(line)
    if current_id is not None and current_str is not None:
        entries.append((current_id, current_str))
    return entries


def compile_mo(entries: list[tuple[str, str]]) -> bytes:
    ids = [msgid.encode("utf-8") for msgid, _ in entries]
    strs = [msgstr.encode("utf-8") for _, msgstr in entries]
    n = len(ids)
    table_start = 28
    id_table = table_start            # id 偏移表起始（紧随文件头）
    str_table = table_start + 8 * n  # str 偏移表起始（紧随 id 表）
    keys = sorted(range(n), key=lambda i: ids[i])
    table = bytearray()
    # id 偏移表（id 字符串区从两个表之后开始）
    id_offsets = []
    cur = table_start + 8 * n * 2
    for i in keys:
        id_offsets.append((cur, len(ids[i])))
        cur += len(ids[i])
    # str 偏移表（str 字符串区紧随 id 字符串区）
    str_offsets = []
    for i in keys:
        str_offsets.append((cur, len(strs[i])))
        cur += len(strs[i])
    # 偏移表项为 (length, offset)（GNU .mo 格式：length 在前）
    for off, length in id_offsets:
        table += struct.pack("II", length, off)
    for off, length in str_offsets:
        table += struct.pack("II", length, off)
    for i in keys:
        table += ids[i]
    for i in keys:
        table += strs[i]
    table += b"\x00"  # 尾部填充——gettext 要求所有字符串严格小于文件长度
    return struct.pack(
        "Iiiiiii", 0x950412DE, 0, n, id_table, str_table, 0, 0,
    ) + bytes(table)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "locale")
    compiled = 0
    for po in sorted(root.glob("*/LC_MESSAGES/*.po")):
        entries = parse_po(po.read_text(encoding="utf-8"))
        mo = po.with_suffix(".mo")
        mo.write_bytes(compile_mo(entries))
        print(f"{po} -> {mo} ({len(entries)} entries)")
        compiled += 1
    return 0 if compiled else 1


if __name__ == "__main__":
    raise SystemExit(main())
