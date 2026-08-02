from __future__ import annotations

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


class ConfigParseError(OmniCrawlError):
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


def describe_error(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, OmniCrawlError):
        return exc.as_info()
    if isinstance(exc, PermissionError):
        return ErrorInfo("policy_blocked", str(exc), False, PolicyBlockedError.suggestion)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return ErrorInfo("network_transient", str(exc), True, TransientFetchError.suggestion)
    return ErrorInfo(type(exc).__name__.lower(), str(exc), False, "请查看详细日志和失败诊断包。")
