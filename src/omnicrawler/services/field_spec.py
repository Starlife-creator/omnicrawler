"""统一字段说明规范。

所有重要配置项都采用统一说明结构：
1. 这是什么
2. 为什么需要
3. 推荐怎么设置
4. 当前默认值
5. 修改后有什么影响
6. 一个真实例子
7. 常见错误
8. 是否可以稍后修改
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """统一字段说明。"""
    field_id: str
    label: str                    # 业务化标签（如"同时采集数量（并发数）"）
    what: str                     # 这是什么
    why: str                      # 为什么需要
    recommendation: str           # 推荐怎么设置
    default: str                  # 当前默认值
    impact: str                   # 修改后有什么影响
    example: str                  # 一个真实例子
    common_errors: str            # 常见错误
    can_modify_later: bool = True  # 是否可以稍后修改
    technical_name: str = ""      # 技术名称（可选，用于专业模式）


# ---------------------------------------------------------------------------
# 核心配置字段说明
# ---------------------------------------------------------------------------

FIELD_SPECS: dict[str, FieldSpec] = {
    "concurrency": FieldSpec(
        "concurrency",
        "同时采集数量（并发数）",
        "同时执行多少个采集请求。数值越大可能越快，但更容易触发网站限制。",
        "需要考虑目标网站的承载能力和本机资源。合适的并发可以在速度和礼貌之间取得平衡。",
        "普通电脑推荐 2；公开大站可在试跑成功后尝试 4；政府/小众网站建议保持 1。",
        "2（均衡模式）",
        "增加并发可能触发目标网站的速率限制或 IP 封禁。减少并发会延长总运行时间。",
        "采集一个政府公告栏目：并发 1，不会给服务器造成压力，每次请求间隔 2 秒。",
        "把所有任务的并发都设为最大值，导致 IP 被封或数据不完整。",
        can_modify_later=True,
        technical_name="crawl.concurrency",
    ),
    "max_pages": FieldSpec(
        "max_pages",
        "最大页面数",
        "本次任务最多采集多少个页面。超过限额后自动停止，不会无限扩展。",
        "防止意外遍历整个网站。设定一个合理上限可以在范围控制和信息完整之间平衡。",
        "栏目采集建议 50-200；单页面保存设为 1；不确定时先设为 10 并试跑。",
        "100（均衡模式）",
        "太小可能漏掉重要内容；太大会增加运行时间和存储空间。不影响每个页面的采集质量。",
        "采集一个每日新闻栏目，每周约 20 篇文章，设置 max_pages=50 留有余量。",
        "设为 0（无限制）或一个极大的数，导致任务运行数小时。",
        can_modify_later=True,
        technical_name="crawl.max_pages",
    ),
    "timeout_seconds": FieldSpec(
        "timeout_seconds",
        "请求超时（秒）",
        "等待单个 HTTP 请求响应的最大时间。超时后自动重试或跳过该页面。",
        "避免任务因个别响应慢的页面而停滞。合理的超时能让任务自动跳过问题页面。",
        "普通网页 30 秒；大文件下载 120 秒；API 请求 15 秒。",
        "30 秒",
        "太短可能误判正常页面为超时；太长会降低整体采集速度。不影响已成功的页面。",
        "采集一个包含大 PDF 的栏目：超时设为 120 秒，确保文件下载不中断。",
        "设置 5 秒超时导致大部分含图片的页面被标记为失败。",
        can_modify_later=True,
        technical_name="http.timeout_seconds",
    ),
    "same_host": FieldSpec(
        "same_host",
        "限制在同一网站",
        "是否只访问入口网址所在域名下的页面，不跳转到其他网站。",
        "防止采集从一个网站意外扩散到其他网站。这是最基础的安全边界之一。",
        "推荐始终开启（默认）；仅当明确需要跨站跟踪时关闭。",
        "开启（限制在入口域名）",
        "关闭后系统可能跟随链接采集到其他网站，包括广告、统计和社交平台。",
        "采集 example.com/news 栏目：开启此选项后，系统不会追踪到 twitter.com 的分享链接。",
        "关闭此选项后意外采集到大量无关网站的内容。",
        can_modify_later=True,
        technical_name="crawl.same_host",
    ),
    "process_pdf": FieldSpec(
        "process_pdf",
        "PDF 文字提取与 OCR",
        "提取 PDF 文件中的文字、表格和元数据；对扫描版 PDF 自动调用 OCR。",
        "很多业务信息不在网页正文而在 PDF 附件中。不处理 PDF 会遗漏重要内容。",
        '推荐设为"自动"：有文本层直接提取，扫描件才 OCR。不确定时留默认值。',
        "关闭（需手动启用）",
        "启用后会增加处理时间；OCR 还需要安装 Tesseract 或 PaddleOCR。不影响网页采集。",
        "采集政府公告栏目，每篇公告都是 PDF：启用 PDF 处理并提取标题、文号和日期。",
        "对所有 PDF 强制 OCR，即使已有文本层，导致处理速度极慢。",
        can_modify_later=True,
        technical_name="processors.pdf.enabled",
    ),
    "download_extensions": FieldSpec(
        "download_extensions",
        "下载文件类型",
        "指定要下载的附件文件扩展名，如 .pdf、.docx、.xlsx、.zip。",
        "聚焦需要的文件类型，避免下载网页中所有链接（包括图片、CSS、JS 等）。",
        "根据任务需求选择：PDF 是最常见的文档格式；需要数据用 .xlsx/.csv；需要所有文件用 .*。",
        ".pdf, .doc, .docx, .xlsx（启用下载时）",
        "扩展名太宽泛会下载大量无关文件；太窄可能遗漏重要附件。系统也会检查响应类型。",
        "只下载 PDF 公告：设置扩展名为 .pdf。即使链接没有 .pdf 后缀，系统也会按 Content-Type 识别。",
        "把 .html 加入下载列表，导致下载大量网页而非附件。",
        can_modify_later=True,
        technical_name="download.extensions",
    ),
    "monitor_same_url": FieldSpec(
        "monitor_same_url",
        "同址内容变化监测",
        "定期重新访问并比较同一网址的内容。网址不变也可能发布新版本。",
        "适合跟踪政策、公告、价格等常变内容。无需每次都重新配置任务。",
        "需要持续跟踪时启用；首次运行建立基线，后续自动比较差异。建议先试跑确认范围。",
        "关闭（需手动启用）",
        '会增加重复访问次数和存储空间。内容不变时每条记录会标记为"未变化"。',
        "每周检查某个政策页面的 PDF 是否有新版：启用监测，设置每周运行。",
        "启用监测但不设置调度，结果只运行一次没有后续比较。",
        can_modify_later=True,
        technical_name="monitor.same_url",
    ),
    "output_formats": FieldSpec(
        "output_formats",
        "输出格式",
        "选择采集结果的保存格式：JSONL（完整留档）、CSV（简单表格）、Excel（人工查看）、Parquet（大数据分析）。",
        "不同格式适合不同的使用场景。选择对的格式能让后续处理更高效。",
        "普通用户推荐 Excel + JSONL：Excel 方便打开查看，JSONL 确保完整数据不丢失。",
        "JSONL + CSV + Excel",
        "CSV 不适合嵌套结构（如多个作者、复杂字段）；Parquet 需要专门工具查看。不影响原始数据。",
        "需要交给同事复核：生成 Excel 方便他们在 Office 中打开、筛选和批注。",
        "只选 CSV 导致嵌套字段（如标签列表）信息被截断或展开混乱。",
        can_modify_later=True,
        technical_name="outputs.formats",
    ),
    "source_kind": FieldSpec(
        "source_kind",
        "获取方式",
        "决定用快速 HTTP 请求、完整浏览器、REST API 还是 RSS 订阅获取内容。",
        "不同页面需要不同的获取方式。静态网页用 HTTP 最快，动态页面需要浏览器。",
        '优先保留"自动识别"；系统会根据实际探测结果选择。明确需要点击/滚动时选浏览器。',
        "自动识别（HTTP 优先，必要时升级浏览器）",
        "选择浏览器会增加资源消耗（内存、CPU、时间）。选择 HTTP 可能遗漏 JS 动态加载的内容。",
        "采集一个 React 渲染的新闻列表：选浏览器模式以执行 JavaScript。采集一个纯 HTML 博客：HTTP 即可。",
        "把地址栏网址误当成 JSON API 地址，然后用 REST 模式访问。",
        can_modify_later=True,
        technical_name="source.kind",
    ),
}


def get_field_spec(field_id: str) -> FieldSpec | None:
    """获取指定字段的说明。"""
    return FIELD_SPECS.get(field_id)


def field_help_html(field_id: str, *, mode: str = "simple") -> str:
    """生成字段帮助 HTML。

    简单模式：业务化表达，技术名称放在补充说明。
    专业/开发者模式：同时显示技术名称。
    """
    spec = get_field_spec(field_id)
    if spec is None:
        return f"<p>未找到字段 '{field_id}' 的帮助信息。</p>"

    label = spec.label
    if mode != "simple" and spec.technical_name:
        label = f"{spec.label}（<code>{spec.technical_name}</code>）"

    parts = [
        f"<h3>{label}</h3>",
        "<dl>",
        f"<dt><b>这是什么</b></dt><dd>{spec.what}</dd>",
        f"<dt><b>为什么需要</b></dt><dd>{spec.why}</dd>",
        f"<dt><b>推荐设置</b></dt><dd>{spec.recommendation}</dd>",
        f"<dt><b>当前默认值</b></dt><dd>{spec.default}</dd>",
        f"<dt><b>修改影响</b></dt><dd>{spec.impact}</dd>",
        f"<dt><b>示例</b></dt><dd>{spec.example}</dd>",
        f"<dt><b>常见错误</b></dt><dd>{spec.common_errors}</dd>",
    ]
    if spec.can_modify_later:
        parts.append("<dt><b>可否稍后修改</b></dt><dd>✓ 可以，在运行前均可修改</dd>")
    else:
        parts.append("<dt><b>可否稍后修改</b></dt><dd>✗ 任务开始后不可修改</dd>")
    parts.append("</dl>")

    return "\n".join(parts)


def all_field_ids() -> list[str]:
    """返回所有已注册的字段 ID。"""
    return sorted(FIELD_SPECS)
