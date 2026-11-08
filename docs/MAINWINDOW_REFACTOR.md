# MainWindow 组合根拆分迁移计划（C-#19）

> 适用版本：1.0.1 · 配置协议：v5 · 维护状态：现行（历史状态见归档；功能验证以验收账本为准）

## 1. 现状基线（2026-11-09 实测）

| 指标 | 实测值 | 门禁基线（`tools/main-window-baseline.json`） |
| --- | ---: | ---: |
| `gui/main.py` 总行数 | 2241 | ≤ 2241 |
| `MainWindow` 类方法数 | 104 | ≤ 104 |
| 最长方法 `_show_workspace_background_settings` 行数 | 72（406–477 行） | ≤ 72 |
| 次长方法 `_on_update_checked` 行数 | 71（681–751 行） | 观察项 |

既有拆分：delegate 体系（`gui/delegates/`，弱引用显式持有）已替代旧 `__getattr__` 魔法转发；
中央区域装配已下沉为 `gui/delegates/central_area.py` 的 `CentralAreaBuilder`（批次 6-4b），主类不再 import 那些 view/widget 类型；
菜单 / 工具栏 / 主题管理器的**纯转发壳已删除**（批次 6-4c）：主类不再有 `_setup_menu_bar` / `_setup_toolbar` / `_apply_ui_mode` / `_set_theme`，全部调用点直连 `self._menu_builder` / `self._toolbar_manager` / `self._theme_manager`。
窗口外壳装配（状态栏 / 系统托盘 / 帮助中心停靠 / 工作台背景还原）已下沉为 `gui/delegates/chrome.py` 的 `WindowChromeBuilder`（批次 6-4d），主类不再 import `HelpCenterDock` / `StatusIndicator`；
`__init__` 已按构造顺序分五段（批次 6-4d），从 134 行降到 9 行，最长方法变成 `_show_workspace_background_settings` 的 72 行。
仍保留的两个同名方法是**有意保留**：`_show_error_dialog` 被 `main.py` 末尾的全局异常钩子以 `hasattr(window, '_show_error_dialog')` **动态**探测（静态引用扫不到，删掉会静默失去错误弹窗）；`_on_workspace_changed` 不是转发壳（内含 300ms 去抖定时器与 `_updating_editor` 回声抑制，§A-11 修复），且被 `delegates/central_area.py` 直接接线、被测试直接调用。

## 2. 拆分目标

1. ✅ **装配职责下沉**（批次 6-4b 已完成）：`_setup_central_area` 的 227 行面板装配迁入独立面板装配 delegate（参照 `delegates/` 既有弱引用模式），主类只保留组合顺序与跨面板协调。
2. ✅ **删除纯转发壳**（批次 6-4c 已完成）：菜单 / 工具栏 / 主题访问改为调用点直连 `self._theme_manager` 等成员，4 个转发方法已删除；按 §3 第 4 条先单独提交调用点直连（`00e40678`）再删壳，保留一个可回退的分界。
3. ✅ **构造分段**（批次 6-4d 已完成）：`__init__` 拆为 `_init_window_frame` / `_init_core_state` / `_init_project_context` / `_init_runtime_state` / `_init_ui_and_signals` 五个私有装配段，`__init__` 只剩 9 行；段间不共享可变临时状态（原有的三个局部变量都只在项目根目录段内使用）。
4. 拆分后用 `tools/check_main_window_size.py --write-baseline` 按实测下调基线，维持只降不升。

## 3. 迁移顺序与兼容保护

1. **先补测试网**：在 Qt offscreen 环境为现有主窗口装配路径补契约测试（启动 → 中央区域可见 → 菜单动作可触发 → 关闭生命周期完整），作为拆分回归底线；测试网就位前不做物理拆分。
2. 按「面板装配 delegate → 转发壳清理 → 构造分段」顺序迁移，每个阶段独立提交、独立回归；禁止在一个提交内同时移动装配代码与改信号连接。
3. delegate 之间通过主类协调信号，不互相持有对方控件引用；新引入的控件访问必须经 delegate 公开方法，不在主类内直接读写子部件内部状态。
4. 转发壳删除前先完成全部调用点直连改造，保留一个提交作为删除点，便于回退定位。

## 4. 验收口径

- `MainWindow` 方法数、`main.py` 总行数、最长方法行数全部低于本计划基线，门禁基线随实测下调；
- 菜单 / 工具栏 / 主题不再存在主类纯转发方法；
- 既有 offscreen 契约测试全绿，无新增 widget 生命周期泄漏（参照 `gui/core/background_worker.py` 与 delegates 弱引用约定）。

## 5. 当前状态与环境依赖

- 已落地：规模 ratchet 门禁（`tools/check_main_window_size.py`）、本计划文档，以及 §3 第 1 条要求的 **offscreen 契约测试网** `tests/gui/test_main_window_contract.py`（启动 → 导航/页面栈结构 → 每个菜单动作已接线 → 导航切换每页 → 关闭生命周期完整；含两条反向断言锁住判据本身）。该测试已接入 `.github/workflows/quality.yml` 的 `gui-and-browser` 腿。
- 契约测试只断言窗口**最终长成的结构**，不断言由哪个方法拼出来——因此后续把装配搬进 delegate、或把 `__init__` 分段时它仍须全绿；反之搬迁漏接信号、少挂页面、导航映射错位会立刻变红。
- 已完成 §2 第 1 条（批次 6-4b）：226 行面板装配整块平移进 `gui/delegates/central_area.py` 的 `CentralAreaBuilder.setup()`，`self.` 全量改写为 `mw.`，**未改任何 connect 目标、未改装配顺序**；主类只保留 `self._central_area.setup()` 一次调用，并不再 import 那些 view/widget 类型（改为类级 `Any` 注解，注释写明「主窗口只按名字持有、不碰类型细节」）。
- 该搬迁让 `MainWindow` 的规模实测降到 2318 行 / 109 方法 / 最长方法 134 行（`__init__`），基线已按 `--write-baseline` 下调到逐项相等。环预算随之按实测上调 39/61/12 → 40/63/13：新 delegate 必然落进既有 GUI 大环（`delegates → central_area → _base →(TYPE_CHECKING) main → delegates`），增量可逐条归因（modules +1 / edges +2 / largest +1），与 `e3e41bf7` 同一模式。
- 已完成 §2 第 2 条（批次 6-4c）：先提交调用点直连（`00e40678`，5 处测试改直连 `_theme_manager`），再单独提交删除 4 个纯转发方法。规模实测进一步降到 2297 行 / 105 方法，ratchet 基线按实测下调到逐项相等。
- 已完成 §2 第 3 条（批次 6-4d）：先把窗口外壳装配下沉为 `gui/delegates/chrome.py` 的 `WindowChromeBuilder`（状态栏 / 系统托盘 / 帮助中心停靠 / 工作台背景还原，共 6 个方法约 96 行），再把 `__init__` 按原有 `# ---- ... ----` 注释边界切成五段。**为什么必须同批内先下沉**：ratchet 逐项顶格零余量，而分段本身要 +4 个方法、+37 行，先拆段必然被门禁拒绝（首次尝试实测 2334 行 / 110 方法，已回滚）；先腾空间才可能让三项指标同时下降。
- 该批实测规模：`main.py` 2297 → 2241 行、`MainWindow` 105 → 104 方法、最长方法 134 → 72 行（`__init__` 仅 9 行），ratchet 基线已按实测下调到逐项相等。环预算再次按实测上调 40/63/13 → 41/65/14，归因同上（新增一个 delegate，modules +1 / edges +2 / largest +1）。
- 至此 §2 四条目标全部完成。拆分期间 `main.py` 只允许缩减、不得增长（门禁阻断新增膨胀），后续任何改动完成后用 `--write-baseline` 按实测下调。
- 历史迁移教训与 delegate 边界见 `gui/delegates/` 目录注释与 `docs/CODING_STANDARDS.md`。
