#!/usr/bin/env python3
"""Generate English .po translation from .pot template + built-in translation map.

Usage::

    python tools/generate_en_po.py
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POT_PATH = PROJECT_ROOT / "locale" / "omnicrawler-gui.pot"
PO_PATH = PROJECT_ROOT / "locale" / "en_US" / "LC_MESSAGES" / "omnicrawler-gui.po"

# -- Translation map: zh_CN → en_US ----------------------------
_TRANSLATIONS: dict[str, str] = {
    "任务工作台": "Task Workspace",
    "打开空白任务": "Open Blank Task",
    "任务目标": "Task Goal",
    "采集方案": "Crawl Plan",
    "字段规则": "Field Rules",
    "试跑验证": "Trial Validation",
    "输出与交付": "Output & Delivery",
    "输出与存储在此配置；全量运行前需要先通过试跑验证。": "Configure output and storage here. Trial validation is required before a full run.",
    "简单模式：仅显示创建、试跑、运行和结果等核心操作": "Simple mode: only core create, trial, run, and results actions are shown",
    "你想采集什么？": "What do you want to collect?",
    "任务网址或描述": "Task URL or description",
    "创建任务": "Create Task",
    "创建任务并准备试跑": "Create task and prepare a trial run",
    "采集新闻标题": "Collect News Headlines",
    "采集新闻栏目中的标题、日期和链接": "Collect titles, dates, and links from a news section",
    "下载 PDF": "Download PDFs",
    "下载网页中的新增 PDF 附件": "Download new PDF attachments from web pages",
    "监测变化": "Monitor Changes",
    "每周监测网页内容变化": "Monitor web page changes weekly",
    "查看全部": "View All",
    "继续编辑": "Continue Editing",
    "暂无最近任务。创建并运行一次任务后，可从这里继续编辑或查看结果。": "No recent tasks. After creating and running a task, continue editing or view results here.",
    "工作": "Work",
    "自动化": "Automation",
    "高级": "Advanced",
    "运行与历史": "Runs & History",
    "场景与模板": "Scenes & Templates",
    "导航分组": "Navigation group",
    "试跑 {0} 页": "Trial Run {0} Pages",
    "开始全量运行": "Start Full Run",
    "尚未试跑：填写网址并生成草稿后开始小样本验证": "No trial run yet. Enter a URL and generate a draft to start sample validation.",
    "完成试跑后，这里会显示页面数、记录数和诊断建议。": "After a trial run, page count, record count, and diagnostic suggestions appear here.",
    "这里显示最近试跑的字段命中、页面处理和错误详情。": "Recent field matches, processed pages, and error details appear here.",
    "检查网址与范围": "Check URL & Scope",
    "修正字段规则": "Fix Field Rules",
    "✓ 交付配置有效": "✓ Delivery configuration is valid",
    "⚠ 至少选择一种输出格式": "⚠ Select at least one output format",
    "请先修复输出与交付设置": "Fix output and delivery settings first",
    "采集范围或字段规则已变更，请重新试跑": "Crawl scope or field rules changed. Run another trial.",
    "✓ 试跑通过：可以开始全量运行": "✓ Trial passed. You can start the full run.",
    "试跑通过：可以开始全量运行": "Trial passed. You can start the full run.",
    "<b>第一次创建任务</b><br>": "<b>Create Your First Task</b><br>",
    "跳过引导": "Skip Guide",
    "粘贴网址": "Paste a URL",
    "确认采集方案": "Confirm Crawl Plan",
    "试跑少量页面": "Trial Run a Few Pages",
    "可以全量运行": "Ready for Full Run",
    "切换任务工作台/YAML 编辑器": "Switch Task Workspace/YAML Editor",
    "⇄ 工作台": "⇄ Workspace",
    "小样本试跑完成：请在工作台查看结果": "Sample trial completed. View results in the workspace.",
    "小样本试跑失败：请在工作台查看诊断": "Sample trial failed. View diagnostics in the workspace.",
    "试跑未通过：请按工作台中的诊断建议修正": "Trial did not pass. Follow the workspace diagnostics.",
    "页面可以访问，但没有提取到记录。请检查字段选择器、列表容器或页面类型。": "The page is accessible, but no records were extracted. Check field selectors, list containers, or page type.",
    "没有成功处理页面。请检查网址、访问权限、robots 设置或页面是否需要浏览器渲染。": "No pages were processed successfully. Check the URL, access permissions, robots settings, or browser rendering requirements.",
    "工作台锁定中，请先完成当前操作": "The workspace is locked. Finish the current operation first.",
    "放弃 YAML，保留工作台修改": "Discard YAML and keep workspace changes",
    "粘贴网址，或用一句话说明目标、范围、下载和监测要求。": "Paste a URL, or describe the goal, scope, downloads, and monitoring requirements.",
    "系统会自动选择安全起点，生成可修改草案，并始终先试跑。": "The system chooses a safe starting point, creates an editable draft, and always runs a trial first.",
    "粘贴 https://example.com，或输入：采集这个新闻栏目中的标题、日期和链接": "Paste https://example.com, or enter: collect titles, dates, and links from this news section",
    "示例：": "Examples:",
    "请粘贴网址或描述你的任务": "Paste a URL or describe your task",
    "请修改：{0}": "Please fix: {0}",
    "<b>试跑概览</b><br>状态：{0}<br>处理页面：{1}<br>提取记录：{2}<br>平均每页：{3:.1f} 条": "<b>Trial Summary</b><br>Status: {0}<br>Pages processed: {1}<br>Records extracted: {2}<br>Average per page: {3:.1f}",
    "OmniCrawler 配置向导": "OmniCrawler Configuration Wizard",
    "任务状态": "Task Status",
    "空闲": "Idle",
    "运行中": "Running",
    "已完成": "Completed",
    "错误": "Error",
    "保存": "Save",
    "打开": "Open",
    "取消": "Cancel",
    "确定": "OK",
    "关闭": "Close",
    "删除": "Delete",
    "新建": "New",
    "导入": "Import",
    "导出": "Export",
    "帮助": "Help",
    "关于": "About",
    "设置": "Settings",
    "搜索": "Search",
    "刷新": "Refresh",
    "停止": "Stop",
    "暂停": "Pause",
    "继续": "Continue",
    "运行": "Run",
    "回退": "Back",
    "← 上一步": "← Back",
    "下一步 →": "Next →",
    "完成并保存": "Finish & Save",
    "模板库": "Template Library",
    "搜索名称、说明或标签…": "Search name, description, or tags...",
    "全部分类": "All Categories",
    "加载模板": "Load Template",
    "未保存": "Unsaved",
    "已保存": "Saved",
    "保存失败": "Save Failed",
    "打开失败": "Open Failed",
    "新建配置": "New Config",
    "打开 YAML 配置": "Open YAML Config",
    "另存为 YAML 配置": "Save YAML Config As",
    "YAML 文件 (*.yaml *.yml);;所有文件 (*)": "YAML Files (*.yaml *.yml);;All Files (*)",
    "当前配置有未保存的更改，是否继续？": "Unsaved changes exist. Continue?",
    "导出配置包": "Export Config Package",
    "导入配置包": "Import Config Package",
    "ZIP 文件 (*.zip);;所有文件 (*)": "ZIP Files (*.zip);;All Files (*)",
    "ZIP 文件 (*.zip)": "ZIP Files (*.zip)",
    "即将导入配置 '{0}'，当前配置将被覆盖，是否继续？": "Import config '{0}' — current config will be overwritten. Continue?",
    "导入配置": "Import Config",
    "配置历史": "Config History",
    "清除最近文件": "Clear Recent Files",
    "环境配置": "Environment Setup",
    "未检测到 omnicrawler 命令。请选择配置方式：": "omnicrawler command not detected. Choose setup method:",
    "自动安装": "Auto Install",
    "手动指定路径": "Specify Path Manually",
    "跳过（仅编辑配置）": "Skip (Edit Only)",
    "安装成功！": "Installation Successful!",
    "安装失败": "Installation Failed",
    "选择 omnicrawler 可执行文件": "Select omnicrawler executable",
    "可执行文件 (*.exe *.bat *.cmd);;所有文件 (*)": "Executables (*.exe *.bat *.cmd);;All Files (*)",
    "无效路径": "Invalid Path",
    "指定的路径无效": "The specified path is invalid",
    "环境就绪": "Environment Ready",
    "快速体验": "Quick Start",
    "当前配置将被覆盖，是否继续？": "Current config will be overwritten. Continue?",
    "模板加载失败": "Template Load Failed",
    "无法加载 news 模板": "Cannot load news template",
    "快速体验 - 未保存": "Quick Start - Unsaved",
    "请先安装依赖": "Please install dependencies first",
    "请确保已安装": "Please ensure installation of",
    "确认": "Confirm",
    "是": "Yes",
    "否": "No",
    "文件": "File",
    "编辑": "Edit",
    "视图": "View",
    "工具": "Tools",
    "窗口": "Window",
    "配置": "Config",
    "任务": "Task",
    "结果": "Results",
    "日志": "Logs",
    "插件": "Plugins",
    "模板": "Templates",
    "恢复": "Recovery",
    "安全": "Security",
    "导航": "Navigation",
    "首页": "Home",
    "开发者": "Developer",
    "专业复核台": "Professional Review Desk",
    "操作录制": "Action Recording",
    "计划任务": "Scheduled Tasks",
    "错误中心": "Error Center",
    "运行对比": "Run Comparison",
    "网站检查": "Site Inspection",
    "字段建议": "Field Suggestions",
    "数据源": "Data Source",
    "网址列表": "URL List",
    "字段定义": "Field Definitions",
    "下载设置": "Download Settings",
    "预览确认": "Preview & Confirm",
    "明亮模式": "Light Mode",
    "暗色模式": "Dark Mode",
    "高对比度": "High Contrast",
    "色盲友好": "Color Blind Friendly",
    "减少动画": "Reduce Motion",
    "运行功率": "Run Power",
    "尝试": "Try",
    "创建": "Create",
    "取消操作": "Cancel Operation",
    "处理中": "Processing",
    "等待中": "Waiting",
    "失败": "Failed",
    "成功": "Success",
    "警告": "Warning",
    "信息": "Information",
    "提示": "Tip",
    "配置已更新": "Config Updated",
    "任务已开始": "Task Started",
    "任务已停止": "Task Stopped",
    "任务已暂停": "Task Paused",
    "任务已完成": "Task Completed",
    "复制": "Copy",
    "粘贴": "Paste",
    "剪切": "Cut",
    "全选": "Select All",
    "撤销": "Undo",
    "重做": "Redo",
    "查找": "Find",
    "替换": "Replace",
    "快捷键": "Shortcuts",
    "FAQ": "FAQ",
    "快速入门": "Quick Start Guide",
    "功能总览": "Capability Overview",
    "检查更新": "Check for Updates",
    "许可证": "License",
    "第三方组件": "Third-Party Components",
    "主题": "Theme",
    "界面缩放": "Interface Scale",
    "请勿打扰": "Do Not Disturb",
    "任务状态指示器": "Task Status Indicator",
    "字段名称": "Field Name",
    "字段类型": "Field Type",
    "选择器": "Selector",
    "必填": "Required",
    "描述": "Description",
    "默认值": "Default Value",
    "示例值": "Sample Value",
    "规则": "Rules",
    "添加字段": "Add Field",
    "移除字段": "Remove Field",
    "测试选择器": "Test Selector",
    "选择器测试结果": "Selector Test Results",
    "无匹配": "No Match",
    "匹配数量": "Match Count",
    "采集范围": "Crawl Scope",
    "最大页数": "Max Pages",
    "延迟秒数": "Delay Seconds",
    "超时秒数": "Timeout Seconds",
    "并发数": "Concurrency",
    "输出格式": "Output Format",
    "JSON Lines": "JSON Lines",
    "CSV 文件": "CSV File",
    "Excel 文件": "Excel File",
    "DuckDB 数据库": "DuckDB Database",
    "附件下载": "Attachment Download",
    "PDF 处理": "PDF Processing",
    "OCR 引擎": "OCR Engine",
    "AI 辅助": "AI Assist",
    "AI 服务": "AI Service",
    "API 地址": "API Address",
    "API 密钥": "API Key",
    "模型名称": "Model Name",
    "最大 Token": "Max Tokens",
    "温度": "Temperature",
    "系统提示词": "System Prompt",
    "启用 AI": "Enable AI",
    "项目名称": "Project Name",
    "项目路径": "Project Path",
    "工作目录": "Working Directory",
    "浏览": "Browse",
    "选择目录": "Select Directory",
    "选择文件": "Select File",
    "所有文件 (*)": "All Files (*)",
    "文本文件 (*.txt)": "Text Files (*.txt)",
    "图片文件": "Image Files",
    "视频文件": "Video Files",
    "未知": "Unknown",
    "其他": "Other",
    "无": "None",
    "是/否": "Yes/No",
    "开启": "On",
    "启用": "Enabled",
    "禁用": "Disabled",
    "是（默认）": "Yes (Default)",
    "否（默认）": "No (Default)",
}


def _auto_translate(msgid: str) -> str:
    """Fallback translation for strings not in the map."""
    if msgid in _TRANSLATIONS:
        return _TRANSLATIONS[msgid]

    # Pattern-based translations
    patterns = [
        (r"^重复: (.+)$", r"Repeat: \1"),
        (r"^已选中 (\d+) 行$", r"\1 row(s) selected"),
        (r"^已移除 (\d+) 行$", r"\1 row(s) removed"),
        (r"^剩余:\s*(.+)$", r"Remaining: \1"),
        (r"^最多\s*(\d+)\s*页$", r"Max \1 pages"),
        (r"^第\s*(\d+)\s*页$", r"Page \1"),
        (r"^正则:\s*(.+)$", r"Regex: \1"),
        (r"^当前页显示\s*(\d+)\s*/\s*(\d+)\s*行$", r"Showing \1 / \2 rows"),
        (r"^共\s*(\d+)\s*个文件$", r"\1 file(s) total"),
        (r"^已导入:\s*(.+)$", r"Imported: \1"),
        (r"^完成:\s*(.+)$", r"Done: \1"),
        (r"^环境就绪:\s*(.+)$", r"Environment ready: \1"),
    ]
    for pattern, replacement in patterns:
        m = re.match(pattern, msgid)
        if m:
            return re.sub(pattern, replacement, msgid)

    return msgid


def generate_po() -> int:
    """Read .pot, generate .po with English translations. Returns string count."""
    if not POT_PATH.is_file():
        raise FileNotFoundError(f"{POT_PATH} not found. Run extract_i18n.py first.")

    msgids: list[tuple[list[str], str]] = []
    current_locations: list[str] = []
    current_msgid: str | None = None

    for line in POT_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("#:"):
            current_locations.append(line[3:].strip())
        elif line.startswith("msgid "):
            m = re.match(r'msgid "(.+)"$', line)
            if m:
                current_msgid = m.group(1)
        elif line == 'msgstr ""' and current_msgid is not None:
            msgids.append((current_locations, current_msgid))
            current_locations = []
            current_msgid = None

    PO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PO_PATH, "w", encoding="utf-8") as f:
        f.write("# OmniCrawler GUI — English (en_US) translation\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n")
        f.write("#\n")
        f.write('msgid ""\n')
        f.write('msgstr ""\n')
        f.write('"Language: en_US\\n"\n')
        f.write('"Content-Type: text/plain; charset=UTF-8\\n"\n\n')

        translated = 0
        for locations, msgid in msgids:
            for loc in locations:
                f.write(f"#: {loc}\n")
            escaped = msgid.replace('"', '\\"')
            f.write(f'msgid "{escaped}"\n')
            translation = _auto_translate(msgid)
            if translation != msgid:
                translated += 1
            escaped_tr = translation.replace('"', '\\"')
            f.write(f'msgstr "{escaped_tr}"\n\n')

    return translated


if __name__ == "__main__":
    translated = generate_po()
    total = 0
    with open(PO_PATH, encoding="utf-8") as f:
        total = f.read().count("\nmsgid ")
    print(f"Generated {PO_PATH}")
    print(f"  {translated}/{total} strings translated ({100*translated//max(1,total)}%)")
