from __future__ import annotations

from pathlib import Path

from omnicrawler.runtime.scheduler import ScheduleStore


def test_schedule_lease_recovery_and_next_run(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "schedules.sqlite3") as schedules:
        schedule_id = schedules.add("demo", config, 60, start_at=100)
        first = schedules.claim_due(now=100, lease_seconds=30)
        assert [item["schedule_id"] for item in first] == [schedule_id]
        assert schedules.claim_due(now=110) == []
        recovered = schedules.claim_due(now=131)
        assert [item["schedule_id"] for item in recovered] == [schedule_id]
        schedules.finish(schedule_id, ok=True, now=140)
        saved = schedules.list()[0]
        assert saved["last_status"] == "succeeded"
        assert saved["next_run_at"] == 200


def test_s2519_finish_deleted_schedule_does_not_abort_loop(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "schedules2.sqlite3") as schedules:
        kept = schedules.add("kept", config, 60, start_at=100)
        deleted = schedules.add("deleted", config, 60, start_at=100)
        schedules.claim_due(now=100, lease_seconds=300)
        schedules.conn.execute("DELETE FROM schedules WHERE schedule_id=?", (deleted,))
        # finish 已删调度不再 KeyError 中断，后续调度正常收尾
        schedules.finish(deleted, ok=True, now=110)
        schedules.finish(kept, ok=True, now=110)
        assert schedules.list()[0]["last_status"] == "succeeded"


def test_s2519_default_lease_is_short(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "schedules3.sqlite3") as schedules:
        schedules.add("demo", config, 60, start_at=100)
        schedules.claim_due(now=100)
        # 默认租约 300s：死进程在 ~5 分钟内可回收，而非 1 小时
        assert schedules.claim_due(now=101) == []
        assert schedules.claim_due(now=100 + 300) != []


def test_final_r1_finish_persists_across_connections(tmp_path: Path) -> None:
    """FINAL-R1 回归：finish/defer 必须显式事务提交。

    run-due 为一次性进程——裸 UPDATE 在进程退出（连接关闭）时被回滚，
    next_run_at 不推进、last_status 丢失，任务在租约过期后反复重复执行。
    用"新连接读回"模拟下一进程视角，断言状态已持久化。
    """
    import time as _time

    from omnicrawler.runtime.scheduler import ScheduleStore

    db = tmp_path / "schedules.sqlite3"
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: t, workspace: work}\n", encoding="utf-8")

    store = ScheduleStore(db)
    schedule_id = store.add("job", config, interval_seconds=60)
    claimed = store.claim_due()
    assert len(claimed) == 1
    store.finish(schedule_id, ok=True)
    store.close()

    # 新连接 = 模拟下一次 run-due 进程
    fresh = ScheduleStore(db)
    try:
        rows = fresh.list()
        assert len(rows) == 1
        row = rows[0]
        assert row["last_status"] == "succeeded"
        assert row["lease_until"] is None
        assert row["next_run_at"] > _time.time() + 30  # 已按 interval 推进
        # 已完成且未到期 → 不应再被认领
        assert fresh.claim_due() == []
    finally:
        fresh.close()
