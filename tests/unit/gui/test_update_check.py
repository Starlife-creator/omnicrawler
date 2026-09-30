"""GUI「检查更新」：摘要格式化（B 站弹窗形态的体积对比）、状态分支，
以及**应用内更新**可选项与结果呈现（纯函数，与对话框共用同一判据）。"""

from __future__ import annotations

import pytest

from omnicrawler.gui.update_check import (
    MODE_FULL,
    MODE_INCREMENTAL,
    MODE_VERSIONS,
    apply_kwargs_for_mode,
    available_apply_modes,
    describe_apply_mode,
    format_apply_result,
    format_update_summary,
)


def test_summary_when_up_to_date() -> None:
    summary = format_update_summary(
        {"status": "up-to-date", "current_version": "0.15.0"}
    )
    assert "0.15.0" in summary
    assert "已是最新版本" in summary


def test_summary_shows_incremental_and_full_side_by_side() -> None:
    """★ 体积对比照 B 站弹窗：增量与全量并排（_human_bytes 为 1024 进制，标注 MB）。"""
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.14.0",
            "latest_version": "0.15.0",
            "options": {
                "incremental": {"available": True, "size": 51_200_000},
                "full": {"available": True, "size": 515_000_000, "via_fallback": False},
            },
            "payload_plan": {"needs_download": 3, "files_total": 2000},
            "notes": "修复了一些问题",
        }
    )
    assert "0.14.0" in summary and "0.15.0" in summary
    assert "增量更新（约 48.8MB）" in summary
    assert "全量包（约 491.1MB）" in summary
    assert "需更新 3/2000 个文件" in summary
    assert "修复了一些问题" in summary
    assert "可直接在应用内更新" in summary


def test_summary_labels_full_package_without_fallback_notion() -> None:
    """全量包只按体积报出 —— **不再**有"取自最近一次全量发布"这类兜底措辞。

    兜底字段已随"统一发布形态"删除（每版都发本平台全量包），仍出现那句提示就说明
    旧分支没删干净（或 `options.full` 又冒出了 `via_fallback`）。
    """
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.13.1",
            "latest_version": "0.14.1",
            "options": {
                "incremental": {"available": False, "size": 0},
                "full": {"available": True, "size": 515_000_000},
            },
        }
    )
    assert "全量包（约 491.1MB）" in summary
    assert "最近一次全量发布" not in summary
    assert "增量更新" not in summary


def test_summary_without_options_still_renders() -> None:
    """清单连 options 都没有的极端形态 ⇒ 不崩溃、至少报版本。"""
    summary = format_update_summary(
        {"status": "update-available", "current_version": "0.14.0", "latest_version": "0.15.0"}
    )
    assert "0.15.0" in summary


@pytest.mark.parametrize("code", [2, 3])
def test_summary_called_for_disabled_and_failed(code: int) -> None:
    """禁用/失败形态：摘要至少包含 detail（调用方用 Toast 呈现）。"""
    from omnicrawler.gui.update_check import format_update_summary as fmt

    summary = fmt({"status": "disabled" if code == 2 else "failed", "detail": "x"})
    assert summary == "当前版本：?\nx" or "当前版本" in summary


def test_summary_tells_the_user_to_install_manually_when_platform_cannot_auto_apply() -> None:
    """★ 平台不支持自动更新时，摘要必须给**可执行的**信息（下哪个包、多大），
    而不是只丢一句"请手动安装"，更不该给出一个点了必然失败的动作。
    """
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.15.0",
            "latest_version": "0.16.0",
            "options": {
                "auto_apply": False,
                "manual_install": True,
                "full": {
                    "available": True,
                    "size": 943_718_400,
                    "name": "OmniCrawler-0.16.0-macOS-Portable-Standard.dmg",
                },
            },
        }
    )

    assert "不支持自动更新" in summary
    assert "OmniCrawler-0.16.0-macOS-Portable-Standard.dmg" in summary
    assert "900.0MB" in summary
    # 不能出现"跑这条命令就行"的暗示：本平台根本落不了地
    assert "self-update apply" not in summary


def test_summary_keeps_the_apply_hint_on_supported_platforms() -> None:
    """反向的一半：能自动更新的平台照旧给命令（别把正常路径的指引删掉）。"""
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.15.0",
            "latest_version": "0.16.0",
            "options": {
                "auto_apply": True,
                "manual_install": False,
                "incremental": {"available": True, "size": 8_000_000},
                "full": {"available": True, "size": 514_000_000, "name": "pkg.zip"},
            },
        }
    )

    assert "可直接在应用内更新" in summary
    assert "不支持自动更新" not in summary


# ── 应用内更新：可选项与结果呈现（纯函数，与对话框共用同一判据）─────────────
# 此前 GUI 只给一句"请到命令行执行" —— 等于让 GUI 用户拿不到已经可用的能力。


def test_available_modes_are_ordered_by_cost() -> None:
    """可选项顺序＝推荐顺序（最省的在前），让用户默认点最低代价的那个。"""
    modes = available_apply_modes(
        {
            "auto_apply": True,
            "incremental": {"available": True, "size": 8_000_000},
            "full": {"available": True, "size": 514_000_000},
            "install_to_versions": True,
        }
    )

    assert modes == [MODE_INCREMENTAL, MODE_FULL, MODE_VERSIONS]


def test_available_modes_shrink_to_what_actually_exists() -> None:
    """没有增量包、也不允许装到 versions/ ⇒ 只给"全量·就地"这一项。"""
    modes = available_apply_modes(
        {
            "auto_apply": True,
            "incremental": {"available": False, "size": 0},
            "full": {"available": True, "size": 514_000_000},
            "install_to_versions": False,
        }
    )

    assert modes == [MODE_FULL]


def test_available_modes_empty_when_platform_cannot_auto_apply() -> None:
    """★ 反向断言：本平台没有自动落地能力 ⇒ **一个动作都不给**。

    "给了按钮但点了必然失败"比"没有按钮"更糟 —— macOS 上正是这种情况。
    """
    assert available_apply_modes(
        {
            "auto_apply": False,
            "manual_install": True,
            "incremental": {"available": False, "size": 0},
            "full": {"available": True, "size": 900_000_000},
            "install_to_versions": False,
        }
    ) == []
    # 只凭 manual_install 也要拦住（两个字段任一为假都不给动作）
    assert available_apply_modes({"manual_install": True}) == []


def test_mode_descriptions_state_size_and_disk_cost() -> None:
    """文案必须同时给**体积**与**磁盘代价**（用户要据此做选择）。"""
    options = {
        "incremental": {"available": True, "size": 8_000_000},
        "full": {"available": True, "size": 514_000_000},
    }

    assert "7.6MB" in describe_apply_mode(MODE_INCREMENTAL, options)
    assert "一份磁盘" in describe_apply_mode(MODE_FULL, options)
    assert "两份磁盘" in describe_apply_mode(MODE_VERSIONS, options)
    assert "490.2MB" in describe_apply_mode(MODE_FULL, options)


def test_apply_kwargs_mapping_matches_the_command_switches() -> None:
    """★ 方式 ⇒ 开关的映射只有一处；它必须与 `commands.self_update.apply` 的参数名一致。

    这里直接对着真实签名核对 —— 名字写错的话 CLI 侧会 TypeError，而 GUI 只会在
    用户点下按钮的那一刻才炸。
    """
    import inspect

    from omnicrawler.commands import self_update as cmd_self_update

    signature = inspect.signature(cmd_self_update.apply)
    for mode in (MODE_INCREMENTAL, MODE_FULL, MODE_VERSIONS):
        for key in apply_kwargs_for_mode(mode):
            assert key in signature.parameters, f"{mode} 用了不存在的开关 {key}"
    assert apply_kwargs_for_mode(MODE_INCREMENTAL) == {}
    assert apply_kwargs_for_mode(MODE_VERSIONS) == {"full": True, "to_versions": True}


def test_apply_result_tells_the_user_to_restart() -> None:
    """成功结果必须给**下一步**（重启才生效），而不是只说"完成了"。"""
    text = format_apply_result(
        {
            "status": "applied",
            "staged_version": "0.16.0",
            "applied_files": 12,
            "pending_cleanup": ["a.dll"],
            "plan": {"mode": "incremental"},
        },
        0,
    )

    assert "0.16.0" in text
    assert "12" in text
    assert "incremental" in text
    assert "重启" in text
    assert "占用" in text


def test_apply_result_surfaces_the_delta_fallback() -> None:
    """★ 自愈降级必须**让用户看见**（否则"我明明点了增量，却下了整包"无从解释）。"""
    text = format_apply_result(
        {
            "status": "applied",
            "staged_version": "0.16.0",
            "plan": {
                "mode": "full-archive",
                "delta_fallback": {"missing_count": 2, "missing_members": ["a", "b"]},
            },
        },
        0,
    )

    assert "全量包" in text
    assert "2" in text


def test_apply_result_renders_failure_reason() -> None:
    """失败结果要把原因原文带上（否则用户只知道"没成功"）。"""
    text = format_apply_result({"status": "failed", "detail": "变更包 sha256 不一致"}, 3)

    assert "未能完成" in text
    assert "sha256 不一致" in text
    assert "重启" not in text


def test_missing_auto_apply_key_does_not_block_actions() -> None:
    """★ 字段缺席 ⇒ 按"可更新"处理（与清单侧 `auto_apply` 缺省 true 同一口径）。

    反例（真实踩过）：把判据写成 `not options.get("auto_apply")` ⇒ 字段一缺席就静默
    失去更新按钮。这里把"缺席"和"显式 true"钉成同一个结果。
    """
    payload = {"incremental": {"available": True, "size": 1}, "full": {"available": True, "size": 2}}

    assert available_apply_modes(dict(payload)) == [MODE_INCREMENTAL, MODE_FULL]
    assert available_apply_modes({**payload, "auto_apply": True}) == [
        MODE_INCREMENTAL,
        MODE_FULL,
    ]


# ── 执行线程的接线（"按钮真的把开关传下去了吗"）─────────────────────────────

pytest.importorskip("PySide6")


@pytest.fixture()
def qapp():
    """与 tests/unit/gui 里的既有约定一致（QThread 需要 QApplication 实例）。"""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_apply_worker_forwards_the_mode_switches(qapp, monkeypatch) -> None:
    """★ 接线断言：`_ApplyWorker` 必须把**方式对应的开关**原样交给 commands 层。

    只测纯映射函数是不够的 —— 映射对、但 worker 忘了传、或传错参数名，用户点按钮那一刻
    才会炸（CLI 侧 TypeError）。这里直接把 `commands.self_update.apply` 换成记录器。
    """
    from omnicrawler.commands import self_update as cmd_self_update
    from omnicrawler.gui.update_check import _ApplyWorker

    seen: dict[str, object] = {}

    def fake_apply(*, config_path: str, **kwargs: object):
        seen["config_path"] = config_path
        seen["kwargs"] = kwargs
        return {"status": "applied", "staged_version": "1.2.3"}, cmd_self_update.EXIT_OK

    monkeypatch.setattr(cmd_self_update, "apply", fake_apply)
    worker = _ApplyWorker("/tmp/task.yaml", MODE_VERSIONS)
    captured: list[tuple[dict, int]] = []
    worker.finished_with.connect(lambda payload, code: captured.append((payload, code)))

    worker.run()   # 直接跑，不开事件循环（确定性）

    assert seen["config_path"] == "/tmp/task.yaml"
    assert seen["kwargs"] == {"full": True, "to_versions": True}, "装到 versions/ 必须带两个开关"
    assert captured == [({"status": "applied", "staged_version": "1.2.3"}, cmd_self_update.EXIT_OK)]


def test_apply_worker_reports_exceptions_instead_of_crashing_the_thread(qapp, monkeypatch) -> None:
    """★ 异常必须变成**可呈现的结果**：后台线程里抛出去会直接崩掉，用户什么也看不到。"""
    from omnicrawler.commands import self_update as cmd_self_update
    from omnicrawler.gui.update_check import _ApplyWorker

    def boom(*, config_path: str, **kwargs: object):
        raise RuntimeError("磁盘满了")

    monkeypatch.setattr(cmd_self_update, "apply", boom)
    worker = _ApplyWorker("/tmp/task.yaml", MODE_INCREMENTAL)
    captured: list[tuple[dict, int]] = []
    worker.finished_with.connect(lambda payload, code: captured.append((payload, code)))

    worker.run()

    assert len(captured) == 1
    payload, code = captured[0]
    assert payload["status"] == "failed"
    assert "磁盘满了" in payload["detail"]
    assert code == cmd_self_update.EXIT_FAILED
