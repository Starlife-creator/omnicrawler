# OmniCrawler GUI 工作台 — 快速上手指南

> 适用版本：1.0.1 · 配置协议：v5 · 维护状态：现行

GUI 框架为 **PySide6**（依赖声明以仓库根 `pyproject.toml` 的 `gui` extra 为唯一真源，
本目录不再保留独立 `requirements.txt`）。

## 1. 安装与启动（2 步）

### 步骤 1：在仓库根目录安装项目与 GUI 依赖

```bash
cd /path/to/omnicrawler
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[gui]"
```

`[gui]` 会一并装入 PySide6 及其配套运行时依赖；需要浏览器采集时再追加
`[browser]`（`pip install -e ".[gui,browser]"`），随后运行
`python -m playwright install chromium`。

### 步骤 2：启动 GUI 工作台

```bash
# 方式一：控制台入口脚本
omnicrawler-gui

# 方式二：模块方式启动（等价入口）
python -m omnicrawler.gui
```

## 2. 5 分钟跑通第一个爬虫

1. **启动应用** → 运行上方任一启动命令打开工作台
2. **选择"快速体验"** → 首次启动时会提示是否加载示例配置，选择"是"
3. **点击运行** → 工具栏 ▶ 按钮启动爬虫
4. **查看日志** → 在监控页观察实时日志输出
5. **查看结果** → 任务完成后自动切换到结果页，可浏览 CSV 表格

## 3. 核心功能

| 功能 | 说明 |
| ---- | ---- |
| **任务工作台** | 持续配置：目标 → 方案 → 字段 → 试跑验证 → 输出 |
| **YAML 编辑器** | 高级用户可直接编辑 YAML，支持语法高亮和双向同步 |
| **运行与历史** | 实时日志、进度条、资源占用、历史记录 |
| **结果查看** | 流式 CSV 浏览、分页、Excel 导出 |
| **模板系统** | 内置新闻/电商/公告模板，一键加载 |
| **选择器测试** | 在保存前验证 CSS/XPath/JSONPath 选择器 |
| **智能提取** | 粘贴 HTML 自动推荐 XPath |

## 4. 常见问题快速索引

- **为什么选择器测试返回空？** → 页面可能是动态渲染，尝试切换为"动态浏览器"
- **如何应对动态渲染页面？** → Step 1 选择"动态浏览器"
- **爬虫被网站封 IP 怎么办？** → 增加请求延迟（Step 2），或配置代理
- **结果 CSV 乱码？** → 用 Excel 导入时选择 UTF-8 编码，或用记事本打开
- **如何设置定时任务？** → 走无头命令行入口 `omnicrawler run -c config.yaml`，再配合系统定时任务

## 5. 无头（无 GUI）模式

```bash
# 直接运行配置，不显示工作台
omnicrawler run -c configs/my_config.yaml --log-level DEBUG
```

更多帮助请查看 `docs/FAQ.md`。
