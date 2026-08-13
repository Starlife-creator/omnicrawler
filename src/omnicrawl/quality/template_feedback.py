"""P2：模板推荐「拒绝理由」诊断快照持久化（PRD §3.2）。

现有 observation_store 的 schema 面向 L2/L3 修复候选（RepairCandidate），
不支持用户反馈类记录 → 按 PRD §8.1 决策④ 采用本地 JSONL 兜底
（`workspace/logs/template_feedback.jsonl`）。GUI 无后台批量导入需求，
JSONL 追加写即最终语料，可供 SiteCategorizer L3 迭代读取。

**埋点约束（PRD §3.2 / §6）**：拒绝理由必须携带诊断快照——
URL 域名、命中分类、置信度、命中来源、推荐模板与字段、用户动作标签；
`record()` 对缺任一必需字段的快照返回 False（无快照不入库）。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# 预设拒绝标签（PRD §3.2 产品数据闭环）
REJECT_LABELS: tuple[str, ...] = ("网址不匹配", "字段太少", "结构过时")

# 入库必需字段：缺失任一 → 视为无快照 → 不入库
_REQUIRED_KEYS: tuple[str, ...] = ("domain", "template_id", "action")

# 默认相对存储路径（相对项目根）
_DEFAULT_REL_PATH = Path("workspace") / "logs" / "template_feedback.jsonl"


@dataclass(frozen=True, slots=True)
class TemplateRejectionSnapshot:
    """一次「👎 不准确」拒绝的完整诊断快照。"""

    url: str = ""
    domain: str = ""          # URL 域名（urlparse netloc）
    category: str = ""        # L1/L2 命中分类摘要（CategorizeResult.reason）
    confidence: float = 0.0   # 推荐置信度（0.30-1.00）
    hit_source: str = ""      # 命中来源（L1/L2/L3/MANUAL）
    template_id: str = ""     # 被拒绝的推荐模板 ID
    template_fields: tuple[str, ...] = ()  # 推荐模板相关字段名
    action: str = ""          # 用户动作标签（如 template_rejection）
    reject_label: str = ""    # 预设中文标签（网址不匹配/字段太少/结构过时）
    field_count: int = 0      # 用户当前字段数（快照上下文）
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["template_fields"] = list(self.template_fields)
        return data


class TemplateFeedbackStore:
    """JSONL 追加写拒绝快照；`record()` 返回是否入库（无快照=False）。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = (
            (Path.cwd() / _DEFAULT_REL_PATH) if path is None else (path if isinstance(path, Path) else Path(path))
        )

    @property
    def path(self) -> Path:
        return self._path

    def record(self, snapshot: TemplateRejectionSnapshot) -> bool:
        """写入一条快照；缺必需字段或写盘失败返回 False（不入库）。"""
        data = snapshot.to_dict()
        if not self._valid(data):
            LOGGER.warning("拒绝理由缺少诊断快照，不入库: %s", data)
            return False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            LOGGER.warning("写入模板反馈快照失败: %s", exc)
            return False

    def iter_records(self) -> Iterator[dict[str, Any]]:
        """顺序读取已入库的快照（L3 迭代语料 / 调试）。"""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

    @staticmethod
    def _valid(data: dict[str, Any]) -> bool:
        return all(bool(data.get(key)) for key in _REQUIRED_KEYS)
