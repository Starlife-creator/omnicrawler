from __future__ import annotations

import ssl
import urllib.error
from dataclasses import dataclass


@dataclass(slots=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool
    suggestion: str


class OmniCrawlError(Exception):
    code = "omnicrawl_error"
    retryable = False
    suggestion = "请查看诊断信息并检查任务配置。"

    def as_info(self) -> ErrorInfo:
        return ErrorInfo(self.code, str(self), self.retryable, self.suggestion)


class PolicyBlockedError(PermissionError, OmniCrawlError):
    code = "policy_blocked"
    suggestion = "请确认目标地址、采集范围和访问授权。"


class ResponseTooLargeError(ValueError, OmniCrawlError):
    code = "response_too_large"
    suggestion = "如确需下载该文件，请在高级设置中提高单响应大小上限。"


class PermanentFetchError(OmniCrawlError):
    code = "permanent_fetch_error"
    suggestion = "该错误自动重试通常无效，请检查 URL、权限或请求参数。"


class TransientFetchError(OmniCrawlError):
    code = "transient_fetch_error"
    retryable = True
    suggestion = "系统会自动重试；持续失败时可降低并发或延长超时。"


class EgressDisabledError(PolicyBlockedError):
    code = "egress_disabled"
    suggestion = "网络出口已被任务停止或紧急断网开关关闭；确认安全后再恢复任务。"


class EgressBudgetExceededError(PolicyBlockedError):
    code = "egress_budget_exceeded"
    suggestion = "任务已达到网络请求、流量、并发、时长或费用预算；请检查任务范围后调整预算。"


class CredentialScopeError(PolicyBlockedError):
    code = "credential_scope_blocked"
    suggestion = "凭据只能发送到任务明确批准的域名和用途。"


class AIPrivacyBlockedError(PolicyBlockedError):
    code = "ai_privacy_blocked"
    suggestion = (
        "AI 外发受隐私策略限制：请在工作区 ai_config.json 的 privacy 中显式开启"
        "对应内容类型（allow_page_text/allow_pdf_content/allow_screenshots/allow_cookies），"
        "或关闭该 AI 功能。默认 fail-closed，未显式开启即拒发。"
    )


class ConfigParseError(ValueError, OmniCrawlError):
    code = "config_parse_error"
    suggestion = "配置文件存在语法错误；请检查 YAML 缩进、引号匹配和字段名拼写，或运行 omnicrawl validate。"


class SelectorSyntaxError(OmniCrawlError):
    code = "selector_syntax_error"
    suggestion = "CSS/XPath/JSONPath 选择器语法错误；请使用浏览器开发者工具验证选择器，或在模板中使用系统推荐的选择器。"


class BrowserEngineError(OmniCrawlError):
    code = "browser_engine_error"
    suggestion = "浏览器启动失败；请确认已安装 Playwright 浏览器（python -m playwright install chromium），或检查 Chromium 路径。"


class ExportError(OmniCrawlError):
    code = "export_error"
    suggestion = "导出阶段失败；请检查磁盘空间、输出目录权限和所选格式依赖（如 Excel 需要 openpyxl）。"


class TemplateValidationError(OmniCrawlError):
    code = "template_validation_error"
    suggestion = "模板校验失败；请检查模板占位符是否正确填充，或使用 omnicrawl templates validate 批量检查。"


class LoginFailedError(OmniCrawlError):
    code = "login_failed"
    suggestion = (
        "登录请求被目标拒绝（HTTP 4xx/5xx）。请检查 source.login 的 url、method、"
        "fields、headers 是否正确，以及登录凭据是否仍然有效。"
    )


class ExtractionError(OmniCrawlError):
    """S2.5.33：提取阶段异常——与 fetch 阶段区分，排障方向正确。"""

    code = "extraction_error"
    suggestion = (
        "提取阶段失败（处理器/规则/质量评估）。请检查 extract.fields 选择器与正则，"
        "或运行 omnicrawl sample 试跑验证模板。"
    )


def _safe_message(exc: BaseException) -> str:
    """空 message 兜底（源B P2#65）：异常无消息时退化为类型名，避免输出空串。"""
    message = str(exc).strip()
    return message or exc.__class__.__name__


def describe_error(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, OmniCrawlError):
        return exc.as_info()
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        detail = _safe_message(reason) if isinstance(reason, BaseException) else str(reason or exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return ErrorInfo(
                "tls_verification",
                detail,
                False,
                "HTTPS 证书校验失败；请确认目标证书链有效、服务器时间正确，"
                "或在明确信任的环境中使用 verify_tls=false。",
            )
        return ErrorInfo("network_transient", detail, True, TransientFetchError.suggestion)
    if isinstance(exc, ssl.SSLError):
        return ErrorInfo(
            "tls_error",
            _safe_message(exc),
            True,
            "TLS 握手失败；请检查目标 HTTPS 配置、本地时钟或证书信任设置。",
        )
    if isinstance(exc, PermissionError):
        return ErrorInfo("policy_blocked", _safe_message(exc), False, PolicyBlockedError.suggestion)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorInfo("network_transient", _safe_message(exc), True, TransientFetchError.suggestion)
    if isinstance(exc, KeyError):
        # 源B P2#78：KeyError 消息带键名 + 修复建议，不再只有孤零零的 repr
        key = exc.args[0] if exc.args else ""
        return ErrorInfo(
            "key_error",
            f"缺少必需的键: {key!r}",
            False,
            "请检查配置文件或数据结构是否缺少该字段，或检查键名拼写。",
        )
    return ErrorInfo(type(exc).__name__.lower(), _safe_message(exc), False, "请查看详细日志和失败诊断包。")
