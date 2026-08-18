"""Offline, searchable and mode-aware help registry with stable IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.utils import user_agent


@dataclass(frozen=True, slots=True)
class HelpEntry:
    help_id: str
    title: str
    what: str
    why: str
    how: str
    example: str
    limitations: str
    common_errors: str
    default_behavior: str
    change_impact: str
    professional_note: str = ""
    developer_note: str = ""
    keywords: tuple[str, ...] = ()
    auto_action: str = ""

    @property
    def summary(self) -> str:
        return self.what

    @property
    def details(self) -> str:
        return f"为什么：{self.why}\n如何填写：{self.how}"

    def short(self, mode: str = "simple") -> str:
        if mode == "developer" and self.developer_note:
            return f"{self.what} {self.developer_note}"
        if mode == "professional" and self.professional_note:
            return f"{self.what} {self.professional_note}"
        return self.what

    def full_text(self, mode: str = "simple", context: str = "") -> str:
        note = self.developer_note if mode == "developer" else self.professional_note if mode == "professional" else ""
        parts = [
            f"是什么：{self.what}", f"为什么：{self.why}", f"如何填写：{self.how}",
            f"示例：{self.example}", f"限制：{self.limitations}", f"常见错误：{self.common_errors}",
            f"默认行为：{self.default_behavior}", f"修改影响：{self.change_impact}",
        ]
        if note:
            parts.append(f"当前模式补充：{note}")
        if context:
            parts.append(f"当前任务建议：{context}")
        return "\n\n".join(parts)


def _entry(help_id: str, title: str, what: str, why: str, how: str, example: str,
           limitations: str, common_errors: str, default_behavior: str, change_impact: str,
           *, professional_note: str = "", developer_note: str = "", keywords: tuple[str, ...] = (),
           auto_action: str = "") -> HelpEntry:
    return HelpEntry(help_id, title, what, why, how, example, limitations, common_errors,
                     default_behavior, change_impact, professional_note, developer_note, keywords, auto_action)


HELP_ENTRIES = {
    "task.name": _entry("task.name", "任务名称", "用于区分任务、工作区和运行历史。", "清楚的名称便于以后查找结果和监测记录。", "写明站点、栏目和主题，不需要技术术语。", "政策栏目—新能源汽车补贴PDF", "不应包含密码或个人隐私。", "所有任务都叫‘新任务’，以后难以区分。", "系统按站点和时间生成名称。", "只改变显示和新工作区名称，不改变采集范围。", keywords=("名称", "项目")),
    "task.intent": _entry("task.intent", "采集目标", "用业务语言选择最终想得到的结果。", "系统据此组合网页、浏览器、附件、PDF和监测能力。", "不确定时选择‘让系统判断’，之后仍可修改。", "每周监测栏目中的主题PDF", "自动判断仍需通过试跑确认。", "把网站技术类型误当业务目标。", "自动识别普通网页。", "可能调整获取方式、附件和更新设置，会先显示计划。", keywords=("目标", "用途")),
    "source.seed": _entry("source.seed", "入口网址", "浏览器地址栏中的栏目、搜索或详情入口。", "系统从入口发现列表、详情和附件。", "复制完整http/https网址；通常一个入口就够。", "https://example.com/news", "必须是你有权访问的目标。", "猜测翻页URL或粘贴搜索引擎结果页。", "限制在同站范围并自动识别结构。", "改变实际访问域名和计划哈希。", keywords=("网址", "URL", "入口"), auto_action="inspect_seed"),
    "source.kind": _entry("source.kind", "获取方式", "决定用快速HTTP、浏览器、API或订阅源获取内容。", "不同页面需要不同执行组件。", "优先保留自动识别；只有明确需要登录/点击时选浏览器。", "动态搜索页选择‘需要登录、搜索、点击或滚动’", "REST必须是实际返回数据的接口。", "把地址栏网址误当JSON API。", "HTTP优先，证据表明需要时升级浏览器。", "改变组件需求、速度和资源估计。", professional_note="可在计划中查看路由理由。", developer_note="映射到source.kind和browser actions。", keywords=("动态", "API", "浏览器"), auto_action="inspect_seed"),
    "source.pagination": _entry("source.pagination", "翻页与操作学习", "告诉系统如何取得下一批结果。", "漏掉翻页会只采到第一页。", "地址栏不变时用‘学习点击/搜索/翻页’，不要猜参数。", "点击一次‘下一页’，系统捕获XHR中的page或cursor", "验证码和越权访问不会被绕过。", "手填不存在的page参数。", "先自动识别；无法确认时保留浏览器方案。", "可能改变动作、API候选、请求体和范围。", professional_note="可检查page/offset/cursor候选。", developer_note="编译为动作IR和API候选IR。", keywords=("下一页", "cursor", "offset"), auto_action="learn_pagination"),
    "fields.definition": _entry("fields.definition", "字段内容", "定义每条结果要保留的列。", "字段决定Excel、CSV和复核表结构。", "可用推荐字段、页面点选或普通语言描述；留空会自动提取通用内容。", "标题、发布日期、发布单位、PDF链接", "页面缺失字段会进入完整性统计。", "把整页容器当成单个字段。", "自动提取标题、正文、来源和链接。", "改变输出Schema并可能需要重新试跑。", professional_note="可检查CSS/XPath/JSONPath。", developer_note="映射到IR fields与extract.fields。", keywords=("字段", "选择器", "列")),
    "selection.topic": _entry("selection.topic", "主题筛选", "用必含、任一命中和排除词控制主题。", "先缩小栏目候选，再读正文复核可减少漏采。", "核心主题填任一命中；强条件填全部命中；噪声词填排除。", "任一：新能源,补贴；排除：招聘", "短链接无法判断时需保留不确定项。", "关闭不确定项导致正文相关内容被漏掉。", "保留不确定候选，正文后再判断。", "改变保留/排除理由和页面数量。", keywords=("主题", "关键词", "排除")),
    "download.files": _entry("download.files", "附件下载", "保存PDF、Office、压缩包等链接文件。", "很多业务信息不在网页正文而在附件中。", "勾选后选择类型；无后缀文件也会按响应类型识别。", ".pdf,.docx,.xlsx,.zip", "只下载获准站点和大小预算内的文件。", "只按网址后缀判断附件。", "关闭；启用后核对类型、文件名和签名。", "增加磁盘、网络和处理时间。", keywords=("附件", "PDF", "下载")),
    "processors.pdf": _entry("processors.pdf", "PDF与OCR", "提取PDF文字、表格和元数据；扫描件可OCR。", "附件只有转成可检索文本才能结构化和复核。", "建议OCR设为自动，有文本层时会跳过。", "扫描PDF自动使用已安装OCR组件", "复杂版式和低清扫描需人工复核。", "所有PDF都强制OCR，造成速度慢。", "文本优先，必要时OCR。", "增加组件需求、耗时和内存。", keywords=("OCR", "扫描", "PDF")),
    "updates.same_url": _entry("updates.same_url", "变化监测", "定期重新访问并比较同一网址内容。", "网址不变也可能发布新版本。", "启用后设置计划；系统保留历史并标记增删改。", "每周检查政策详情及附件内容变化", "删除需连续多轮缺失确认。", "把短暂访问失败误当删除。", "保留版本，连续2轮缺失才确认删除。", "增加重复访问和历史存储。", keywords=("监测", "更新", "变化")),
    "outputs.formats": _entry("outputs.formats", "输出格式", "选择JSONL、CSV、Excel、Parquet或DuckDB。", "不同格式适合留档、人工查看或分析。", "普通用户建议Excel+JSONL。", "Excel用于查看，JSONL用于完整留档", "CSV不适合复杂嵌套结构。", "只选CSV导致嵌套字段信息损失。", "JSONL、CSV、Excel。", "影响导出时间、磁盘与下游兼容。", keywords=("Excel", "CSV", "导出")),
    "ai.mode": _entry("ai.mode", "AI增强", "可选用本地、云端或兼容API辅助理解。", "适合字段理解和文档增强，但不是抓取必需。", "无Provider时保持关闭；密钥使用secret引用。", "secret://openai_key", "AI不会扩大域名、关闭安全策略或替代试跑。", "把真实密钥直接写入配置。", "关闭，确定性功能完整可用。", "会增加外部访问、费用和隐私边界。", keywords=("AI", "模型", "密钥"), auto_action="test_ai"),
    "tryrun.plan": _entry("tryrun.plan", "试跑与计划", "先用少量页面验证范围、字段和附件。", "能在全量运行前发现漏采、多采和字段问题。", "查看保留/排除理由后选择正确、少了、多了或字段不对。", "先试跑3页，再确认全量", "试跑结果只对同一计划哈希有效。", "修改配置后仍沿用旧试跑判断。", "独立工作区试跑3页。", "任何执行语义变化都会产生新哈希并要求重试跑。", keywords=("试跑", "哈希", "确认")),
    "crawl.concurrency": _entry("crawl.concurrency", "并发请求数", "同时向目标网站发送多少个页面请求。", "太低则慢；太高可能被目标网站限速或封禁。", "一般网页 2-4；API 可到 8；政府/小网站建议 1-2。", "普通新闻网站设置为4，政府公告网站设置为1", "过高并发会触发网站反爬机制。", "全部设为10以上导致IP被暂时封禁。", "默认 4 个并发。", "直接影响采集速度和对目标网站的压力。", keywords=("并发", "速度", "限速")),
    "crawl.max_pages": _entry("crawl.max_pages", "最大页面数", "本次采集最多访问多少个不同 URL。", "防止无限爬取失控，控制运行时间和资源。", "先估计：列表页/每页20条/要采200条 → 至少11页；加上详情页等于31页。", "栏目有10页列表+每页20条详情，设为 220", "设为0会不设上限，不推荐。", "不估计直接设9999，导致运行数小时。", "默认 100 页。", "直接影响运行时长和采集完整度。", keywords=("上限", "页面数", "范围")),
    "crawl.max_depth": _entry("crawl.max_depth", "链接深度", "从入口页面算起，最多跟随几层链接。", "防止跟着链接越走越远。", "列表+详情一般深度=2；目录→列表→详情需要3。", "入口是栏目首页→列表→详情，深度设为3", "每层可能链接数量爆炸增长。", "设为10导致采到整个网站。", "默认 3 层。", "影响采集范围和页面数。", keywords=("深度", "层数")),
    "http.delay": _entry("http.delay", "请求间隔", "每两次请求之间最少等待多少秒。", "保护目标网站服务器；降低被封概率。", "一般网站 0.5-1s；政府/小网站 2-3s；API 可按文档建议。" , "对县政府网站设置为2秒", "太短可能被视为攻击行为。", "设为0秒连续请求导致IP被封。", "默认 1 秒。", "直接影响采集速度和目标网站负载。", keywords=("间隔", "延迟", "礼貌")),
    "http.user_agent": _entry("http.user_agent", "用户代理", "发送给网站的身份标识字符串。", "网站据此识别你的工具和联系方式；方便管理员联系你。", "保留默认字段，只改邮箱和名称。", user_agent("+contact: my@email.com"), "虚假UA可被视为恶意。", "不替换默认邮箱导致管理员无从联系。", "系统生成含版本和邮箱的默认UA。", "部分网站据此调整响应内容（如移动版）。", keywords=("UA", "身份", "邮箱")),
    "http.timeout": _entry("http.timeout", "请求超时", "单个页面请求最多等待多少秒。", "过短会丢失慢页面；过长会拖慢整体。", "国内网站一般 15-30s；国外或大文件适当增加。", "普通网页20秒，大文件下载60秒", "超时页面会重试并计入失败。", "设为5秒导致大量正常页面超时。", "默认 25 秒。", "影响完整性（慢站丢页）和总耗时。", keywords=("超时", "timeout", "等待")),
    "browser.headless": _entry("browser.headless", "无头浏览器", "浏览器是否在后台运行（无界面）。", "无头模式省资源；有头模式方便调试和学习。", "正常采集用无头；录制操作/调试时取消。", "服务端部署必须无头", "部分网站检测无头并返回不同内容。", "有头模式在无显示器服务器上无法运行。", "默认开启无头。", "改变浏览器行为和资源占用。", keywords=("headless", "浏览器", "后台", "无界面")),
    "browser.actions": _entry("browser.actions", "浏览器操作", "录制在浏览器中执行的点击、搜索、翻页等操作。", "地址栏不变的动态页面必须通过操作才能翻页。", "在向导中点击按钮启动录制浏览器→操作一次→点击完成。", "点击搜索框→输入关键词→点击搜索→点击下一页", "操作路径一旦网站改版可能失效。", "手工猜URL参数而不是录制操作。", "默认不录制；需要时手动启动。", "改变请求方式、API候选和采集能力。", keywords=("录制", "操作", "点击", "搜索", "翻页")),
    "schedule": _entry("schedule", "定时任务", "按固定间隔自动重复运行采集任务。", "用于定期监测内容变化或周期性汇总。", "在专业模式下启用并设置间隔（秒）；配合系统计划任务使用。", "每小时采集一次：every_seconds=3600", "系统关机或休眠期间不会执行。", "依赖GUI保持运行而不是用系统定时器。", "默认关闭。", "增加定期网络访问和磁盘写入。", professional_note="可在专业模式直接添加；生产建议用Windows任务计划程序或cron触发 run-due。", keywords=("定时", "周期", "计划", "监测")),
    "recovery": _entry("recovery", "恢复与重试", "从中断、崩溃或失败中恢复已采集的进度。", "长时间采集难免中断；恢复可避免从头开始。", "中断后运行 omnicrawler resume；失败页面用 retry-failed。", "断网后：omnicrawler resume -c config.yaml", "只恢复已保存到SQLite的进度。", "中断后直接重新 run 导致重复采集。", "SQLite WAL 自动保存进度。", "resume 复用已有原始响应；reprocess 只重做提取。", keywords=("恢复", "中断", "resume", "重试")),
    "extract.mode": _entry("extract.mode", "提取模式", "用何种方式从页面中提取结构化数据。", "不同页面类型需要不同提取引擎。", "网页选 html；JSON API 选 json；不确定选 auto。", "静态HTML页面选 html 模式", "CSS/XPath 规则写错会提取空值。", "API 返回 JSON 但选了 html 模式。", "默认 auto，自动根据 Content-Type 选择。", "改变提取器，影响字段提取结果。", keywords=("提取", "mode", "html", "json")),
    "export": _entry("export", "结果导出", "将数据库中的记录重新导出为文件。", "修改提取规则后可重新导出而不重新采集。", "omnicrawler export -c config.yaml；指定 --run-id 导出特定批次。", "修改字段规则后：omnicrawler reprocess -c config.yaml", "reprocess 需要原始归档完整。", "重新采集而不是用已有数据重新导出。", "每次 run 结束自动导出。", "手动导出可指定不同的格式和批次。", keywords=("导出", "export", "CSV", "Excel")),
    "workspace": _entry("workspace", "工作区", "存放配置、状态、原始响应、结果和日志的本地目录。", "所有运行数据和产物集中管理，方便备份和迁移。", "保持默认；需要时可复制整个目录到另一台电脑继续。", "work/my_project/ 包含所有运行数据", "不同项目不要共用同一个工作区。", "直接编辑工作区中的 SQLite 数据库。", "自动创建在 work/<项目名>/ 下。", "工作区大小随采集量增长；定期清理用 cleanup 命令。", keywords=("工作区", "workspace", "目录", "数据")),
    "robots": _entry("robots", "robots.txt 策略", "是否遵守目标网站的 robots.txt 采集规则。", "尊重网站意愿是合法采集的基础。", "默认开启；确认有授权后才关闭。", "公开数据网站如开放数据门户通常允许采集", "关闭可能导致法律和合规风险。", "默认关闭 robots 对所有网站放肆采集。", "默认开启，失败则拒绝采集（fail-closed）。", "可能限制可访问的路径。", keywords=("robots", "合规", "合法")),
    "yaml.editor": _entry("yaml.editor", "YAML 编辑器与单一编辑权", "以 YAML 源码形式查看与编辑任务配置。", "画布与 YAML 是同一份配置的两种视图；为避免冲突，任一时刻只有一个入口可写。", "画布顶部「查看 YAML」是只读源码视图；在侧栏 YAML 编辑器页修改后保存，画布会自动同步。", "在侧栏编辑器修改 max_pages 后保存，画布重新加载并保留你的草稿", "画布有未提交修改时，外部 YAML 编辑会触发冲突提示：加载 YAML 覆盖 或 保留画布。", "画布和编辑器同时编辑同一字段导致互相覆盖。", "画布修改回写配置，YAML 文件是持久事实。", "外部编辑会置为锁定态并要求二选一，避免双重事实来源。", keywords=("YAML", "源码", "编辑器", "冲突", "锁定")),
}


NON_OBVIOUS_CONTROL_HELP_IDS = frozenset(HELP_ENTRIES)


def get_help(help_id: str) -> HelpEntry:
    if help_id not in HELP_ENTRIES:
        raise KeyError(f"未知帮助ID: {help_id}")
    return HELP_ENTRIES[help_id]


def search_help(query: str, *, mode: str = "simple") -> list[HelpEntry]:
    terms = [item.casefold() for item in query.split() if item.strip()]
    if not terms:
        return list(HELP_ENTRIES.values())
    result = []
    for entry in HELP_ENTRIES.values():
        text = " ".join((entry.help_id, entry.title, entry.full_text(mode), *entry.keywords)).casefold()
        if all(term in text for term in terms):
            result.append(entry)
    return result


def contextual_advice(help_id: str, task: dict[str, Any]) -> str:
    if help_id == "processors.pdf" and task.get("process_pdf") and not task.get("ocr_component", True):
        return "当前任务启用了PDF处理，但未检测到OCR组件；文本PDF仍可处理，扫描件需安装组件。"
    if help_id == "source.pagination" and task.get("source_kind") == "browser":
        return "当前任务使用浏览器，建议录制一次真实翻页并保留浏览器回退。"
    if help_id == "updates.same_url" and task.get("monitor_same_url"):
        return "当前任务已启用变化监测；首次运行建立基线，第二次起显示变化。"
    return ""
