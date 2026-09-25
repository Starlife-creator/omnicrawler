"""sync_snapshot.py 快照-钉值联动守卫（2026-09-25 发布 0.4.0 事故固化）。

背景：主仓 CI 跨仓守卫（``test_market_catalog_cross_repo``）按
``constraints/market-ref.txt`` 钉值检出市场仓；快照升到 0.4.0 而钉值停在
0.3.0 时代的 ``887a612`` ⇒ 「plugins 条目内容漂移」必红。本测试锁死
``sync_snapshot.py`` 的联动行为：

1. 干净市场仓：同步成功且钉值自动跟升到市场 HEAD（同一原子变更）；
2. 脏市场仓（有未提交改动）：同步被拒绝（fail-closed），钉值与快照均不动；
3. 钉值已一致：幂等，不重写；
4. 非 git 检出的市场仓：跳过钉值更新但同步继续（降级兼容）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "sync_snapshot.py"

GIT_ENV_ARGS = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return r.stdout.strip()


def _make_market_repo(tmp_path: Path) -> tuple[Path, str]:
    """构造一个最小市场仓（catalog + authors/ + keys/，已提交）。"""
    mkt = tmp_path / "OmniCrawler-market"
    (mkt / "authors").mkdir(parents=True)
    (mkt / "keys").mkdir(parents=True)
    (mkt / "catalog.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
    (mkt / "authors" / "a.json").write_text("{}\n", encoding="utf-8")
    (mkt / "keys" / "trust.pem").write_text("KEY\n", encoding="utf-8")
    _git(["init", "-q", "-b", "main"], mkt)
    _git([*GIT_ENV_ARGS, "add", "-A"], mkt)
    _git([*GIT_ENV_ARGS, "commit", "-q", "-m", "init"], mkt)
    return mkt, _git(["rev-parse", "HEAD"], mkt)


def _run(mkt: Path, dest: Path, ref: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--market-repo", str(mkt),
            "--dest", str(dest),
            "--ref-file", str(ref),
            *extra,
        ],
        capture_output=True, text=True,
    )


def test_clean_market_sync_bumps_pin(tmp_path: Path) -> None:
    mkt, head = _make_market_repo(tmp_path)
    dest = tmp_path / "main" / "market"
    ref = tmp_path / "main" / "constraints" / "market-ref.txt"
    ref.parent.mkdir(parents=True)
    ref.write_text("0" * 40 + "\n", encoding="utf-8")

    r = _run(mkt, dest, ref)

    assert r.returncode == 0, r.stderr
    assert ref.read_text(encoding="utf-8").strip() == head, "钉值必须跟升到市场 HEAD"
    assert (dest / "catalog.json").is_file()
    assert (dest / "keys" / "trust.pem").is_file()
    assert "[OK] 已更新市场钉值" in r.stdout


def test_dirty_market_repo_refuses_sync(tmp_path: Path) -> None:
    mkt, _ = _make_market_repo(tmp_path)
    (mkt / "catalog.json").write_text('{"schema_version": 2}\n', encoding="utf-8")
    dest = tmp_path / "main" / "market"
    ref = tmp_path / "main" / "constraints" / "market-ref.txt"
    ref.parent.mkdir(parents=True)
    ref.write_text("1" * 40 + "\n", encoding="utf-8")

    r = _run(mkt, dest, ref)

    assert r.returncode == 1, r.stdout
    assert "不干净" in r.stderr
    assert ref.read_text(encoding="utf-8").strip() == "1" * 40, "拒绝同步时钉值不得被改写"
    assert not (dest / "catalog.json").exists(), "拒绝同步时不得写快照"


def test_already_pinned_is_idempotent(tmp_path: Path) -> None:
    mkt, head = _make_market_repo(tmp_path)
    dest = tmp_path / "main" / "market"
    ref = tmp_path / "main" / "constraints" / "market-ref.txt"
    ref.parent.mkdir(parents=True)
    ref.write_text(head + "\n", encoding="utf-8")
    before = ref.read_text(encoding="utf-8")

    r = _run(mkt, dest, ref)

    assert r.returncode == 0, r.stderr
    assert ref.read_text(encoding="utf-8") == before, "钉值一致时不得重写"
    assert "市场钉值未变化" in r.stdout


def test_dry_run_reports_pin_change_without_write(tmp_path: Path) -> None:
    mkt, head = _make_market_repo(tmp_path)
    dest = tmp_path / "main" / "market"
    ref = tmp_path / "main" / "constraints" / "market-ref.txt"
    ref.parent.mkdir(parents=True)
    ref.write_text("0" * 40 + "\n", encoding="utf-8")

    r = _run(mkt, dest, ref, "--dry-run")

    assert r.returncode == 0, r.stderr
    assert ref.read_text(encoding="utf-8").strip() == "0" * 40, "dry-run 不得写钉值"
    assert f"{head[:12]}" in r.stdout, "dry-run 应报告钉值将更新"
    assert not (dest / "catalog.json").exists(), "dry-run 不得写快照"
