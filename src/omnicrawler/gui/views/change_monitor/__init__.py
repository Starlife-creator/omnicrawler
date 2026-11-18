"""变更监控视图包 — 保持 omnicrawler.gui.views.change_monitor 导入契约稳定。"""

from __future__ import annotations

from ._dialogs import (
    ChangeEventDialog as ChangeEventDialog,
)
from ._dialogs import (
    NewRuleDialog as NewRuleDialog,
)
from ._dialogs import (
    _escape_html as _escape_html,
)
from ._view import ChangeMonitorView as ChangeMonitorView
from ._workers import (
    CONDITION_OPTIONS as CONDITION_OPTIONS,
)
from ._workers import (
    INTERVAL_OPTIONS as INTERVAL_OPTIONS,
)
from ._workers import (
    _CheckWorker as _CheckWorker,
)
from ._workers import (
    _ProbeWorker as _ProbeWorker,
)
