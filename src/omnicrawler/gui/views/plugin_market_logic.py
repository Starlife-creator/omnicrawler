"""插件市场面板的纯逻辑：目录条目解析、权限风险分级、版本兼容性裁决与安装审查文案。

从 plugin_market.py 迁出（P1-3 第一批）。无 Qt 依赖，可独立单测；
 heavyweight 依赖（plugin_broker / egress / AppConfig）保持函数内懒加载。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..._version import __version__
from ...plugins.plugins import OFFICIAL_PLUGIN_TYPES
from ..i18n import _


def _project_root_of(base: str | Path | None) -> Path:
    if base:
        return Path(base)
    # src/omnicrawler/gui/views/plugin_market_logic.py -> 上溯 4 级到项目根（与原模块同目录）
    return Path(__file__).resolve().parents[4]


_CATALOG_PURPOSE = "plugin"

_TYPE_LABELS = {
    "source": _("数据源"),
    "fetcher": _("抓取器"),
    "processor": _("处理器"),
    "parser": _("解析器"),
    "extractor": _("提取器"),
    "auth_provider": _("认证"),
    "transformer": _("转换器"),
    "exporter": _("导出器"),
    "hook": _("生命周期"),
    "ui": _("原生界面"),
    "resource_provider": _("本地资源"),
    "view": _("声明式界面"),
}


def _entry_strings(entry: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = entry.get(key, [])
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _entry_plugin_types(entry: dict[str, Any]) -> tuple[str, ...]:
    """读取运行扩展点；旧 catalog 从 category/tags 做保守兼容推断。"""
    raw = entry.get("plugin_types")
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip().casefold() for item in raw]
        return tuple(dict.fromkeys(item for item in values if item in OFFICIAL_PLUGIN_TYPES))
    candidates = [entry.get("category"), *_entry_strings(entry, "tags")]
    inferred = [str(item).strip().casefold() for item in candidates]
    return tuple(dict.fromkeys(item for item in inferred if item in OFFICIAL_PLUGIN_TYPES))


def _permission_risk(entry: dict[str, Any]) -> tuple[str, str]:
    permissions = {
        str(item).strip().casefold()
        for item in _entry_strings(entry, "permissions")
        if str(item).strip()
    }
    if (
        str(entry.get("execution_mode") or "subprocess") == "in_process"
        or permissions & {"secrets:read", "responses:payload", "render:scripted"}
    ):
        return "high", _("高风险")
    if permissions & {
        "network:scoped",
        "records:write",
        "responses:read",
        "artifacts:write",
        "files:read",
        "temp:write",
        "resources:read",
        "surfaces:background",
        "render:local",
    }:
        return "medium", _("需授权")
    return "low", _("低风险")


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".") if part.isdigit())


def _compatibility(entry: dict[str, Any], current: str = __version__) -> tuple[str, str]:
    """覆盖市场现用的简单版本约束；无法判断时明确显示未知而不误拦截。"""
    from ...plugins.plugin_broker import validate_required_capabilities

    required_capabilities = entry.get("required_capabilities", {})
    if not isinstance(required_capabilities, dict):
        return "blocked", _("能力版本声明无效")
    try:
        validate_required_capabilities(required_capabilities)
    except ValueError as exc:
        return "blocked", _("宿主能力不兼容：") + str(exc)
    constraint = str(entry.get("compatible_core") or "").strip()
    if not constraint:
        return "unknown", _("兼容性未知")
    current_version = _version_tuple(current)
    if not current_version:
        return "unknown", _("兼容性未知")
    for clause in (item.strip() for item in constraint.split(",")):
        matched = False
        for operator in (">=", "<=", "==", ">", "<"):
            if not clause.startswith(operator):
                continue
            target = _version_tuple(clause[len(operator):].strip())
            if not target:
                return "unknown", _("兼容性未知")
            comparisons = {
                ">=": current_version >= target,
                "<=": current_version <= target,
                "==": current_version == target,
                ">": current_version > target,
                "<": current_version < target,
            }
            if not comparisons[operator]:
                return "incompatible", _("不兼容当前版本")
            matched = True
            break
        if not matched:
            return "unknown", _("兼容性未知")
    return "compatible", _("兼容")


def _reviewed(entry: dict[str, Any]) -> bool:
    """「已审核」＝维护者复签通过（M5 裁定：已审核＝流程状态，作者可以是任何人）。

    数据源：catalog 条目声明的维护者包签名文件（市场 CI 会校验其存在与有效）。
    ★ 「官方」徽章（作者身份维度）**暂缺**：需要市场侧发布「官方认证作者」数据源
      才能落地，客户端不能自己发明 —— 已登记《审查记录》§10.4。
    """
    return bool(str(entry.get("maintainer_package_signature_file") or "").strip())


def _installed_permissions(plugin_dir: Path) -> list[str]:
    """读取已安装插件声明的权限（AST 静态读，**不执行代码**）。

    ★ 安装元数据（install meta）只有哈希不含权限 ⇒ 只能从插件本体的
      PLUGIN_METADATA 取（复用 plugin_audit 的静态提取器）。
    """
    from ...plugins.plugin_audit import _extract_static_metadata

    meta = _extract_static_metadata(plugin_dir)
    if not meta:
        return []
    return [str(item).strip() for item in meta.get("permissions", []) or [] if str(item).strip()]


def _permission_diff(previous: list[str], entry: dict[str, Any]) -> dict[str, list[str]]:
    """更新前后的权限差异（§4.6：更新扩大权限必须重新呈现并处理授权）。

    「扩权」判据＝**新增**权限（删除不算扩权——收窄权限不需要用户重新授权）。
    比较大小写不敏感（权限名按 casefold 归一），展示保留原写法。
    """
    old = {item.casefold() for item in previous}
    new_items = [item for item in _entry_strings(entry, "permissions")]
    new_folded = {item.casefold() for item in new_items}
    return {
        "added": [item for item in new_items if item.casefold() not in old],
        "removed": [item for item in previous if item.casefold() not in new_folded],
    }


def _permission_diff_text(diff: dict[str, list[str]]) -> str:
    lines: list[str] = []
    if diff.get("added"):
        lines.append(_("新增权限：") + ", ".join(diff["added"]))
    if diff.get("removed"):
        lines.append(_("移除权限：") + ", ".join(diff["removed"]))
    return "\n".join(lines)


def _badges(entry: dict[str, Any]) -> tuple[str, ...]:
    """卡片与详情的徽章（M5：官方与已审核是**两个维度、非互斥**，不得做成二选一筛选）。"""
    badges: list[str] = []
    if _reviewed(entry):
        badges.append(_("已审核"))
    if _permission_risk(entry)[0] == "high":
        badges.append(_("高权限"))
    return tuple(badges)


def _install_block_reason(entry: dict[str, Any]) -> str:
    compatibility, detail = _compatibility(entry)
    if compatibility in {"incompatible", "blocked"}:
        return detail
    if "ui" in _entry_plugin_types(entry):
        return _("原生 UI 插件仅允许作为受信任本地插件使用，不能从市场安装")
    return ""


# ---------------------------------------------------------------------------
# 安装失败原因链（P0：安装失败给「原因链」而非原始异常）
#
# 由来：`_on_install_error` 此前只取异常**第一行**塞进一个会消失的 Toast ——
# market_client 在明确的阶段抛**有语义**的异常（PermissionError=验签/安全校验、
# FileNotFoundError=读取、KeyError=目录条目、ValueError=解析），但用户永远看不到
# 「卡在哪一步、为什么、该做什么」，也拿不到可复制的细节。
#
# 判据是**确定性**的：按 market_client 各阶段抛出的异常类型与文案前缀归类；
# 并沿 `__cause__` / `__context__` 走完整链路（网络错误常被包在最外层）。
# ---------------------------------------------------------------------------

_INSTALL_FAILURE_RULES: tuple[tuple[str, str, str, str], ...] = (
    # (判据子串, 阶段, 人话结论, 可行动建议)
    ("非法插件 ID", "参数校验", _("插件 ID 不合法"), _("通常是目录数据问题；刷新目录后重试，若仍出现请反馈")),
    ("无法读取资源", "下载/读取", _("无法读取市场资源"), _("检查网络与代理设置后重试；离线环境请用本地安装路径")),
    ("catalog.json 签名校验失败", "目录验签", _("市场目录签名校验失败"), _("fail-closed 安全行为：确认市场来源可信后再试；不要尝试绕过")),
    ("catalog.json 解析失败", "目录解析", _("市场目录格式无效"), _("市场侧数据可能已损坏，稍后刷新重试")),
    ("缺少 plugins 数组", "目录解析", _("市场目录缺少插件清单"), _("市场侧数据可能已损坏，稍后刷新重试")),
    ("catalog 中无此插件", "目录条目", _("目录中已无此插件"), _("刷新目录后重新选择；该插件可能已被下架")),
    ("市场 package manifest", "包校验", _("插件包清单校验失败"), _("fail-closed 安全行为：包与目录声明不一致，请反馈给发布者")),
    ("市场包", "包校验", _("插件包校验失败"), _("fail-closed 安全行为：包与目录声明不一致，请反馈给发布者")),
    ("签名校验失败", "插件验签", _("插件签名校验失败"), _("fail-closed 安全行为：包可能被篡改；不要重试绕过，请反馈给发布者")),
    ("下载校验失败", "下载哈希校验", _("下载内容与目录不符"), _("fail-closed 安全行为：包可能被篡改或已过期；刷新目录后重试")),
)

_STAGE_RULES: tuple[tuple[type[BaseException], str, str, str], ...] = (
    (PermissionError, "安全校验", _("安全校验未通过"), _("fail-closed 安全行为：安装被拒绝；确认来源可信，不要尝试绕过")),
    (KeyError, "目录条目", _("目录中找不到该插件"), _("刷新目录后重新选择")),
    (FileNotFoundError, "下载/读取", _("无法读取市场资源"), _("检查网络与代理设置后重试")),
    (TimeoutError, "网络", _("连接市场超时"), _("检查网络后重试；离线环境请用本地安装路径")),
    (ConnectionError, "网络", _("连接市场失败"), _("检查网络后重试；离线环境请用本地安装路径")),
    (ValueError, "解析/格式", _("市场数据解析失败"), _("市场侧数据可能无效，稍后刷新重试")),
)


def _classify_failure_text(message: str) -> dict[str, str] | None:
    for needle, stage, summary, advice in _INSTALL_FAILURE_RULES:
        if needle in message:
            return {"stage": stage, "summary": summary, "advice": advice}
    return None


def _classify_failure_type(exc: BaseException) -> dict[str, str] | None:
    for exc_type, stage, summary, advice in _STAGE_RULES:
        if isinstance(exc, exc_type):
            return {"stage": stage, "summary": summary, "advice": advice}
    return None


def install_failure_chain(exc: BaseException) -> dict[str, Any]:
    """把安装异常整理成**结构化原因链**（无 Qt 依赖，可独立单测）。

    返回::

        {
          "stage": "插件验签",                # 最外层归类到的阶段
          "summary": "插件签名校验失败",       # 一句人话结论
          "advice": "……",                    # 可行动建议
          "detail": "……",                    # 完整链路的可复制文本
          "chain": [                          # 每一环：类型 + 消息
            {"type": "PermissionError", "message": "..."},
            ...
          ],
        }
    """
    chain: list[dict[str, str]] = []
    links: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        links.append(current)
        chain.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or current.__context__
    detail = "\n".join(f"{index + 1}. {item['type']}: {item['message']}" for index, item in enumerate(chain))

    # 分类顺序：**文本规则最具体**（逐环找，外层优先）；其次**类型规则由内向外**取根因
    # —— 否则「外层 ValueError 包装内层 TimeoutError」会被误判成「解析失败」而非「网络」。
    classified: dict[str, str] | None = None
    for link in links:
        classified = _classify_failure_text(str(link))
        if classified is not None:
            break
    if classified is None:
        for link in reversed(links):
            classified = _classify_failure_type(link)
            if classified is not None:
                break
    if classified is None:
        classified = {
            "stage": _("安装"),
            "summary": str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__,
            "advice": _("查看详细信息确认原因；可重试一次，若仍失败请附带详情反馈"),
        }
    return {
        "stage": classified["stage"],
        "summary": classified["summary"],
        "advice": classified["advice"],
        "detail": detail or type(exc).__name__,
        "chain": chain,
    }


def parse_install_failure(message: str) -> dict[str, Any] | None:
    """解析 worker 的失败信号：结构化原因链（JSON）或旧格式纯文本。"""
    import json as _json

    text = (message or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = _json.loads(text)
    except _json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) and "chain" in payload else None


def _install_review_text(entry: dict[str, Any]) -> str:
    plugin_types = _entry_plugin_types(entry)
    type_text = ", ".join(_TYPE_LABELS.get(item, item) for item in plugin_types) or _("未知")
    mode = str(entry.get("execution_mode") or "subprocess")
    mode_text = _("隔离子进程") if mode == "subprocess" else _("进程内（高风险）")
    permissions = list(_entry_strings(entry, "permissions"))
    domains = list(_entry_strings(entry, "domains"))
    return _(
        "插件：{0}\n运行扩展点：{1}\n执行模式：{2}\n请求权限：{3}\n允许域名：{4}\n\n"
        "安装仅下载并验签；启用这些权限时仍需在项目插件管理中逐项批准。"
    ).format(
        entry.get("name") or entry.get("id") or "—",
        type_text,
        mode_text,
        ", ".join(permissions) if permissions else _("无"),
        ", ".join(domains) if domains else _("无"),
    )


def _market_egress(project_root: Path, app_config: Any | None = None) -> Any:
    """Lazily build a shared EgressBroker for curated plugin-market traffic.

    The marketplace downloads third-party signed plugins; those requests must
    cross the same policy/budget/audit boundary as every other network egress,
    not ride a raw urllib call.

    提供 ``app_config`` 时**以用户项目配置为基准**（#74 §1/§4）：市场的
    ``http.proxy``／``allow_private_network``／``egress.*`` 与任务抓取同源，
    不再读死内置 ``DEFAULTS``。只强制打开 ``egress.audit`` —— 第三方插件下载
    始终留痕，这一条不交给用户配置决定。
    """
    import copy

    from ...core.config import DEFAULTS, AppConfig, deep_merge
    from ...security.egress import EgressBroker

    if app_config is not None and hasattr(app_config, "section"):
        raw = copy.deepcopy(app_config.raw)
        egress_cfg = raw.setdefault("egress", {})
        if isinstance(egress_cfg, dict):
            egress_cfg["audit"] = True
        return EgressBroker(
            AppConfig(app_config.path, app_config.root, raw, app_config.workspace)
        )

    # 无项目配置（独立打开市场/测试）时退回内置默认：它只限制私网目标并记账。
    raw = deep_merge(dict(DEFAULTS), {"egress": {"audit": True}})
    raw.setdefault("project", {"name": "plugin-market", "workspace": str(project_root)})
    config = AppConfig(Path("<plugin-market>"), project_root, raw, project_root)
    return EgressBroker(config)
