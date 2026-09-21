"""`tools/post_bump_steps.py` 的判据：幂等 · 不静默 · 漂移可见。

★ 这个工具存在的理由是"bump 之后有三处手工补齐漏过"。所以用例的重点不是"它会改文件"，
而是**"漏了会被发现"**：版本号不认识必须报错、版本头漂移必须报出来、重复执行必须 skip。
"""

from __future__ import annotations

from pathlib import Path

from tools import post_bump_steps as pbs

OLD = "0.13.0"
NEW = "0.13.1"
DRIFTED = "0.12.0"


def _stage(root: Path) -> Path:
    """搭一棵最小仓库：uv.lock + 两个版本声明的门禁页 + 带版本头的文档 + 已重命名的报告。"""
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "docs" / "archive").mkdir(parents=True)
    (root / "docs" / "archive" / "README.md").write_text(
        f"# archive\n\n当前版本线为 **{OLD}**（见 pyproject.toml）。\n", encoding="utf-8"
    )
    (root / "uv.lock").write_text(
        "version = 1\n\n"
        "[[package]]\n"
        'name = "something-else"\n'
        'version = "9.9.9"\n'
        "\n"
        "[[package]]\n"
        f'{pbs._PROJECT_ANCHOR}version = "{OLD}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )
    (root / "docs" / "releases" / "README.md").write_text(
        f"| `RELEASE_REPORT_{OLD}.md` | 当前版本 {OLD} 的发布报告 |\n", encoding="utf-8"
    )
    (root / "docs" / "releases" / f"RELEASE_REPORT_{NEW}.md").write_text(
        "placeholder\n", encoding="utf-8"
    )
    for name, version in (
        ("INSTALLATION.md", OLD),
        ("LINUX_INSTALL.md", OLD),
        ("OPERATIONS.md", DRIFTED),  # 既不是 OLD 也不是 NEW ⇒ 漂移
    ):
        (root / "docs" / name).write_text(
            f"# {name}\n\n{pbs._VERSION_HEADER}{version} · 配置协议：v5\n", encoding="utf-8"
        )
    return root


def test_applies_all_steps_then_is_idempotent(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("# OmniCrawler 0.13.1 发布报告\n\n真实内容\n", encoding="utf-8")

    assert pbs.run(root, OLD, NEW, draft, dry_run=False) == 0

    # ① uv.lock 项目版本改了，其它包的版本不受影响
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert f'{pbs._PROJECT_ANCHOR}version = "{NEW}"' in lock
    assert 'name = "something-else"\nversion = "9.9.9"' in lock

    # ② 两个版本声明门禁页的引用同步（releases 与 archive 各一处）
    assert f"RELEASE_REPORT_{NEW}.md" in (root / "docs" / "releases" / "README.md").read_text(
        encoding="utf-8"
    )
    assert f"当前版本线为 **{NEW}**" in (root / "docs" / "archive" / "README.md").read_text(
        encoding="utf-8"
    )

    # ③ 版本头：OLD → NEW；漂移的那份**不动**
    assert f"{pbs._VERSION_HEADER}{NEW}" in (root / "docs" / "INSTALLATION.md").read_text(
        encoding="utf-8"
    )
    assert f"{pbs._VERSION_HEADER}{DRIFTED}" in (root / "docs" / "OPERATIONS.md").read_text(
        encoding="utf-8"
    )

    # ④ 报告正文换成草稿内容
    assert (root / "docs" / "releases" / f"RELEASE_REPORT_{NEW}.md").read_text(
        encoding="utf-8"
    ) == draft.read_text(encoding="utf-8")

    # ★ 幂等：再跑一次必须全是 skip（但 run 的返回仍是 0）
    assert pbs.run(root, OLD, NEW, draft, dry_run=False) == 0
    assert pbs.fix_uv_lock(root, OLD, NEW, dry_run=True).status == pbs._STATUS_SKIP
    assert all(
        r.status == pbs._STATUS_SKIP
        for r in pbs.fix_version_statements(root, OLD, NEW, dry_run=True)
    )
    assert pbs.install_report(root, draft, NEW, dry_run=True).status == pbs._STATUS_SKIP


def test_check_reports_pending_then_clean(tmp_path: Path) -> None:
    """`--check` 是只读的：未完成 ⇒ 非零；完成 ⇒ 0；且**不修改任何文件**。"""
    root = _stage(tmp_path)
    draft = tmp_path / "draft.md"
    draft.write_text("fresh content\n", encoding="utf-8")
    before = {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file()
    }

    assert pbs.run(root, OLD, NEW, draft, dry_run=True) == 1, "未完成时必须判红"
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before, "--check 不得改动任何文件"

    assert pbs.run(root, OLD, NEW, draft, dry_run=False) == 0
    assert pbs.run(root, OLD, NEW, draft, dry_run=True) == 0, "补齐后 --check 必须通过"


def test_uv_lock_anchor_missing_is_an_error_not_a_silent_pass(tmp_path: Path) -> None:
    """★ 反向断言：锚点没了 ⇒ 必须报错退出，而不是"看着像成功"。"""
    root = _stage(tmp_path)
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    result = pbs.fix_uv_lock(root, OLD, NEW, dry_run=False)
    assert result.status == pbs._STATUS_ERROR
    assert "anchor" in result.message
    assert pbs.run(root, OLD, NEW, None, dry_run=False) == 1


def test_uv_lock_unexpected_project_version_is_an_error(tmp_path: Path) -> None:
    """★ 反向断言：项目版本既不是 old 也不是 new ⇒ 明确报错并**说出实际值**。"""
    root = _stage(tmp_path)
    lock = (root / "uv.lock").read_text(encoding="utf-8").replace(f'"{OLD}"', '"0.9.9"')
    (root / "uv.lock").write_text(lock, encoding="utf-8")
    result = pbs.fix_uv_lock(root, OLD, NEW, dry_run=False)
    assert result.status == pbs._STATUS_ERROR
    assert "0.9.9" in result.message, "要报出实际值，便于判断是不是版本号记错了"


def test_version_header_drift_is_reported_but_not_rewritten(tmp_path: Path) -> None:
    """★ 漂移只报告、不代改：某份文档可能**有意**标注更早的适用版本。"""
    root = _stage(tmp_path)
    _, drift = pbs.fix_version_headers(root, OLD, NEW, dry_run=False)
    assert any("OPERATIONS.md" in line and DRIFTED in line for line in drift), drift
    assert not any("INSTALLATION.md" in line for line in drift), "已同步的那份不该被报漂移"
    assert f"{pbs._VERSION_HEADER}{DRIFTED}" in (root / "docs" / "OPERATIONS.md").read_text(
        encoding="utf-8"
    ), "漂移文档不得被改写"


def test_missing_report_draft_is_reported(tmp_path: Path) -> None:
    root = _stage(tmp_path)
    result = pbs.install_report(root, tmp_path / "nope.md", NEW, dry_run=False)
    assert result.status == pbs._STATUS_ERROR
    assert pbs.run(root, OLD, NEW, tmp_path / "nope.md", dry_run=False) == 1


def test_missing_archive_gate_page_is_reported(tmp_path: Path) -> None:
    """版本声明的门禁页缺失 ⇒ 报错（它是文档门禁要求存在的页面，不能静默跳过）。"""
    root = _stage(tmp_path)
    (root / "docs" / "archive" / "README.md").unlink()
    results = pbs.fix_version_statements(root, OLD, NEW, dry_run=False)
    assert any(r.status == pbs._STATUS_ERROR and "archive" in r.message for r in results), results
    assert pbs.run(root, OLD, NEW, None, dry_run=False) == 1


def test_index_page_and_prose_mentions_are_not_treated_as_headers(tmp_path: Path) -> None:
    """★ 精度要求：只认**行首**的元数据行，并跳过导航页。

    实测（2026-09-20）：`docs/README.md` 在正文里引用「> 适用版本：」这个标记本身，
    按子串匹配会造出一条假漂移 —— 判据必须能区分"自己的元数据"与"正文提到的字符串"。
    """
    root = _stage(tmp_path)
    (root / "docs" / "README.md").write_text(
        "# 文档导航\n\n每个文档须带 `> 适用版本：` 元数据行。\n", encoding="utf-8"
    )
    (root / "docs" / "PROSE_MENTION.md").write_text(
        "# 说明\n\n本页讲 `> 适用版本：` 该怎么写。\n", encoding="utf-8"
    )

    results, drift = pbs.fix_version_headers(root, OLD, NEW, dry_run=False)
    touched = " ".join(r.message for r in results)

    assert "docs/README.md" not in touched, "导航页不是带元数据的文档，不得被处理"
    assert "PROSE_MENTION.md" not in touched, "正文提及不得被当成元数据头"
    # 桩里那份**有意**漂移的 OPERATIONS.md 仍应被报出；但不得多出这两条的假漂移
    assert len(drift) == 1 and "OPERATIONS.md" in drift[0], drift
