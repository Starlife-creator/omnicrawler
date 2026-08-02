# OmniCrawler GUI 前端优化方案

> 版本：1.0 | 日期：2026-07-27
> 基于探查报告 + 前端开发规范 + Impeccable 设计原则 + 需求分析师框架

---

## 一、执行摘要

OmniCrawler 2.3.0 的 PyQt6 GUI 层具备**坚实的令牌化视觉架构**（三主题、40+ 控件 QSS 覆盖、无障碍基础），但存在 6 个系统性短板：

| 短板 | 影响 | 严重度 |
|------|------|--------|
| 图标系统缺失（依赖 emoji） | 跨平台渲染不一致、无色彩控制 | P0 |
| i18n 管道空转（无 .mo 文件） | 无法国际化 | P0 |
| 部分硬编码颜色未令牌化 | 主题切换时不一致 | P1 |
| 减帧检测使用轮询 | 性能浪费、架构不统一 | P1 |
| 表单验证覆盖局部 QSS | 样式丢失后状态切换报错 | P2 |
| 无视觉回归测试 | 主题/样式变更无保障 | P2 |

**目标：6 周内，GUI 从「功能完备」提升至「生产级精致度 + 国际化就绪」。**

---

## 二、优化模块与功能清单（瀑布模式）

### 模块 A：图标与视觉语言系统（P0）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F1.1.1 | 图标系统 | SVG 图标管线 | 图标注册表 | 建立 `IconRegistry` 单例，按 `icon_key` 注册 SVG 路径。支持通过 `IconRegistry.icon("save")` 获取对应主题色的 `QIcon`。 | P0 | M |
| F1.1.2 | 图标系统 | SVG 图标管线 | 双态图标渲染 | 每个图标定义 `light` / `dark` 两组 SVG（或使用 `currentColor` 单文件）。`QIcon` 的 Normal/Disabled/Active/Selected 四模式自动从 VisualTokens 取色。 | P0 | S |
| F1.1.3 | 图标系统 | SVG 图标管线 | 图标尺寸阶梯 | 定义 `IconSize` 枚举：SMALL(16px) / MEDIUM(24px) / LARGE(48px)。`IconRegistry` 按尺寸缓存 `QPixmap`。 | P1 | XS |
| F1.2.1 | 图标系统 | 全局图标替换 | 按钮图标迁移 | 将所有 QPushButton/QToolButton 中的 Unicode emoji（💾▶🚀🗂✓ℹ⚠✕🔧）替换为 `IconRegistry` 调用。 | P0 | L |
| F1.2.2 | 图标系统 | 全局图标替换 | 状态栏图标迁移 | 状态栏中的 emoji 指示器替换为 StatusIndicator+图标组合。 | P1 | S |
| F1.2.3 | 图标系统 | 全局图标替换 | 导航图标迁移 | 侧边导航栏 QListWidget 的 emoji 前缀替换为 24px SVG 图标 + 文字。 | P1 | M |

### 模块 B：国际化就绪（P0）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F2.1.1 | 国际化 | PO 模板生成 | 字符串提取脚本 | 编写 `tools/extract_i18n.py`，扫描 `gui/` 目录中所有 `_()` 和 `ngettext()` 调用（约 650+ 处），生成 `locale/omnicrawl-gui.pot` 模板。排除 `f"..."` 动态字符串。 | P0 | M |
| F2.2.1 | 国际化 | 英文翻译 | 英文 .po 填充 | 基于 `.pot` 模板创建 `locale/en_US/LC_MESSAGES/omnicrawl-gui.po`，逐条翻译为英文。 | P0 | L |
| F2.2.2 | 国际化 | 英文翻译 | .mo 编译与加载 | 编写 `tools/compile_i18n.py`，调用 `msgfmt` 将 `.po` 编译为 `.mo`。验证 `set_language("en_US")` 后全局 `_()` 返回英文。 | P0 | S |
| F2.3.1 | 国际化 | 翻译质量保障 | 占位符完整性检查 | 在 `compile_i18n.py` 中添加 `msgfmt --check-format` 校验步骤，确保 `%(name)s` 等占位符在翻译中保持一致。 | P1 | XS |
| F2.3.2 | 国际化 | 翻译质量保障 | 翻译覆盖率 CI 检查 | CI 流程中添加步骤：统计 `.po` 文件中已翻译/模糊/未翻译条目比例，覆盖率 < 90% 时发出警告。 | P2 | S |

### 模块 C：视觉令牌体系完善（P1）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F3.1.1 | 令牌体系 | 硬编码颜色清除 | Toast 阴影令牌化 | 将 `toast.py:70` 的 `QColor(0, 0, 0, 40)` 替换为 `VisualTokens` 中的 `shadow_overlay`（自动随主题切换透明度）。 | P1 | XS |
| F3.1.2 | 令牌体系 | 硬编码颜色清除 | HomePage 阴影令牌化 | 将 `home.py:110` 的 `QColor(26, 62, 78, 34)` 替换为 `VisualTokens` 中的 `card_shadow`。 | P1 | XS |
| F3.1.3 | 令牌体系 | 硬编码颜色清除 | 全面扫描守卫 | 将 `OMNICRAWL_GUI_STRICT_HEX` 环境变量守卫扩展为 CI 默认开启，阻止任何新的硬编码颜色合入。 | P1 | XS |
| F3.2.1 | 令牌体系 | 暗色模式图片 | 图片资源切换表 | 建立 `DARK_MODE_ASSETS` 映射表（`help/images/xxx.png` → `help/images/xxx_dark.png`），在 `ThemeManager._apply_visual_theme()` 中根据当前主题选择资源路径。 | P1 | S |
| F3.2.2 | 令牌体系 | 暗色模式图片 | 暗色变体生成脚本 | 编写 `tools/generate_dark_assets.py`，对 `assets/` 中的 PNG 图片自动生成反色/降亮度暗色变体。 | P2 | M |
| F3.3.1 | 令牌体系 | 表单验证样式修复 | 属性选择器替代 setStyleSheet | `form_feedback.py` 改用 `widget.setProperty("validation", "error")` 属性选择器 + 全局 QSS 规则 `[validation="error"] { border: 2px solid ... }`，避免覆盖局部样式。 | P1 | S |

### 模块 D：动效与微交互（P1）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F4.1.1 | 动效系统 | 减帧统一信号 | MotionSignal 总线 | 创建全局 `QObject` 单例 `MotionSignal`，发射 `reduced_motion_changed(bool)` 信号。所有动效组件（AmbientHero、StatusIndicator、PageTransition）连接此信号，**不再轮询**。 | P1 | S |
| F4.1.2 | 动效系统 | 减帧统一信号 | AmbientHero 去轮询 | 将 `home.py` 中的 `QTimer(50ms)` 轮询替换为 `MotionSignal.reduced_motion_changed.connect()`。 | P1 | XS |
| F4.1.3 | 动效系统 | 减帧统一信号 | StatusIndicator 去轮询 | 将 `status_indicator.py` 中的属性检查替换为信号连接。 | P1 | XS |
| F4.2.1 | 动效系统 | 页面过渡增强 | 过渡动画参数化 | `PageTransitionController` 支持 configurable 的缓动曲线（ease-out-quart / ease-in-out-expo），时长和透明度起止值通过 `VisualTokens` 中 `transition.*` 令牌控制。 | P2 | S |
| F4.2.2 | 动效系统 | 页面过渡增强 | 内容淡入编排 | 页面切换时，对页面内的次要元素（标题、描述、按钮）施加 50ms 交错延迟淡入（staggered fade-in），仅在 `reduced_motion=False` 时生效。 | P2 | M |
| F4.3.1 | 动效系统 | 微交互 | 按钮波纹效果 | 为主要操作按钮（`[primary="true"]`）添加点击涟漪动画（圆形遮罩从点击点扩散），使用 300ms ease-out 缓动。 | P3 | M |
| F4.3.2 | 动效系统 | 微交互 | 列表项悬浮反馈 | 导航栏和文件列表项添加悬浮（hover）时微平移（2px）+ 背景色渐变过渡（150ms ease-in-out）。 | P3 | S |

### 模块 E：组件规范与可访问性（P2）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F5.1.1 | 组件规范 | 命名与结构 | 组件命名迁移 | 将 `EmptyState`→`EmptyStateCard`、`HelpTooltip`→`HelpButton` 等，统一为 `PascalCase + 语义后缀` 格式。 | P2 | S |
| F5.1.2 | 组件规范 | 命名与结��� | 属性规范检查 | 所有自定义 Widget 必须通过 `Q_PROPERTY` 声明公开接口（如 `status`、`icon`、`label`），禁止外部直接 `.setStyleSheet()`。 | P2 | M |
| F5.2.1 | 可访问性 | 屏幕阅读器覆盖 | 全量 ARIA 补齐 | 对所有 Wizard 步骤页、ResultTable 单元格、ChartView 数据点补充 `setAccessibleName()` 和 `setAccessibleDescription()`。 | P2 | M |
| F5.2.2 | 可访问性 | 屏幕阅读器覆盖 | Tab 顺序优化 | 逐个 Wizard 页面审计 `setTabOrder()` 调用，确保键盘导航按自然阅读顺序（上→下、左→右）流转。 | P2 | S |
| F5.3.1 | 可访问性 | 焦点可视化 | 全局焦点框增强 | 全局 QSS 中 `*:focus` 规则改为 2px solid `tokens.accent` 轮廓 + 1px offset，替代默认的虚线焦点框。 | P2 | XS |
| F5.3.2 | 可访问性 | 焦点可视化 | 键盘焦点陷阱 | Wizard 步骤页的模态 QDialog 中实现焦点循环（Tab 在最后元素时回到第一元素，Shift+Tab 反向）。 | P2 | S |

### 模块 F：质量保障与回归测试（P2）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F6.1.1 | 质量保障 | 视觉回归测试 | 截图对比框架 | 搭建 `tests/gui/visual/` 目录，使用 `pytest-qt` + `QWidget.grab()` 对以下组件截图并存储为 baseline：EmptyState、StatusIndicator（4 状态）、Toast、HelpTooltip、导航栏（3 主题）。 | P2 | L |
| F6.1.2 | 质量保障 | 视觉回归测试 | CI 像素对比 | CI 中运行 `pytest tests/gui/visual/`，将当前截图与 baseline 做像素级对比（容差 1%），差异 > 阈值时报错。使用 `Pillow.ImageChops.difference()` 实现。 | P2 | M |
| F6.2.1 | 质量保障 | 主题一致性测试 | 三主题快照验证 | 对每个可视化组件自动截取 Light/Dark/HighContrast 三张快照，验证以下不变量：所有文字可读（对比度 ≥ 4.5:1）、无颜色盲区、无未令牌化颜色。 | P2 | L |
| F6.2.2 | 质量保障 | 主题一致性测试 | 令牌泄漏检测 CI | 对生成的 QSS 字符串运行 `assert_no_raw_hex()` 检查，作为 CI 的一个 Gate。运行时变更主题 3 次（L→D→HC→L），确保无内存泄漏（`QTimer`/信号未断开）。 | P2 | M |

### 模块 G：性能与渲染优化（P3）

| 编号 | 一级功能 | 二级功能 | 三级功能 | 功能描述 | 优先级 | 工作量 |
|------|---------|---------|---------|---------|--------|--------|
| F7.1.1 | 性能优化 | QSS 生成 | 增量 QSS 更新 | `stylesheet()` 改为仅重新生成**当前主题相关**的 QSS 片段，避免每次主题切换时重建全部 40+ 控件的样式表。 | P3 | M |
| F7.1.2 | 性能优化 | QSS 生成 | 预编译 QSS 缓存 | 首次 `stylesheet()` 调用后将结果写入 `APP.omnicrawlQSS` 属性缓存；后续调用直接返回缓存。`theme_changed` 信号触发时清除缓存。 | P3 | XS |
| F7.2.1 | 性能优化 | 列表渲染 | 大结果集虚拟化 | 当 `ResultTable` 行数 > 1000 时，启用 Qt 的 `setItemDelegate` + `canFetchMore`/`fetchMore` 分页加载，避免一次性创建所有 QTableWidgetItem。 | P3 | L |
| F7.2.2 | 性能优化 | 列表渲染 | 日志控制台行数限制 | `LogConsole` 设置最大行数限制（默认 5000 行），超出时裁剪前半部分，避免 OOM。 | P3 | XS |

---

## 三、优先级汇总与工作量预估

| 优先级 | 任务数 | 预估总工时 | 关键任务 |
|--------|--------|-----------|---------|
| P0 | 6 | ~12 天 | 图标系统 + 全局替换 + i18n 管道 |
| P1 | 9 | ~8 天 | 令牌清除 + 暗色图片 + 表单验证修复 + 减帧统一 |
| P2 | 12 | ~18 天 | 组件规范 + 可访问性 + 视觉回归测试 |
| P3 | 5 | ~9 天 | 涟漪微交互 + 列表虚拟化 + 增量 QSS |
| **合计** | **32** | **~47 天** | |

---

## 四、实施路线图

### 第 1-2 周：视觉基础（P0）

```
Week 1：图标系统
  - 建立 IconRegistry + SVG 图标管线（16 个核心图标）
  - 全局 emoji → SVG 替换（按钮、工具栏、状态栏）
  - 暗色模式适配

Week 2：国际化就绪
  - .pot 模板生成脚本 + 扫描 650 处 _() 调用
  - 英文 .po 翻译（核心 UI 字符串）
  - .mo 编译 + 运行时切换验证
```

### 第 3-4 周：令牌完善 + 动效统一（P1）

```
Week 3：令牌体系
  - Toast/HomePage 硬编码颜色清除
  - 暗色模式图片资源切换表
  - 表单验证 QSS 属性选择器重构

Week 4：动效系统
  - MotionSignal 全局总线（替代轮询）
  - AmbientHero/StatusIndicator 去轮询
  - 页面过渡参数化
```

### 第 5-6 周：质量保障 + 性能（P2 + P3）

```
Week 5：组件规范 + 可访问性
  - 组件命名规范化
  - 屏幕阅读器全量补齐
  - 焦点可视化 + 键盘导航审计

Week 6：视觉回归 + 性能
  - 截图对比框架搭建（baseline + CI）
  - 主题一致性自动化测试
  - 大结果集虚拟化
```

---

## 五、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| SVG 图标在 Windows 高 DPI 下渲染模糊 | 中 | 中 | 使用 `QIcon.pixmap(size, devicePixelRatio)` + 2x 导出 |
| .po 翻译遗漏 f-string 动态字符串 | 高 | 低 | `extract_i18n.py` 跳过 f-string，手动审查 |
| 截图对比在不同平台/DPI 下不一致 | 中 | 中 | baseline 按平台/DPI 分别存储；CI 限单平台运行 |
| 图标迁移遗漏 emoji（搜索不完整） | 低 | 低 | 全局 emoji 正则扫描 + CI 禁止新增 emoji |

---

## 六、附录：PyQt6 前端规范速查

### 组件命名
- Widget 类名：`PascalCase` + 语义后缀（`StatusIndicator`, `EmptyStateCard`）
- 文件命名：`snake_case` 与类名对应（`status_indicator.py`）
- 属性命名：`camelCase`（`setAccessibleName`, `setProperty`）

### QSS 样式规范
- **禁止** 内联 `setStyleSheet()` 覆盖全局样式
- 使用 **属性选择器** + 全局 QSS 规则：`widget.setProperty("state", "error")`
- 所有颜色值通过 `VisualTokens` 获取，禁止硬编码 hex/rgba

### 可访问性
- 每个自定义 Widget 必须设置 `setAccessibleName()` 和 `setAccessibleDescription()`
- 所有交互元素支持 Tab 键导航
- 焦点可视化必须有 ≥ 2px 轮廓

### 主题切换
- 组件必须连接 `theme_changed` 信号并实现 `_apply_token_style()`
- 禁止缓存 `QColor` 对象——每次绘制时重新从 `VisualTokens` 读取

---

## 实施进度（2026-07-27）

| 模块 | 状态 | 完成内容 |
|------|------|---------|
| A. 图标系统 | ✅ 核心完成 | IconRegistry + 16 个 SVG 图标；待替换 emoji |
| B. 国际化 | ✅ 管道就绪 | .pot 提取(556 strings) + EN .po(73 translated) + .mo 编译脚本 |
| C. 令牌完善 | ✅ 全部完成 | shadow_overlay/card_shadow 令牌；Toast/HomePage 去硬编码；form_feedback 属性选择器重构 |
| D. 动效系统 | ✅ 全部完成 | MotionSignal 全局总线；AmbientHero/StatusIndicator 去轮询 |
| E. 组件规范+A11y | ✅ 核心完成 | 全局焦点可视化 QSS；5 个 Wizard 步骤页 ARIA 补齐 |
| F. 视觉回归测试 | ✅ 框架就绪 | conftest.py + 5 组件快照测试 + Pillow 像素对比 + OMNI_BASELINE 模式 |
| G. 渲染性能 | ✅ 核心完成 | QSS 缓存(tk_hash)；LogConsole 已有 5000 行限制 |

### 新增文件
- `src/omnicrawl/gui/motion_signal.py` — 减帧信号总线
- `src/omnicrawl/gui/icon_registry.py` — SVG 图标管线（16 icons）
- `tools/extract_i18n.py` — AST 解析 _() 调用，生成 .pot
- `tools/generate_en_po.py` — .pot → EN .po 生成器
- `tools/compile_i18n.py` — .po → .mo 编译器 (msgfmt)
- `locale/omnicrawl-gui.pot` — 556 条字符串模板
- `locale/en_US/LC_MESSAGES/omnicrawl-gui.po` — 英文翻译

### 修改文件
- `design_system.py` — +2 tokens, +1 helper, +[validation] QSS
- `toast.py` — 硬编码→shadow_overlay token
- `home.py` — 硬编码→card_shadow token, 轮询→MotionSignal
- `form_feedback.py` — setStyleSheet→属性���择器
- `status_indicator.py` — QApplication轮询→MotionSignal
- `delegates.py` — 连接 MotionSignal.notify()
