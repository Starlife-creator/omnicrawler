# GUI 设计体系使用指南（开发者）

> 适用对象：新增或修改 GUI 页面/组件的人（含 AI 协作方）。
> 最后更新：2026-09-11 ｜ 对应代码：`gui/design_system.py`、`gui/core/view_base.py`、`gui/widgets/`

## 为什么要读这份

设计体系的**部件早已建成**（令牌与三套主题、`stylesheet()`、动效信号、图标注册表、无障碍模块、
`EmptyState`/`Toast`/`StatusIndicator`/`LogConsole` 等组件），但 2026-09-11 勘察发现
**新页面接不上**——动效与图标注册表 **0 个视图**接入、`EmptyState` 仅 **3/39**、
`setAccessibleName` 仅 **6/39**、9 个文件仍写内联样式。也就是「资产齐全、继承路径缺失」。

现在继承路径有了：**`BaseView` 骨架** + **可执行门禁** `tools/check_gui_conventions.py`
（新文件零容忍，存量只降不升）。本文件是那条路径的说明书。

---

## 一、新页面三步走

```python
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout

from ..core.view_base import BaseView
from ..i18n import _


class MyView(BaseView):
    """我的页面（一句话说明用途）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(accessible_name=_("我的页面"), object_name="myView", parent=parent)
        self._data = None          # 自有状态：放在 super() 之后
        self.finish_setup()        # 触发 build_ui + 局部样式

    def build_ui(self, container) -> None:
        layout = QVBoxLayout(container)
        title = QLabel(_("区块标题"))
        title.setObjectName("sectionTitle")     # 用语义类，不写内联样式
        layout.addWidget(title)

    def reload(self) -> None:
        self.show_loading()                     # 三态由骨架统一切换
        try:
            self._data = load()
        except Exception:
            self.show_error(_("加载失败"), _("请重试"))
            return
        if not self._data:
            self.show_empty(_("暂无数据"), _("导入后即可查看"))
        else:
            self.show_content()

    def save(self) -> None:
        self.notify(_("已保存"), "success")     # 轻提示统一走 Toast
```

三条要点：

1. **`finish_setup()` 必须显式调用**（不要指望 `BaseView.__init__` 自动调 `build_ui`：那会在
   `super()` 期间执行子类代码，此时子类属性还没赋值——经典陷阱）。
2. `build_ui` 里只搭内容；**空/加载/错误三态交给 `show_empty` / `show_loading` / `show_error`**，
   回到正常态用 `show_content()`。三态与内容区互斥，无需自己 `setVisible`。
3. 提示一律 `self.notify(msg, "info"|"success"|"warning"|"error")`。

---

## 二、颜色与排版：只用令牌

| 取法 | 场景 |
|---|---|
| `self.tokens.<字段>` | 继承 `BaseView` 的页面（推荐） |
| `ThemeManager.instance().tokens` | 非页面代码（组件、委托） |
| `stylesheet(tokens)` | 需要生成整段 QSS 时（这是**允许**的写法） |

**禁止**：字面量样式串（`setStyleSheet("color: red")`）与裸十六进制色值。
门禁 B 拦前者、门禁 C 拦后者（令牌真源＝`design_system.py`）。

**语义色令牌（30 个字段，摘要）**

| 分组 | 字段 |
|---|---|
| 表面 | `canvas` 应用底色 / `surface` 卡片面板 / `elevated` 弹层浮起 / `nav` 侧栏表头 |
| 文字 | `text` 主文字 / `muted` 次要文字 |
| 描边 | `border` 默认 / `border_strong` 聚焦强调 |
| 主色 | `primary` / `primary_hover` / `primary_active` / `selection` 选中背景 |
| 状态 | `success`/`success_bg`、`warning`/`warning_bg`、`danger`/`danger_bg`、`info`/`info_bg` |
| 指示器 | `indicator_idle`/`indicator_running`/`indicator_finished`/`indicator_error` |
| 代码块 | `code_bg`/`code_fg`/`code_border` |
| 阴影 | `shadow`/`shadow_overlay`/`card_shadow` |

**刻度**（`design_system` 模块级常量）

| 常量 | 值 |
|---|---|
| `FONT_SIZE` | caption 11 / small 12 / body 14 / label 14 / subtitle 15 / title 18 / heading 22 / display 28 / hero 34 |
| `SPACING` | xs 4 / sm 8 / md 12 / lg 16 / xl 24 / xxl 32 |
| `RADIUS` | xs 4 / sm 6 / md 8 / lg 12 / xl 16 / pill 999 |

**可用语义类（`setObjectName(...)`，QSS 已定义）**：
`sectionTitle`（区块/侧栏标题）、`muted`（弱化文本）、`eyebrow`（小标签）、
`homeTitle`（首页大标题）、`advancedSummary`、`placeholderHint`。

---

## 三、共享组件清单

| 组件 | 位置 | 什么时候用 | 最小用法 |
|---|---|---|---|
| `EmptyState` | `widgets/empty_state.py` | 空数据/未开始/错误占位（**不要自己写 QLabel 提示**） | 经 `BaseView.show_empty()`；直接构造：`EmptyState("📭", "标题", "说明", action_label="去导入", action_callback=fn)` |
| `StatusIndicator` | `widgets/status_indicator.py` | 运行/空闲/成功/失败状态点 | `StatusIndicator(state="running")` |
| `ToastManager` | `widgets/toast.py` | 轻提示（成功/警告/错误） | `ToastManager.instance().success(_("已保存"))`；页面内用 `self.notify(...)` |
| `LogConsole` | `widgets/log_console.py` | 长日志展示（带高亮） | `LogConsole()` + `append_line(text)` |
| `HelpTooltip` | `widgets/help_tooltip.py` | 字段/按钮的上下文帮助 | `HelpTooltip(widget, _("说明"))` |
| `CalendarPopup` | `widgets/calendar_popup.py` | 日期选择 | `CalendarPopup(parent)` |
| `ResourceMonitor` | `widgets/resource_monitor.py` | 资源占用展示 | `ResourceMonitor()` |

---

## 四、无障碍（最低要求）

1. **每个控件类必须有无障碍名**（门禁 A）：页面继承 `BaseView` 并在 `super().__init__(accessible_name=...)` 提供；
   子控件用 `setAccessibleName(...)`。
2. 焦点顺序合理、键盘可达（不要只依赖鼠标事件）。
3. 动效必须尊重 reduced-motion（见 `accessibility.py` / 动效模块）。
4. 对比度用高对比主题（`HIGH_CONTRAST`）自检。

---

## 五、动效与图标（当前接入率最低，新页面应当使用）

- **动效**：用 `motion_signal` 提供的信号，不要自写 `QPropertyAnimation` 循环。
  接入前确认 reduced-motion 下自动降级。
- **图标**：用 `icon_registry.IconRegistry`（便于统一尺寸/配色/缓存），
  **不要**在页面里内嵌 SVG 字符串或写死颜色。

> 这两项的当前接入率是 0；新页面请从第一个页面起就接入，避免又积累一批待迁移代码。

---

## 六、新页面自检清单

提交前逐项确认（前三条由门禁自动把关，后几条需人工）：

- [ ] 控件类都有无障碍名（`BaseView` 已代劳；子控件自查）
- [ ] 没有字面量样式串（要样式就用语义类或 `stylesheet(tokens)`）
- [ ] 没有裸十六进制色值（颜色一律经令牌）
- [ ] 三态用 `show_empty` / `show_loading` / `show_error`，没有自绘提示标签
- [ ] 提示用 `notify()`，没有直接 `QMessageBox` 弹窗（对话框类除外）
- [ ] 文案经 `_()` 国际化
- [ ] 动效接入并尊重 reduced-motion
- [ ] 图标经 `IconRegistry`，未内嵌 SVG
- [ ] 本地跑 `python tools/check_gui_conventions.py` 通过
- [ ] 有对应测试（页面构造 + 关键状态切换；用 `isHidden()` 而非 `isVisible()` 断言）

---

## 七、存量收敛

`tools/gui-conventions-baseline.json` 记录既有违规（只降不升）。改造某个页面后：

```bash
python tools/check_gui_conventions.py                 # 会提示哪些项已下降
python tools/check_gui_conventions.py --write-baseline  # 把下降结果落盘（收紧门槛）
```

**`task_history.py` 是首个按本指南改造的示范页面**：它从"手写 `QLabel` 空态 + 内联标题样式"
改为"继承 `BaseView` + 统一状态区 + `sectionTitle` 语义类"，从豁免清单中移出（两处违规清零）。
新页面照它写即可。
