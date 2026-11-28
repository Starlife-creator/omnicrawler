"""能力契约叶子模块：版本协议、错误码、CapabilityError 与 required_capabilities 校验。

从 plugin_broker.py 迁出（P1-3 第一批）。无反向依赖（不导入任何包内模块），
可独立单测；plugin_broker.py 与 plugin_market_logic.py 等按函数内懒加载导入 validate。
"""
from __future__ import annotations

import re
from typing import Any

# Contract 2 capability protocol versions.  A version changes only when the
# request/response semantics change incompatibly; adding optional fields does
# not bump it.  Plugins discover this mapping through ``system.info`` and may
# declare fail-closed requirements in PLUGIN_METADATA.required_capabilities.
CAPABILITY_VERSIONS: dict[str, int] = {
    "system.info": 1,
    "records.read": 1,
    "records.page": 1,
    "records.write": 1,
    "responses.page": 1,
    "responses.payload": 1,
    "state.get": 1,
    "state.set": 1,
    "state.delete": 1,
    "state.migrate": 1,
    "artifacts.read": 1,
    "artifact.stream.open": 1,
    "artifact.stream.write": 1,
    "artifact.stream.commit": 1,
    "artifact.stream.abort": 1,
    "network.fetch": 1,
    "temp.open": 1,
    "files.read": 1,
    "resources.describe": 1,
    "resources.enumerate": 1,
    "resources.read": 1,
    "render.html.snapshot": 1,
    "render.html.live.start": 1,
    "render.html.live.stop": 1,
    "surface.background.set": 2,
    "surface.background.configure": 2,
    "surface.background.clear": 1,
    "surface.background.capabilities": 1,
    "secrets.get": 1,
    # 2026-09-24 P2.1：受限长文本渲染（声明式视图 rich_text 组件）。
    # 语义：view.describe 的 components 可含 rich_text 段，宿主以受限组件渲染
    # （纯文本、无 HTML、外链确认后打开）。结构化段格式不兼容变更时 bump，
    # 追加新段类型属可选扩展、不 bump（与文件头约定一致）。
    "view.richtext": 1,
    # 2026-09-24 U6：运行进度推送（插件 → 宿主 GUI，单向、可丢弃）。
    # 2026-11-17：**组件式 OCR 代理**（文档工作台插件化时新增）。
    #
    # 为什么必须经宿主代理而不是让插件自己管组件：
    # 组件验签依赖 `configs/component_trust.pub.pem`，租赁依赖跨进程注册表锁，
    # 两者都是**宿主特权**。插件是子进程，一旦让它自己读信任根、拿锁，
    # 等于把安全边界下移到不受控进程里 —— 与「安全与隔离」原则冲突。
    #
    # 语义：插件只交出 PNG 与引擎名，宿主完成验签、租赁、子进程调用，
    # 回传文本与置信度。**插件看不到组件路径、信任根与注册表**。
    "component.ocr.recognize": 1,
    # 语义：运行期插件经 omnicrawler_sdk.call("view.progress", {...}) 上报批处理
    # 进度，宿主合并限速后刷新对应插件面板；面板缺席时静默丢弃。载荷收窄为
    # 已知字段集，新增可选字段不 bump、剔除字段才 bump。
    "view.progress": 1,
    # ★ 2026-11-27：**运行期切换主题**。
    #
    # 为什么必须新增一条能力，而不是复用 `ui:theme`：
    # `ui:theme` 是**加载期**声明（插件在 `register(registry)` 里注册一个
    # ThemeRegistration，由宿主在启动时套用），并且对本地插件**自动批准**
    # （`plugin_loader` 里 `approved |= UI_PERMISSIONS`）。
    # 若让运行期也走它，等于「任何本地插件无需审批即可随时改整个界面的配色」，
    # 那是权限提升，不是功能。
    #
    # 语义：插件请求宿主把**已注册**的某个主题设为当前主题。只接受**已注册的
    # theme_id**（宿主侧白名单），不接受任意色值 —— 插件不能借此渲染任意界面。
    # 载荷：{"theme_id": str}；返回 {"applied": bool, "theme_id": str}。
    # 未注册的 theme_id ⇒ E_RESOURCE（不是静默忽略，否则插件作者无从调试）。
    "theme.apply": 1,
    # ★ 2026-11-28：**前景层**（浮层）。与 surface.background.* 对称的两层：
    #   背景在中央区**下面**，前景在中央区**上面**。
    #
    #   用途：提示条、角标、"正在处理"遮罩这类**展示**需求。
    #   **不做交互** —— 交互走 `view.action`（声明式组件）。
    #
    #   硬约束（由宿主代码结构保证，不是约定）：
    #   · 从结构上盖不住菜单/停靠面板/对话框（overlay 的 parent 是 centralWidget，
    #     而这些控件都不在 centralWidget 里）；
    #   · 同一时刻只有一个活跃前景层（切换时上一个被 hide，不叠层）；
    #   · `reduced_motion` / 高对比下动画暂停（内容保留）；
    #   · 卸载必须 hide + 脱离父级（否则重载会叠出幽灵浮层）。
    "surface.foreground.set": 1,
    "surface.foreground.configure": 1,
    "surface.foreground.clear": 1,
    "surface.foreground.capabilities": 1,
}

_CAPABILITY_REQUIREMENT = re.compile(r"^(?:>=)?([1-9][0-9]*)$")
_SENSITIVE_STATE_KEY = re.compile(
    r"(?:^|[_\-])(?:cookies?|sessions?|tokens?|credentials?|passwords?|storage_state)(?:$|[_\-])",
    re.IGNORECASE,
)


def is_sensitive_state_key(key: object) -> bool:
    """普通插件 state 只能保存游标等业务状态，不得替代宿主凭据库。"""
    return _SENSITIVE_STATE_KEY.search(str(key)) is not None


def validate_required_capabilities(required: dict[str, Any]) -> None:
    """Reject a Contract 2 plugin whose broker protocol cannot satisfy it."""

    for name, raw_requirement in required.items():
        capability = str(name).strip()
        match = _CAPABILITY_REQUIREMENT.fullmatch(str(raw_requirement).strip())
        if not capability or match is None:
            raise ValueError(
                f"能力版本要求非法: {name!r}={raw_requirement!r}（仅支持正整数或 >=正整数）"
            )
        minimum = int(match.group(1))
        available = CAPABILITY_VERSIONS.get(capability)
        if available is None:
            raise ValueError(f"宿主不支持插件要求的能力: {capability}")
        if available < minimum:
            raise ValueError(
                f"宿主能力版本不足: {capability}>={minimum}，当前为 {available}"
            )

# 能力 → 所需 manifest 权限（None = 内置，无需声明）
_CAPABILITY_PERMISSIONS: dict[str, str | None] = {
    "records.read": "records:read",
    "records.page": "records:read",
    "records.write": "records:write",
    "responses.page": "responses:read",
    "responses.payload": "responses:payload",
    "state.get": "state:read",
    "state.set": "state:write",
    "state.delete": "state:write",
    "state.migrate": "state:write",
    "artifacts.read": "artifacts:read",
    "artifact.stream.open": "artifacts:write",
    "artifact.stream.write": "artifacts:write",
    "artifact.stream.commit": "artifacts:write",
    "artifact.stream.abort": "artifacts:write",
    "network.fetch": "network:scoped",
    "temp.open": "temp:write",
    "component.ocr.recognize": "component:ocr",
    "files.read": "files:read",
    "resources.describe": "resources:read",
    "resources.enumerate": "resources:read",
    "resources.read": "resources:read",
    "render.html.snapshot": "render:local",
    "render.html.live.start": "render:scripted",
    "render.html.live.stop": "render:scripted",
    "surface.background.set": "surfaces:background",
    "surface.background.configure": "surfaces:background",
    "surface.background.clear": "surfaces:background",
    "surface.background.capabilities": "surfaces:background",
    # O 例外路径（方案 O2 方案 B）：secrets.get 需 manifest 声明 secrets 白名单；
    # 默认路径是网络经宿主代理密钥零暴露（O2 方案 C），secrets.get 仅显式例外。
    "secrets.get": "secrets:read",
    "system.info": None,
    # U6（2026-09-24）：进度推送是宿主生命周期性质的单向通知，不承载敏感能力，
    # 与 system.info 同为内置（None）——无需 manifest 权限；滥用面由宿主侧
    # 限速合并 + 静默丢弃兜住。
    "view.progress": None,
    # ★ 2026-11-27：运行期换主题需要**显式**权限 `theme:apply`。
    # 刻意**不复用** `ui:theme` —— 那条对本地插件是自动批准的（加载期声明），
    # 若运行期也自动批准，等于「无审批即可随时改整个界面配色」。
    "theme.apply": "theme:apply",
    # ★ 2026-11-28：前景层。复用 `surfaces:background` 同族的独立权限名
    #   `surfaces:foreground` —— 让用户能「允许改背景但拒绝盖前景」：
    #   前景层比背景更接近"遮住我的界面"，风险不同级。
    "surface.foreground.set": "surfaces:foreground",
    "surface.foreground.configure": "surfaces:foreground",
    "surface.foreground.clear": "surfaces:foreground",
    "surface.foreground.capabilities": "surfaces:foreground",
}

E_CONTRACT = "E_CONTRACT"
E_PERMISSION = "E_PERMISSION"
E_QUOTA = "E_QUOTA"
E_RESOURCE = "E_RESOURCE"
E_INTERNAL = "E_INTERNAL"
# Phase 2b J2：data_egress_policy=block 档的共现阻断错误码（C4 权威清单第 73 轮）。
E_EGRESS_BLOCKED = "E_EGRESS_BLOCKED"


class CapabilityError(Exception):
    """带协议错误码的能力代理异常（broker 转成 ok=false 响应）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
