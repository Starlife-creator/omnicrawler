"""随仓分发的字体必须「有声明、有许可文本」——读字体自身的 `name` 表核对，不靠文件名猜。

## 为什么

`docs/archive/omnicrawler-evaluation-report/_shared/fonts/` 里的字体**会被打进便携包**
（`build_windows.ps1` 把 `docs/` 整目录拷进发布根），所以这是**分发合规**问题：
SIL OFL 1.1 要求再分发时随附**许可文本与版权声明**。

2026-09-14 实测的缺口有两处，正是本用例要钉住的：
① 目录里**没有随附许可文本**（上游发布物一律带 `OFL.txt`）；
② `THIRD_PARTY_NOTICES.md` **只声明了 Instrument Sans**，漏了 JetBrains Mono 与 Outfit。

判定依据取**字体文件自身的 `name` 表**（名字 ID 1 = 家族名、ID 0 = 版权行），
因此"改个文件名"骗不过这个检查；文案措辞变化也不影响。
"""

from __future__ import annotations

import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FONTS_DIR = REPO_ROOT / "docs" / "archive" / "omnicrawler-evaluation-report" / "_shared" / "fonts"
NOTICES = REPO_ROOT / "THIRD_PARTY_NOTICES.md"

#: TrueType `name` 表里我们关心的名字 ID
_NAME_FAMILY = 1
_NAME_COPYRIGHT = 0


def _name_table(path: Path) -> dict[int, str]:
    """读取 TTF/OTF 的 `name` 表，返回 {名字 ID: 文本}（取第一条非空）。"""
    data = path.read_bytes()
    table_count = struct.unpack(">H", data[4:6])[0]
    tables: dict[str, tuple[int, int]] = {}
    for index in range(table_count):
        offset = 12 + 16 * index
        tag = data[offset : offset + 4].decode("latin-1")
        table_offset, table_length = struct.unpack(">II", data[offset + 8 : offset + 16])
        tables[tag] = (table_offset, table_length)
    assert "name" in tables, f"{path.name} 缺少 name 表"
    base, _ = tables["name"]
    _fmt, count, string_offset = struct.unpack(">HHH", data[base : base + 6])

    result: dict[int, str] = {}
    for index in range(count):
        record = base + 6 + 12 * index
        platform_id, _encoding, _lang, name_id, length, offset = struct.unpack(
            ">HHHHHH", data[record : record + 12]
        )
        raw = data[base + string_offset + offset : base + string_offset + offset + length]
        try:
            text = raw.decode("utf-16-be") if platform_id == 3 else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        if text.strip():
            result.setdefault(name_id, text.strip())
    return result


def _fonts() -> list[Path]:
    assert FONTS_DIR.is_dir(), f"缺少字体目录 {FONTS_DIR}"
    found = sorted(FONTS_DIR.glob("*.ttf")) + sorted(FONTS_DIR.glob("*.otf"))
    # 先确认扫描真的扫到了东西（否则后面的断言会假通过）
    assert found, f"{FONTS_DIR} 下一个字体都没有，检查用例是否失效"
    return found


def _declared_family_lines(notices: str) -> list[str]:
    """取「把某家族声明为随包字体」的行：**必须出现 `.ttf` 资产名**。

    不能只要求"含 fonts"——否则上游链接 `https://github.com/Outfitio/Outfit-Fonts` 会把家族名
    "顺带"带进来，让漏声明逃过检查（实测踩到，故收紧到 `.ttf`）。
    """
    return [line for line in notices.splitlines() if ".ttf" in line]


def test_every_shipped_font_family_is_declared_in_third_party_notices() -> None:
    """每个随仓分发的字体家族都要在 `THIRD_PARTY_NOTICES.md` 里被声明（按家族名核对）。"""
    notices = NOTICES.read_text(encoding="utf-8")
    declaration_lines = _declared_family_lines(notices)
    families = {}
    for font in _fonts():
        names = _name_table(font)
        family = names.get(_NAME_FAMILY)
        assert family, f"{font.name} 读不出家族名"
        families.setdefault(family, []).append(font.name)

    missing = sorted(
        family
        for family in families
        if not any(family in line for line in declaration_lines)
    )
    assert not missing, (
        f"以下字体家族随仓（且随便携包）分发，但未在 THIRD_PARTY_NOTICES.md 声明：{missing}。"
        f"已声明家族示例：{sorted(families)}"
    )


def test_license_text_is_shipped_next_to_the_fonts() -> None:
    """许可文本必须随字体一起分发，且**逐家族带上版权行**（OFL 1.1 的要求）。"""
    license_file = FONTS_DIR / "OFL.txt"
    assert license_file.is_file(), (
        "字体目录下缺少 OFL.txt —— OFL 1.1 要求再分发时随附许可与版权声明，"
        "上游发布物一律带这个文件"
    )
    text = license_file.read_text(encoding="utf-8")

    # 许可正文的特征串（确认不是占位文件）
    for marker in (
        "SIL OPEN FONT LICENSE Version 1.1",
        "PERMISSION & CONDITIONS",
        "Reserved Font Name",
        "OTHER DEALINGS IN THE FONT SOFTWARE",
    ):
        assert marker in text, f"OFL.txt 缺少许可正文特征串：{marker!r}"

    for font in _fonts():
        copyright_line = _name_table(font).get(_NAME_COPYRIGHT, "")
        assert copyright_line, f"{font.name} 的 name 表没有版权行"
        assert copyright_line in text, (
            f"OFL.txt 未包含 {font.name} 的版权声明：{copyright_line!r}（OFL 要求随附版权声明）"
        )


def test_fonts_are_ofl_licensed() -> None:
    """本目录的字体现状是 OFL 1.1；若换成别的许可，**必须**同步改本用例与声明。

    不是为了"锁死实现"，而是防止"换了字体/许可却没人更新声明"这种静默漂移。
    """
    for font in _fonts():
        names = _name_table(font)
        license_text = names.get(13, "")
        license_url = names.get(14, "")
        assert "SIL Open Font License" in license_text, (
            f"{font.name} 内嵌许可不是 OFL：{license_text[:80]!r}"
        )
        assert "openfontlicense.org" in license_url or "scripts.sil.org" in license_url, (
            f"{font.name} 内嵌许可 URL 异常：{license_url!r}"
        )
