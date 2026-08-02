"""远程任务调度预留模块。

当前为空壳实现，为未来远程任务调度功能预留接口。
所有方法均抛出 NotImplementedError 以防止静默失败。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.config_model import CrawlConfig


class RemoteRunner:
    """远程任务执行器（预留）。

    未来用于将任务提交到远程调度系统执行。
    当前所有方法均不可用——调用将抛出 NotImplementedError。
    """

    def __init__(self, endpoint: str = "") -> None:
        self._endpoint = endpoint

    def submit(self, config: CrawlConfig) -> str:
        """提交任务到远程调度器。

        Raises:
            NotImplementedError: 远程执行后端尚未发布。
        """
        raise NotImplementedError("远程执行后端尚未发布")

    def status(self, task_id: str) -> str:
        """查询远程任务状态。

        Raises:
            NotImplementedError: 远程执行后端尚未发布。
        """
        raise NotImplementedError("远程执行后端尚未发布")

    def cancel(self, task_id: str) -> bool:
        """取消远程任务。

        Raises:
            NotImplementedError: 远程执行后端尚未发布。
        """
        raise NotImplementedError("远程执行后端尚未发布")
