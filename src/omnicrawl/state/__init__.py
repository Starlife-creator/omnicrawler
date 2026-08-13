"""OmniCrawler 状态存储子包。

通过 StateStore 门面类（在 ``state.py`` 模块中定义）提供统一的数据库访问，
内部委托给领域 Repository 类以分离职责。

领域：
- frontier — 队列管理：入队、认领、完成/失败标记、重试
- run — 运行生命周期：开始、完成、状态转换、崩溃恢复
- records — 记录、语义变更、编辑、质量统计
- export — 导出的幂等提交
- audit — 审计事件
- checkpoint — 阶段检查点
"""

from .capsule_store import Capsule, CapsuleStore
from .state_store import StateStore

__all__ = ["StateStore", "Capsule", "CapsuleStore"]
