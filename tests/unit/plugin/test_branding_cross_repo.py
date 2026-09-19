"""品牌资产的跨仓一致性（《优化方案.md》§九附录 A §3.11(h) / B5）。

主仓与市场仓各自**独立落盘**同一批品牌文件（两个仓库不能互指），所以「主仓改了
lockup、市场仓忘了」这种漂移只能由断言看住，不能靠「记得一起改」——原包被判定的
病根正是文档/资产与实物不符。

断言放在**主仓**（§3.11(h)）：市场仓的 validate.yml 有 check_workflow_policy.py 守着，
动它的 workflow 会引入额外面。`market compatibility` job 在「两仓同时在场」时执行本目录。

单用例、分支内断言：多条断言各自跳过会在缺市场仓时产生多个不可见跳过。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BRANDING = REPO_ROOT / "assets" / "branding"

# 与 tools/checkout_market.py 的默认落点一致（两仓同级）。
MARKET_ROOT = REPO_ROOT.parent / "OmniCrawler-market"
MARKET_BRANDING = MARKET_ROOT / "assets" / "branding"

# 两仓各自生成的清单：内容按各仓实有文件不同（市场仓只列它实有的 8 项），
# 因此**不**参与跨仓字节比对。
PER_REPO_FILES = frozenset({"MANIFEST.txt", "SHA256SUMS"})

# 市场仓是主仓的子集：README 要渲染的 4 个 lockup + 两份许可。
EXPECTED_MARKET_FILES = frozenset(
    {
        "FONT-NOTICE.txt",
        "LICENSE-OFL.txt",
        "lockup/omnicrawler-wordmark.svg",
        "lockup/omnicrawler-wordmark.png",
        "lockup/omnicrawler-wordmark-dark.svg",
        "lockup/omnicrawler-wordmark-dark.png",
    }
)

# 市场仓只发门面资产，不该出现图标与站点资源（§2.2）。
FORBIDDEN_IN_MARKET = ("omnicrawler.ico", "omnicrawler.icns", "masters/", "raster/", "web/")

_RASTER_SOURCE = re.compile(r"^\d+x\d+ raster of (\S+)$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _manifest_rows(manifest: Path) -> dict[str, str]:
    """市场仓 MANIFEST.txt 的 `- <仓库相对路径>  <说明>` 行 → {路径: 说明}。"""
    rows: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.startswith("- "):
            continue
        parts = line[2:].split(maxsplit=1)
        rows[parts[0]] = parts[1] if len(parts) > 1 else ""
    return rows


def test_branding_stays_byte_identical_across_the_two_repositories() -> None:
    if not MARKET_BRANDING.is_dir():
        pytest.skip("OmniCrawler-market 未 clone（需与主仓库同级）")

    market_files = _tree_files(MARKET_BRANDING)

    # ① 市场仓 MANIFEST 双向覆盖：列出的每一项真实存在，且树里没有未申报的文件。
    rows = _manifest_rows(MARKET_BRANDING / "MANIFEST.txt")
    listed = set(rows)
    assert listed, "市场仓 MANIFEST.txt 没有列出任何条目"
    missing = sorted(p for p in listed if not (MARKET_ROOT / p).is_file())
    assert not missing, f"市场仓 MANIFEST 列了不存在的路径：{missing}"
    undeclared = sorted(
        p for p in market_files if f"assets/branding/{p}" not in listed and p not in PER_REPO_FILES
    )
    assert not undeclared, f"市场仓有未在 MANIFEST 申报的文件：{undeclared}"

    # ② 市场仓的实有文件集合必须恰好是预期的子集（增删都要被发现）。
    assert market_files == EXPECTED_MARKET_FILES | PER_REPO_FILES, (
        "市场仓品牌文件集合与预期不符："
        f"多出={sorted(market_files - EXPECTED_MARKET_FILES - PER_REPO_FILES)}，"
        f"缺少={sorted(EXPECTED_MARKET_FILES - market_files)}"
    )

    # ③ 市场仓不该发的资产（图标、站点资源）一个都不能出现，MANIFEST 也不得提及。
    for name in FORBIDDEN_IN_MARKET:
        assert not (MARKET_BRANDING / name).exists(), f"市场仓不该包含 {name}"
        offenders = sorted(k for k in listed if name in k)
        assert not offenders, f"市场仓 MANIFEST 不该提及 {name}：{offenders}"

    # ④ 跨仓字节相同 —— 这是本用例存在的理由。逐文件比对，并在失败时报出是哪几个。
    drifted = []
    for rel in sorted(EXPECTED_MARKET_FILES):
        main_file = BRANDING / rel
        assert main_file.is_file(), f"主仓缺少 {rel}，市场仓无从对齐"
        if _sha256(main_file) != _sha256(MARKET_BRANDING / rel):
            drifted.append(rel)
    assert not drifted, f"两仓同名品牌文件字节不同（漂移）：{drifted}"

    # ⑤ 市场仓 SHA256SUMS 必须与它自己的字节一致（清单本身不能撒谎）。
    sums = {}
    for line in (MARKET_BRANDING / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, size, rel = line.split(maxsplit=2)
        sums[rel] = (digest, int(size))
    assert set(sums) == {f"assets/branding/{p}" for p in market_files - {"SHA256SUMS"}}, (
        "市场仓 SHA256SUMS 覆盖的文件集合与实际不符"
    )
    for rel, (digest, size) in sorted(sums.items()):
        target = MARKET_ROOT / rel
        assert target.stat().st_size == size, f"{rel} 大小与 SHA256SUMS 不符"
        assert _sha256(target) == digest, f"{rel} 哈希与 SHA256SUMS 不符"

    # ⑥ MANIFEST 里 raster 行声明的来源 SVG 必须在该仓解析得到。
    #    （生成器曾把来源写成 pack 命名空间的 wordmark/...，市场仓里解析不到。）
    for rel, description in sorted(rows.items()):
        match = _RASTER_SOURCE.match(description)
        if match is None:
            continue
        source = MARKET_ROOT / match.group(1)
        assert source.is_file(), f"{rel} 声明的来源 {match.group(1)} 在市场仓不存在"

    # ⑦ README 门面：两仓插入的 lockup 片段必须逐字相同（§3.6）。
    #    主仓 README 是 CRLF 检出、市场仓全库 LF，故按内容比对（行尾差异由
    #    .gitattributes 的字节契约单独看住）。
    def snippet(readme: Path) -> str:
        text = readme.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "</p>" in text, f"{readme} 顶部没有 lockup 片段"
        head = text.split("</p>", 1)[0]
        assert "assets/branding/lockup/" in head, f"{readme} 顶部的片段没有指向仓内 lockup"
        assert "http" not in head, f"{readme} 顶部的片段使用了绝对 URL"
        return head + "</p>"

    main_snippet = snippet(REPO_ROOT / "README.md")
    market_snippet = snippet(MARKET_ROOT / "README.md")
    assert main_snippet == market_snippet, "两仓 README 的 lockup 片段不一致"

    # 片段引用的每个文件都必须在**各仓自己**解析得到 —— 断图是用户直接看得见的缺陷。
    referenced = re.findall(r'(?:srcset|src)="([^"]+)"', main_snippet)
    assert referenced, "lockup 片段没有引用任何文件"
    for rel in referenced:
        assert (REPO_ROOT / rel).is_file(), f"主仓 README 引用的 {rel} 不存在"
        assert (MARKET_ROOT / rel).is_file(), f"市场仓 README 引用的 {rel} 不存在"

    # ⑧ 市场仓品牌目录的行尾保护规则不得被删（§3.11(c)：这是防 PNG 落回
    #    `* text=auto` 的唯一机会）。
    attrs = (MARKET_ROOT / ".gitattributes").read_text(encoding="utf-8")
    for rule in ("/assets/branding/**/*.svg", "/assets/branding/**/*.png"):
        assert rule in attrs, f"市场仓 .gitattributes 缺少作用域规则 {rule}"
