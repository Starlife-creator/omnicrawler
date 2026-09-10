"""插件信任询问（trust prompter）契约：询问结果、询问器类型与进程级默认值。

由 ``plugins.py`` 抽出的**叶子模块**（P1-3 拆分）：不依赖包内其它模块，
因此 ``plugins.py`` 可安全地反向再导出它，不会形成循环导入。

``plugins.py`` 以同名再导出保持既有导入路径 ``omnicrawler.plugins.plugins.X`` 可用：
``TrustPromptResult`` / ``TrustPrompter`` / ``set_default_trust_prompter`` /
``get_default_trust_prompter``。

注意：进程级默认询问器 ``_default_trust_prompter`` 的**唯一持有者是本模块**。
再导出的是函数对象本身，故 CLI（``cli/_handlers``）与 GUI（``gui/main``）设置的值
对 ``pipeline/registry`` 读取到的仍是同一份状态。
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum


class TrustPromptResult(Enum):
    """信任询问结果：信任并加入列表 / 仅本次加载 / 拒绝。"""

    TRUST_AND_LOAD = "trust_and_load"
    LOAD_ONCE = "load_once"
    REJECT = "reject"


# 信任询问器：参数 (plugin_id, author_username, fingerprint)，返回决策；
# 返回 None 表示调用方无交互能力（按拒绝处理）。
TrustPrompter = Callable[[str, str, str], TrustPromptResult | None]

_default_trust_prompter: TrustPrompter | None = None


def set_default_trust_prompter(prompter: TrustPrompter | None) -> None:
    """设置进程级默认信任询问器（CLI/GUI 入口调用一次）。

    未设置时为 None：strict 策略下未信任作者的本地插件直接拒载
    （后台任务、worker、无人值守场景不弹交互）。
    """
    global _default_trust_prompter  # noqa: PLW0603
    _default_trust_prompter = prompter


def get_default_trust_prompter() -> TrustPrompter | None:
    return _default_trust_prompter
