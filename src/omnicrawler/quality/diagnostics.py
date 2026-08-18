"""统一诊断系统 — 合并 error_center + diagnostic_experience，CLI/GUI/SDK 共用。

修复 error_center 的粗糙关键词匹配，融合 diagnostic_experience 的用户友好文案，
增加 403/429/401 精细区分、正则匹配、自动修复建议执行。

用法:
    from omnicrawler.quality.diagnostics import diagnose, DiagnoseReport
    report = diagnose(error_text, context={})
    print(report.user_facing)
    if report.auto_fix:
        report.apply_fix(config)
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from html import escape as html_escape
from pathlib import Path
from typing import Any


class ErrorCategory(Enum):
    ACCESS_POLICY = "access_policy"         # 403/401/robots
    RATE_LIMITED = "rate_limited"           # 429/限流
    NETWORK_TRANSIENT = "network_transient" # DNS/连接超时
    NETWORK_PERMANENT = "network_permanent" # 域名不存在/IP封锁
    SELECTOR_SYNTAX = "selector_syntax"     # XPath/CSS 语法错误
    SELECTOR_NOT_FOUND = "selector_not_found" # 选择器无匹配
    TEMPLATE_EXTRACTION = "template_or_extraction"
    LOCAL_RESOURCE = "local_resource"       # 磁盘/内存
    ENVIRONMENT = "environment"             # Python/依赖
    MISSING_OPTIONAL = "missing_optional"
    CAPTCHA = "captcha"                     # 验证码
    UNKNOWN = "unknown"


# ── 精确诊断规则 ──────────────────────────────────────────────────────

_DIAGNOSE_RULES: list[tuple[str, ErrorCategory, str, str, str, str]] = [
    # (正则, 分类, 用户解释, 可能原因, 建议动作, 数据影响)
    (r"403\b|Forbidden|Access Denied(?!.*captcha)", ErrorCategory.ACCESS_POLICY,
     "网站拒绝了访问请求（403 Forbidden）",
     "可能需要登录、IP 被封锁、或 User-Agent 被识别为爬虫",
     "检查是否需要登录凭据；尝试更换 User-Agent 或使用代理；确认 robots.txt 允许",
     "当前页面数据丢失，但不影响已采集的数据"),
    (r"401\b|Unauthorized|未授权", ErrorCategory.ACCESS_POLICY,
     "需要身份验证（401 Unauthorized）",
     "API 需要 token 或网页需要登录",
     "在配置中添加 secret:// 引用或使用浏览器模式登录",
     "当前请求失败，不影响已采集数据"),
    (r"429\b|Too Many Requests|rate.?limit|限流|请求过于频繁", ErrorCategory.RATE_LIMITED,
     "请求频率过高被限流（429 Too Many Requests）",
     "短时间内发起了过多请求",
     "增加 delay_seconds 到 3-5 秒；降低 concurrency 到 1-2；等待几分钟后重试",
     "当前批次可能部分失败，建议降低速度后重试"),
    (r"503\b|Service Unavailable|502\b|Bad Gateway", ErrorCategory.NETWORK_TRANSIENT,
     "目标服务器暂时不可用",
     "服务器过载或维护中",
     "等待 1-5 分钟后重试；如持续出现，降低请求频率",
     "当前请求失败，可重试"),
    (r"(?<!parse)timeout|timed.out|TimeoutError|connect.*refused|Connection.*refused", ErrorCategory.NETWORK_TRANSIENT,
     "网络连接超时或被拒绝",
     "网络不稳定、DNS 问题、或目标服务器不响应",
     "检查网络连接；增加 timeout_seconds；确认 URL 可访问",
     "当前请求失败，可重试"),
    (r"DNS|Name or service not known|getaddrinfo|nodename nor servname", ErrorCategory.NETWORK_PERMANENT,
     "域名解析失败",
     "域名拼写错误或 DNS 服务不可用",
     "检查 URL 拼写；尝试 nslookup 验证域名",
     "当前任务无法继续，请修正 URL 后重试"),
    (r"XPath|XPATH|xpath.*error|Invalid expression|XPathEvalError", ErrorCategory.SELECTOR_SYNTAX,
     "XPath 选择器语法错误",
     "XPath 表达式包含语法错误或使用了不支持的函数",
     "在浏览器开发者工具中测试 XPath；检查引号匹配；减少嵌套层级",
     "字段提取失败，该字段将为空"),
    (r"CSS.*selector.*invalid|css.*syntax|SelectorSyntaxError", ErrorCategory.SELECTOR_SYNTAX,
     "CSS 选择器语法错误",
     "CSS 选择器包含非法字符或格式错误",
     "在浏览器中测试选择器；检查特殊字符转义",
     "字段提取失败"),
    (r"NoSuchElement|ElementNotFound|no element found|no match|not found.*selector", ErrorCategory.SELECTOR_NOT_FOUND,
     "页面上找不到指定的元素",
     "页面结构已变更；选择器过于具体；页面未完全加载",
     "使用 auto-analyze 重新分析页面；放宽选择器；增加 wait_until",
     "该字段将为空"),
    (r"captcha|verification.*(code|image|required)|人机验证|验证码", ErrorCategory.CAPTCHA,
     "页面要求完成验证码",
     "网站检测到自动化访问并要求人机验证",
     "降低请求频率；使用 undetected 浏览器模式；手动完成验证后恢复",
     "当前页面采集失败"),
    (r"No space left|disk.full|磁盘空间|ENOSPC", ErrorCategory.LOCAL_RESOURCE,
     "磁盘空间不足",
     "工作目录所在磁盘已满",
     "清理不必要的文件；扩展磁盘空间；运行 omnicrawler cleanup",
     "新数据无法写入，已采集数据完好"),
    (r"MemoryError|out of memory|OOM", ErrorCategory.LOCAL_RESOURCE,
     "内存不足",
     "页面过大或 concurrency 过高导致内存超限",
     "降低 concurrency；减少 max_pages；限制 max_response_bytes",
     "当前页面可能丢失"),
    (r"ModuleNotFoundError|ImportError|No module named", ErrorCategory.ENVIRONMENT,
     "缺少 Python 依赖",
     "未安装对应的 optional extra",
     "运行 pip install omnicrawler-platform[full] 安装所有依赖",
     "对应功能不可用"),
    (r"playwright.*(not found|not installed|missing)", ErrorCategory.ENVIRONMENT,
     "Playwright 浏览器未安装",
     "首次使用需下载 Chromium 浏览器",
     "运行 python -m playwright install chromium",
     "浏览器模式不可用"),
]


def diagnose(error_text: str, context: dict[str, Any] | None = None) -> DiagnoseReport:
    """统一诊断入口 — 根据错误文本返回结构化诊断报告。

    Args:
        error_text: 错误消息或异常文本。
        context: 额外上下文（url, config_path, run_id 等）。

    Returns:
        DiagnoseReport 包含分类、用户友好解释、修复建议、自动修复动作。
    """
    ctx = context or {}

    for pattern, category, user_explanation, cause, action, data_impact in _DIAGNOSE_RULES:
        if re.search(pattern, error_text, re.IGNORECASE):
            return DiagnoseReport(
                category=category,
                error_snippet=error_text[:500],
                user_facing=f"**{user_explanation}**\n\n🔍 原因: {cause}\n\n💡 建议: {action}\n\n📊 数据: {data_impact}",
                cause=cause,
                action=action,
                data_impact=data_impact,
                retryable=category in (ErrorCategory.RATE_LIMITED, ErrorCategory.NETWORK_TRANSIENT, ErrorCategory.SELECTOR_NOT_FOUND),
                auto_fix=_build_auto_fix(category, ctx),
                help_id=_help_id_for(category),
                context=ctx,
            )

    # 兜底
    return DiagnoseReport(
        category=ErrorCategory.UNKNOWN,
        error_snippet=error_text[:500],
        user_facing=f"**未分类的错误**\n\n原始信息: {error_text[:300]}",
        cause="错误类型不在已知诊断规则中",
        action="运行 omnicrawler doctor 进行全面诊断；查看完整日志",
        data_impact="未知",
        retryable=False,
        auto_fix=None,
        help_id="doctor.tryrun",
        context=ctx,
    )


def _build_auto_fix(category: ErrorCategory, ctx: dict[str, Any]) -> AutoFix | None:
    """根据错误类别生成自动修复动作。"""
    if category == ErrorCategory.RATE_LIMITED:
        return AutoFix(
            description="降低并发并增加延迟",
            config_changes={
                "crawl.concurrency": 1,
                "http.delay_seconds": 5.0,
            },
            reversible=True,
        )
    if category == ErrorCategory.SELECTOR_NOT_FOUND:
        url = ctx.get("url", "")
        if url:
            return AutoFix(
                description=f"对 {url} 运行智能页面分析",
                command=f"omnicrawler auto-analyze {url}",
                reversible=False,
            )
    return None


def _help_id_for(category: ErrorCategory) -> str:
    _map = {
        ErrorCategory.ACCESS_POLICY: "security.access",
        ErrorCategory.RATE_LIMITED: "crawl.throttle",
        ErrorCategory.NETWORK_TRANSIENT: "tryrun.plan",
        ErrorCategory.SELECTOR_SYNTAX: "fields.definition",
        ErrorCategory.CAPTCHA: "browser.stealth",
        ErrorCategory.ENVIRONMENT: "installation",
    }
    return _map.get(category, "doctor.tryrun")


# ── 数据模型 ──────────────────────────────────────────────────────────

@dataclass
class AutoFix:
    description: str = ""
    config_changes: dict[str, Any] = field(default_factory=dict)
    command: str = ""
    reversible: bool = True

    def apply(self, config: dict[str, Any]) -> dict[str, Any]:
        """尝试自动应用修复（仅 config_changes 类型）。"""
        if not self.config_changes:
            return config
        result = dict(config)
        for key, value in self.config_changes.items():
            parts = key.split(".")
            target = result
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]
            target[parts[-1]] = value
        return result


@dataclass
class DiagnoseReport:
    category: ErrorCategory = ErrorCategory.UNKNOWN
    error_snippet: str = ""
    user_facing: str = ""
    cause: str = ""
    action: str = ""
    data_impact: str = ""
    retryable: bool = False
    auto_fix: AutoFix | None = None
    help_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "error_snippet": self.error_snippet[:200],
            "user_facing": self.user_facing,
            "retryable": self.retryable,
            "has_auto_fix": self.auto_fix is not None,
            "help_id": self.help_id,
        }

    def to_html_card(self) -> str:
        """生成适合 GUI 展示的 HTML 卡片。"""
        icon = {
            ErrorCategory.ACCESS_POLICY: "🔒", ErrorCategory.RATE_LIMITED: "⏳",
            ErrorCategory.NETWORK_TRANSIENT: "🌐", ErrorCategory.CAPTCHA: "🤖",
            ErrorCategory.SELECTOR_SYNTAX: "🔍", ErrorCategory.ENVIRONMENT: "📦",
        }.get(self.category, "❓")
        safe_message = html_escape(self.user_facing).replace("\n", "<br>")
        safe_description = html_escape(self.auto_fix.description) if self.auto_fix else ""
        return f"""<div class='diagnose-card'>
<h3>{icon} {self.category.value}</h3>
<p>{safe_message}</p>
{self._fix_button_html(safe_description)}
</div>"""

    def _fix_button_html(self, safe_description: str = "") -> str:
        if self.auto_fix:
            fix_type = "config" if self.auto_fix.config_changes else "command"
            return f'<button class="auto-fix-btn" data-fix-type="{fix_type}">🔧 自动修复: {safe_description}</button>'
        return ""


# ── 批量诊断 ──────────────────────────────────────────────────────────

def diagnose_batch(errors: list[str], context: dict[str, Any] | None = None) -> list[DiagnoseReport]:
    """批量诊断多个错误。"""
    return [diagnose(e, context) for e in errors]


def diagnose_from_state(run_id: str, state_store: Any) -> list[DiagnoseReport]:
    """从 StateStore 读取错误并批量诊断。"""
    try:
        raw_errors = state_store.error_summary(run_id)
    except Exception:
        return []
    return diagnose_batch(raw_errors, {"run_id": run_id})


LOGGER = logging.getLogger(__name__)

_SENSITIVE_KEYS = re.compile(r"(?:authorization|cookie|token|password|passwd|secret|api[_-]?key|credential)", re.I)
_SENSITIVE_TEXT = (
    re.compile(r"(?i)\b(Bearer|Basic)[\s-]+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)([?&](?:access_?token|api_?key|key|password|secret)=)[^&#\s]+"),
    re.compile(r"(?i)\b((?:authorization|cookie|token|password|passwd|secret|api[_-]?key)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+@"),
)
_MAX_TEXT_LENGTH = 16_384
_MAX_TRACEBACK_LENGTH = 65_536
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _redact_text(value: str, maximum: int = _MAX_TEXT_LENGTH) -> str:
    result = value
    for pattern in _SENSITIVE_TEXT:
        if pattern.pattern.startswith("(?i)\\b(Bearer"):
            result = pattern.sub(r"\1 <redacted>", result)
        else:
            result = pattern.sub(r"\1<redacted>", result)
    if len(result) > maximum:
        return result[:maximum] + f"... <truncated {len(result) - maximum} chars>"
    return result


def _safe_filename_part(value: Any, fallback: str) -> str:
    cleaned = _SAFE_FILENAME.sub("_", str(value)).strip("._-")
    return (cleaned[:80] or fallback)


def redact_diagnostic_text(value: str, maximum: int = _MAX_TEXT_LENGTH) -> str:
    """Public compatibility API for every diagnostic/reporting surface."""
    return _redact_text(value, maximum)


def redact_diagnostic_value(value: Any) -> Any:
    """Recursively redact and JSON-normalize diagnostic data."""
    return _redact(value)


def _redact(value: Any, key: str = "") -> Any:
    """Convert diagnostic payloads to JSON-safe data without leaking credentials."""
    if _SENSITIVE_KEYS.search(key):
        return "<redacted>"
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "omitted": True}
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _redact(getattr(value, item.name), item.name) for item in fields(value)}
    if hasattr(value, "__dict__"):
        return _redact(vars(value))
    return str(value)


class DiagnosticRecorder:
    """Persist redacted, per-failure JSON diagnostics for pipeline recovery."""

    def __init__(self, workspace: str | Path, config: dict[str, Any] | None = None):
        self.workspace = Path(workspace)
        self.directory = self.workspace / "diagnostics"
        raw_config = config or {}
        self.config = _redact(raw_config)
        settings = raw_config.get("diagnostics", {}) if isinstance(raw_config, dict) else {}
        settings = settings if isinstance(settings, dict) else {}
        self.retention_days = self._positive_int(settings.get("retention_days"), 30)
        self.max_files = self._positive_int(settings.get("max_files"), 500)
        self.max_bytes = self._positive_int(settings.get("max_bytes"), 500 * 1024 * 1024)

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def failure(self, run_id: str, stage: str, error: BaseException, *, request: Any = None, result: Any = None) -> Path | None:
        timestamp = datetime.now(timezone.utc)
        trace = _redact_text("".join(traceback.format_exception(error)), _MAX_TRACEBACK_LENGTH)
        payload = {
            "run_id": str(run_id),
            "stage": str(stage),
            "timestamp": timestamp.isoformat(),
            "error": {"type": type(error).__name__, "message": _redact_text(str(error)), "traceback": trace},
            "config": self.config,
        }
        if request is not None:
            payload["request"] = _redact(request)
        if result is not None:
            payload["result"] = _redact(result)
        safe_run_id = _safe_filename_part(run_id, "run")
        safe_stage = _safe_filename_part(stage, "failure")
        path = self.directory / f"{safe_run_id}-{safe_stage}-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        temporary = path.with_suffix(".json.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            self.cleanup(now=timestamp, keep=path)
            return path
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.warning("Unable to persist diagnostic for run %s at stage %s: %s", run_id, stage, exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def cleanup(self, *, now: datetime | None = None, keep: Path | None = None) -> dict[str, int]:
        """Apply age, count and aggregate-size limits without touching non-diagnostic files."""
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(days=self.retention_days)
        removed_files = 0
        removed_bytes = 0
        try:
            candidates = []
            for path in self.directory.glob("*.json"):
                if not path.is_file():
                    continue
                stat = path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                if path != keep and modified < cutoff:
                    path.unlink()
                    removed_files += 1
                    removed_bytes += stat.st_size
                    continue
                candidates.append((path, stat.st_mtime, stat.st_size))

            candidates.sort(key=lambda item: item[1], reverse=True)
            total_bytes = sum(item[2] for item in candidates)
            while len(candidates) > self.max_files or total_bytes > self.max_bytes:
                removable = next((item for item in reversed(candidates) if item[0] != keep), None)
                if removable is None:
                    break
                path, _mtime, size = removable
                path.unlink()
                candidates.remove(removable)
                total_bytes -= size
                removed_files += 1
                removed_bytes += size
        except OSError as exc:
            LOGGER.warning("Unable to clean diagnostic directory %s: %s", self.directory, exc)
        return {"removed_files": removed_files, "removed_bytes": removed_bytes}
