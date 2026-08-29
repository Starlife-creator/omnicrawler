# 插件契约（API v1）

> 本文档是插件的**强制功能契约**。市场只接受契约 2（`handle`）。契约 1
>（`register`/继承）仅用于兼容明确导入的旧插件，不得用于新插件或市场投稿。

## 契约形态

| 形态 | 入口 | 运行模式 | 说明 |
|---|---|---|---|
| **契约 2**（推荐） | 顶层 `handle(operation, payload) -> dict` | 缺省 `subprocess`（隔离沙箱） | 自包含、无 `import omnicrawler`、能力经 `omnicrawler_sdk` 代理 |
| **契约 1**（旧版兼容） | `register(registry)` | 仅 `in_process`（最高风险审批档） | 只供明确导入的存量插件；不接受新投稿 |

契约形态由加载器静态检测（顶层 `handle` → 契约 2；仅 `register` → 契约 1）；两者共存按契约 2。

### 当前可用的扩展类型

当前契约 2 加载器只会把 `plugin_types` 包含 `source` 的插件自动注册到执行管线，调用
`handle("source.seed", payload)`。代码库虽然包含 fetcher 子进程适配器，Catalog Schema 也
保留了 fetcher、processor、exporter、auth_provider、parser、extractor、transformer 和 hook
等类别，但这些类别尚未全部接入契约 2 的加载路由。新插件不得仅凭 Schema 枚举宣称未接线
类别已经可运行；发布说明必须与当前实际接入能力一致。

## 元数据

> **注意**：`PLUGIN_METADATA` 是**静态 dict 字面量**（非 dataclass 实例）。加载器与静态检查器
> 通过 `ast.literal_eval` 解析它，实例形态会被拒。无需 import。**运行期权限 ⊆ 静态审批**的前提
> 就是此处静态可读。

```python
PLUGIN_METADATA = {
    "name": "my-plugin",
    "version": "1.0.0",
    "api_version": 1,
    "description": "...",
    "plugin_types": ("source",),
    "permissions": ("records:read", "network:scoped"),
    "domains": ("example.org",),          # network:scoped 权限必填
    "input_files": ("data/seed.json",),   # files:read 权限必填
    "dependencies": [],                   # 必填；没有依赖时使用空列表
    "license": "MIT",                     # 必填，且必须在 SPDX 白名单内
    "execution_mode": "subprocess",       # subprocess（缺省）| in_process（特权申请）
    "min_core_version": "0.11.2",
    "source_url": "https://example.org/source",
}
```

### execution_mode 语义

- **subprocess（缺省）**：隔离沙箱 + 能力代理（默认路径，无特权申请）。
- **in_process（特权申请）**：进程内运行，必须经过风险分级、用户确认和限时豁免；
  `in_process_allowlist` 条目必须包含 `expires`。无交互确认时失败关闭并降级为 subprocess。
  契约 1 一律采用最高风险审批档。

## 契约 2 能力代理（omnicrawler_sdk）

插件进程内只能经 `omnicrawler_sdk.call(operation, payload)` 访问宿主能力：

| 能力 | 所需权限 | 说明 |
|---|---|---|
| `system.info` | 内置 | 宿主版本/后端/平台 |
| `records.read` / `records.write` | `records:read` / `records:write` | 记录读取（固定 SQL 模板）/写入 |
| `artifacts.read` | `artifacts:read` | 工件清单 |
| `network.fetch` | `network:scoped` | 网络经宿主代理；默认不向插件暴露密钥，且受 domains 和配额约束 |
| `temp.open` | `temp:write` | 会话临时文件（配额约束） |
| `files.read` | `files:read` | 仅允许读取 input_files 白名单中的文件，拒绝路径逃逸 |
| `secrets.get` | `secrets:read` | 明文密钥访问的显式例外：需要 manifest 白名单并记录审计；优先用 auth 注入 |

- **网络密钥默认零暴露**：`network.fetch` 可用 `auth: {secret_ref, header}`，宿主代理侧
  从密钥库解析注入请求头，插件进程永远看不到明文；注入头值不进审计/日志。
- **secrets.get 为显式例外**：返回明文仅限单次调用，不缓存；调用即审计
  （decision=secret_accessed）。优先使用 auth 注入。
- 未声明权限的越权调用 → `E_PERMISSION`；未知能力 → `E_CONTRACT`。

## 错误码（C4 权威清单，I2 比对源）

| 错误码 | 含义 |
|---|---|
| `E_CONTRACT` | 协议违规（payload 非 dict、未知能力、返回值非对象） |
| `E_PERMISSION` | 未声明权限 / 越权能力 / 路径逃逸 |
| `E_QUOTA` | 会话临时文件配额或每日网络配额超限 |
| `E_RESOURCE` | 资源不可用（网络失败、插件超时/崩溃） |
| `E_INTERNAL` | 宿主内部错误（含 keyring 配置问题） |
| `E_EGRESS_BLOCKED` | `egress_policy: block` 下读取记录后又请求网络，被策略阻断 |
| `E_UNSUPPORTED_ENV` | 当前环境不受支持；运行 `plugins audit --report` 生成诊断报告 |

## 生命周期与 hook 事件

事件：`before_run`、`before_fetch`、`after_fetch`、`after_extract`、`before_export`、
`after_export`、`after_run`、`on_error`、`before_reprocess`、`after_reprocess`。

- 契约 2 插件经 keepalive 长驻会话接收 hook 事件：`handle("hook.<event>", payload)`。
  事件载荷必须经过代理并保持为纯数据，不得包含宿主对象引用。
- `plugins.hook_fail_open: true` 时单个 hook 异常记录到插件错误，不阻止主流程；
  认证、主 exporter 和核心 processor 默认失败关闭。

## 环境诊断报告

`plugins audit --report` 生成脱敏环境诊断报告（零插件明细/零路径/零用户标识）：

- 首行携带 `report_schema: N`；字段集合变化时该版本号单调递增。
- **字段白名单**（越界即报错不生成）：`report_schema / os / os_version / kernel /
  python_version / app_version / sandbox_backend / sandbox_available /
  sandbox_detail / sandbox_supported_range / host_exe_present`。
- 用途：E_UNSUPPORTED_ENV 拒载时粘贴至 GitHub Issue 回传（拒载不是死路）。

## 兼容与安全

- 名称在同类型内唯一并转为小写。
- `api_version` 必须等于 1；核心版本范围必须包含当前 0.11.2。
- 契约 2 插件默认运行于独立子进程，并使用 `-I -S`、环境白名单、能力代理和资源限制。
  当前 OS 级 confinement 仍是未来能力；环境探测只提供诊断，不能把现有边界描述为完整的
  AppContainer、seccomp 或 Landlock 沙箱。契约 1 的 `plugins.paths` 仅适用于受信任的本地
  旧插件，更不能当作操作系统级沙箱。
- 插件不应直接读取配置中的明文密钥；使用 `secret://`、系统 keyring 或 auth 注入。
- 插件 source/fetcher 不得绕过 ScopePolicy、robots、响应上限和资源守卫。
- 输出必须可 JSON 序列化；证据不得包含 Token/Cookie。
- 插件应提供本地 fixture 集成测试和明确 fallback；发布前运行 `plugins audit --local .` 和
  `pytest -m plugin_contract`。本地与 CI 使用同一套契约检查。
