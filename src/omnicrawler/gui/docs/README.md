# OmniCrawler GUI 工作台 — 快速上手指南

## 1. 安装与环境配置（3 步）

### 步骤 1：安装 OmniCrawler 框架

```bash
cd /path/to/omnicrawler
pip install -e .
```

### 步骤 2：安装 GUI 依赖

```bash
cd /path/to/omnicrawler_gui
pip install -r requirements.txt
```

### 步骤 3：启动 GUI 工作台

```bash
python main.py
```

## 2. 5 分钟跑通第一个爬虫

1. **启动应用** → 双击 `main.py` 或命令行执行 `python main.py`
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
- **如何设置定时任务？** → 使用 `python main.py --run config.yaml` + 系统定时任务

## 5. 无 GUI 模式

```bash
# 直接运行配置，不显示 GUI
python main.py --run configs/my_config.yaml --log-level DEBUG
```

更多帮助请查看 `docs/FAQ.md`。
