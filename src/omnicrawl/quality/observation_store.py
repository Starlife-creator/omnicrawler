"""P3-3：L2 观察期 + L3 稳定演化的工程化持久化层。

**现存问题（为什么必须做）**：
    `RepairCandidate.observation_rounds` 目前只存在于 frozen dataclass 的内存对象里：
        1. pipeline process 重启 → observation_rounds 归零 → L3（连续 3 轮稳定）永远达不到；
        2. `classify_tier` 为了判 L3 必须用 candidate.observation_rounds，但这个数字是构造时
           临时塞进去的，用户方没有可靠机制让它跨 run 自增；
        3. `should_rollback_on_quality_drop(applied, cur, base)` 是纯函数，只告诉你「该滚吗？」，
           但没配套记录 applied 过的 baseline、没做"连续 N 次回归才真正执行 revert"的
           阻尼防护（单次 shadow_compare 抖动会误回滚）。

**本模块修复上述所有问题**：
    - `ObservationStore(db_path)`：SQLite 持久化表 `obs_candidates`（candidate_id PK），
      保存 observation_rounds、EMA false_positive_risk、applied 次数、
      最近一次 applied 时的 baseline_quality、rollback_config_sha256（回滚索引）。
    - `promote_for_candidate(candidate) -> RepairCandidate`：用持久化值替换 dataclass
      里 observation_rounds/fp_risk 两个字段，供 classify_tier 使用（process 重启也有记忆）。
    - `record_application`：L2/L3 自动应用成功后，写入「应用记录」(baseline_quality + rollback_sha)。
    - `record_observation(comparison)`：新一轮 shadow_compare 后调用。若 candidate 之前被
      L2/L3 应用过：
          * 新比较 improves_safely=True → observation_rounds++（L2→L3 进化路径）
          * improves_safely=False → regression_count++；达到 `l2_regression_threshold`
            （默认连续 2 次）→ 标记 rollback_required=True，调用方拿到 `RollbackDirective`
            执行 revert（L2 质量兜底回滚，回到 active 应用前的 baseline config）。
    - EMA false_positive_risk：新比较 false_matches>0 时，fp_risk = α*risk_now + (1-α)*old（α=0.4），
      逐渐遗忘历史，不被早期一次误判卡死。

**与 L3 判据的对应关系**（与 classify_tier 代码完全对齐，不另起炉灶）：
    L3 升级条件 = observation_rounds >= l3_required_observation_rounds AND fp_risk <= 0.1。
    由 ObservationStore 记忆这两个数，classify_tier 在每次调用前先 `promote_for_candidate`
    把持久化值写入 candidate dataclass，之后 L3 判定 100% 复用原有逻辑。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .shadow_repair import RepairCandidate, ShadowComparison

LOGGER = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS obs_candidates (
    candidate_id            TEXT PRIMARY KEY,
    field                   TEXT NOT NULL,
    rule_type               TEXT NOT NULL,
    observation_rounds      INTEGER NOT NULL DEFAULT 0,
    shadow_pass_count       INTEGER NOT NULL DEFAULT 0,
    shadow_fail_count       INTEGER NOT NULL DEFAULT 0,
    regression_count        INTEGER NOT NULL DEFAULT 0,
    false_positive_risk     REAL    NOT NULL DEFAULT 0.0,
    fp_risk_samples         INTEGER NOT NULL DEFAULT 0,
    applied_count           INTEGER NOT NULL DEFAULT 0,
    last_applied_tier       INTEGER,          -- 0..3 (AutomationTier value)
    last_baseline_quality   REAL,             -- applied 那一刻的 baseline_quality，回滚判定用
    last_rollback_sha       TEXT,             -- config._repair.rollback_config_sha256
    last_compare_json       TEXT,             -- 最近一轮 ShadowComparison 调试用 JSON
    first_seen_ts           REAL    NOT NULL,
    last_seen_ts            REAL    NOT NULL,
    rollback_required       INTEGER NOT NULL DEFAULT 0
);
"""

# EMA 新观测占比（new observation 权重 0.4，历史记忆 0.6）
_EMA_ALPHA = 0.4


@dataclass(slots=True, frozen=True)
class RollbackDirective:
    """ObservationStore 建议调用方回滚的指令（零副作用，只返回建议）。

    调用方拿到后，自己负责把 active config 替换为 rollback_sha 对应的快照版本。
    调用方执行回滚后，必须调用 `mark_rolled_back(candidate_id)` 清 regress_count 与 flag，
    防止重复回滚提示。
    """

    candidate_id: str
    reason: str
    regression_count: int
    threshold: int
    rollback_config_sha256: str | None
    last_applied_tier: int | None
    baseline_quality: float | None
    latest_quality: float | None


class ObservationStore:
    """L2 观察期 + L3 稳定演化的 SQLite 持久化与状态机。

    线程/进程安全级别：SQLite 写串行化；典型用法是 process 级单例（state/state_store 同风格），
    不建议多进程同文件并发写（SQLite 会 busy_timeout，这里设置 5 秒兜底）。
    """

    def __init__(self, db_path: Path | str) -> None:
        path = db_path if isinstance(db_path, Path) else Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._conn = sqlite3.connect(str(path), timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── Context manager + lifecycle ───────────────────────
    def __enter__(self) -> ObservationStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── Promote：把持久化值写回 RepairCandidate dataclass ─────
    def promote_for_candidate(self, candidate: RepairCandidate) -> RepairCandidate:
        """用 SQLite 持久化的 observation_rounds / false_positive_risk 替换 dataclass 默认值。

        classiy_tier 在进入决策前必须先调用本函数，保证跨 run 记忆。
        若 DB 内无该 candidate 记录（首次出现），则原样返回，同时写入 first_seen 基线行。
        """
        now = time.time()
        with self._tx() as con:
            row = con.execute(
                "SELECT observation_rounds, false_positive_risk FROM obs_candidates WHERE candidate_id=?",
                (candidate.candidate_id,),
            ).fetchone()
            if row is None:
                con.execute(
                    """INSERT INTO obs_candidates(
                        candidate_id, field, rule_type,
                        observation_rounds, shadow_pass_count, shadow_fail_count,
                        regression_count, false_positive_risk, fp_risk_samples,
                        applied_count, first_seen_ts, last_seen_ts, rollback_required
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (
                        candidate.candidate_id,
                        candidate.field,
                        candidate.rule_type,
                        candidate.observation_rounds,
                        0,
                        0,
                        0,
                        candidate.false_positive_risk,
                        1,
                        0,
                        now,
                        now,
                    ),
                )
                return candidate
            obs_rounds = int(row["observation_rounds"])
            fp_risk = float(row["false_positive_risk"])
            # L3 判定只看 observation_rounds + false_positive_risk，替换这两个即可
            return _replace_candidate(
                candidate,
                observation_rounds=max(candidate.observation_rounds, obs_rounds),
                false_positive_risk=round(fp_risk, 4),
            )

    # ── 记录 L2/L3 应用 ──────────────────────────────────
    def record_application(
        self,
        candidate: RepairCandidate,
        *,
        tier_value: int,
        baseline_quality: float | None,
        rollback_config_sha256: str | None,
    ) -> None:
        """L2/L3 自动应用后，写入应用审计 + baseline（用于后续回滚判定）。

        调用人：auto_apply_if_safe 返回 AutoApplyResult 之后，立刻调用一次。
        一次 application 会：applied_count += 1, regression_count 归零（新应用进入新观察期）。
        """
        now = time.time()
        with self._tx() as con:
            # 确保该行存在（promote 可能没被调过，但幂等安全）
            self._ensure_row(con, candidate, now)
            con.execute(
                """UPDATE obs_candidates SET
                    applied_count = applied_count + 1,
                    last_applied_tier = ?,
                    last_baseline_quality = ?,
                    last_rollback_sha = ?,
                    regression_count = 0,
                    rollback_required = 0,
                    last_seen_ts = ?
                WHERE candidate_id = ?""",
                (
                    int(tier_value),
                    None if baseline_quality is None else float(baseline_quality),
                    rollback_config_sha256,
                    now,
                    candidate.candidate_id,
                ),
            )

    # ── 记录新一轮观察 + 触发 L2 回滚判据 ────────────────
    def record_observation(
        self,
        candidate: RepairCandidate,
        comparison: ShadowComparison,
        *,
        l2_regression_threshold: int = 2,
    ) -> RollbackDirective | None:
        """新一轮 shadow_compare 完成后，更新计数。

        行为：
          1. improves_safely AND 该 candidate 有 applied 过（applied_count>0）→ observation_rounds += 1
          2. improves_safely=False 且 applied_count>0 → regression_count += 1
             * 连续达到 l2_regression_threshold → 返回 RollbackDirective 建议回滚
          3. 无论是否 applied：更新 EMA false_positive_risk（false_matches>0 时更新）

        Returns:
            RollbackDirective 表示达到阈值，建议调用方回滚；否则 None。
        """
        now = time.time()
        compare_json = json.dumps(
            {
                "old_records": comparison.old_records,
                "new_records": comparison.new_records,
                "old_quality": comparison.old_quality,
                "new_quality": comparison.new_quality,
                "false_matches": comparison.false_matches,
                "historical_compatible": comparison.historical_compatible,
                "improves_safely": comparison.improves_safely,
            },
            ensure_ascii=False,
        )
        with self._tx() as con:
            self._ensure_row(con, candidate, now)
            row = con.execute(
                """SELECT applied_count, observation_rounds, regression_count,
                          false_positive_risk, fp_risk_samples, last_applied_tier,
                          last_baseline_quality, last_rollback_sha, rollback_required
                   FROM obs_candidates WHERE candidate_id = ?""",
                (candidate.candidate_id,),
            ).fetchone()
            assert row is not None
            applied_count = int(row["applied_count"])
            obs_rounds = int(row["observation_rounds"])
            regression_count = int(row["regression_count"])
            old_fp = float(row["false_positive_risk"])
            fp_samples = int(row["fp_risk_samples"])
            last_baseline = row["last_baseline_quality"]
            last_rollback_sha = row["last_rollback_sha"]
            last_tier = row["last_applied_tier"]
            already_flagged = bool(int(row["rollback_required"]))

            # ① 更新 observation_rounds（仅 applied 的候选算「稳定进化」round）
            new_obs_rounds = obs_rounds
            new_regression = regression_count
            directive: RollbackDirective | None = None
            if applied_count > 0:
                if comparison.improves_safely:
                    new_obs_rounds = obs_rounds + 1
                    # improves_safely 时，regression 计数清零（一次好结果抵消累积坏印象）
                    new_regression = 0
                else:
                    new_regression = regression_count + 1
                    if new_regression >= l2_regression_threshold and not already_flagged:
                        # 达到阈值 → 发出回滚指令，同时 set rollback_required=1
                        directive = RollbackDirective(
                            candidate_id=candidate.candidate_id,
                            reason=(
                                f"连续 {new_regression} 次 shadow_compare 未通过安全改善检查"
                                f"（阈值 {l2_regression_threshold}），建议回滚 L{last_tier} 应用"
                            ),
                            regression_count=new_regression,
                            threshold=l2_regression_threshold,
                            rollback_config_sha256=last_rollback_sha,
                            last_applied_tier=None if last_tier is None else int(last_tier),
                            baseline_quality=None if last_baseline is None else float(last_baseline),
                            latest_quality=float(comparison.new_quality),
                        )

            # ② EMA false_positive_risk：新 false_matches>0 时给个瞬时高风险，之后慢慢衰减
            if comparison.false_matches > 0:
                # 瞬时风险 = min(1.0, false_matches / max(1, comparison.new_records))
                n = max(1, int(comparison.new_records))
                instant_risk = min(1.0, float(comparison.false_matches) / float(n))
                updated_fp = _EMA_ALPHA * instant_risk + (1.0 - _EMA_ALPHA) * old_fp
                fp_samples += 1
            else:
                # improves_safely=True → 小幅度"给信任"，fp_risk 缓慢下降
                if comparison.improves_safely:
                    updated_fp = (1.0 - _EMA_ALPHA) * old_fp
                    fp_samples += 1
                else:
                    updated_fp = old_fp
            updated_fp = round(updated_fp, 6)

            con.execute(
                """UPDATE obs_candidates SET
                    observation_rounds = ?,
                    shadow_pass_count = shadow_pass_count + ?,
                    shadow_fail_count = shadow_fail_count + ?,
                    regression_count = ?,
                    false_positive_risk = ?,
                    fp_risk_samples = ?,
                    last_compare_json = ?,
                    rollback_required = ?,
                    last_seen_ts = ?
                WHERE candidate_id = ?""",
                (
                    int(new_obs_rounds),
                    1 if comparison.improves_safely else 0,
                    0 if comparison.improves_safely else 1,
                    int(new_regression),
                    updated_fp,
                    fp_samples,
                    compare_json,
                    1 if directive is not None else int(row["rollback_required"]),
                    now,
                    candidate.candidate_id,
                ),
            )
            return directive

    # ── 回滚后清理状态 ──────────────────────────────────
    def mark_rolled_back(self, candidate_id: str) -> None:
        """调用方完成 RollbackDirective 对应 revert 后调用。

        作用：rollback_required=0，regression_count=0（允许将来重新观察再升级）。
        不清除 observation_rounds（过去的稳定经验仍然有价值）。
        """
        with self._tx() as con:
            con.execute(
                "UPDATE obs_candidates SET rollback_required=0, regression_count=0 WHERE candidate_id=?",
                (candidate_id,),
            )

    # ── 快照诊断 ────────────────────────────────────────
    def snapshot(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        """读取当前 DB 状态（用于 metrics / 调试）。"""
        with self._tx() as con:
            if candidate_id:
                rows = con.execute(
                    "SELECT * FROM obs_candidates WHERE candidate_id=?", (candidate_id,)
                ).fetchall()
            else:
                rows = con.execute("SELECT * FROM obs_candidates ORDER BY last_seen_ts DESC").fetchall()
            return [dict(r) for r in rows]

    # ── 内部 ────────────────────────────────────────────
    @staticmethod
    def _ensure_row(con: sqlite3.Connection, candidate: RepairCandidate, now: float) -> None:
        con.execute(
            """INSERT OR IGNORE INTO obs_candidates(
                candidate_id, field, rule_type,
                observation_rounds, shadow_pass_count, shadow_fail_count,
                regression_count, false_positive_risk, fp_risk_samples,
                applied_count, first_seen_ts, last_seen_ts, rollback_required
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                candidate.candidate_id,
                candidate.field,
                candidate.rule_type,
                candidate.observation_rounds,
                0,
                0,
                0,
                candidate.false_positive_risk,
                1,
                0,
                now,
                now,
            ),
        )


# ── 工具：frozen RepairCandidate 只改两个字段 ──────────
def _replace_candidate(
    candidate: RepairCandidate,
    *,
    observation_rounds: int,
    false_positive_risk: float,
) -> RepairCandidate:
    """绕过 dataclass.replace（在某些历史版本对 slots=True 可能走 init=False 路径报错）
    这里直接用 __class__ 构造（frozen=True 的 dataclass 构造是唯一合法的「新对象生成」方式）。
    """
    return RepairCandidate(
        candidate_id=candidate.candidate_id,
        field=candidate.field,
        rule_type=candidate.rule_type,  # type: ignore[arg-type]
        old_rule=candidate.old_rule,
        new_rule=candidate.new_rule,
        confidence=candidate.confidence,
        supporting_samples=candidate.supporting_samples,
        counterexamples=candidate.counterexamples,
        expected_recovery=candidate.expected_recovery,
        false_positive_risk=false_positive_risk,
        observation_rounds=observation_rounds,
    )
