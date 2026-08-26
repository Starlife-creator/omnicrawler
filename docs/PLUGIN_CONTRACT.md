# 插件契约（API v1）

> 本文档是插件的**强制功能契约**。契约 1（register/继承）与契约 2（handle）双契约并存；
> **市场侧自 0.10 起仅接受契约 2**（第 67 轮 P1）。契约 1 存量走退役窗口：0.10 后一个主版本周期，
> 超期仅显式导入的存量可加载。

## 契约形态（第 67 轮 P1）

| 形态 | 入口 | 运行模式 | 说明 |
|---|---|---|---|
| **契约 2**（推荐） | 顶层 `handle(operation, payload) -> dict` | 缺省 `subprocess`（隔离沙箱） | 自包含、无 `import omnicrawler`、能力经 `omnicrawler_sdk` 代理 |
| **契约 1**（legacy） | `register(registry)` | 仅 `in_process`（T3 档最严格批准） | 继承宿主类；不能 subprocess（无宿主注册面） |

契约形态由加载器静态检测（顶层 `handle` → 契约 2；仅 `register` → 契约 1）；两者共存按契约 2。

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
    "domains": ("example.org",),          # network 权限必填（门 1）
    "input_files": ("data/seed.json",),   # files:read 权限必填（门 1，第 50 轮）
    "dependencies": [],                   # 必填（空 [] 合法，第 67 轮 P3）
    "license": "MIT",                     # 必填（门 2 SPDX 白名单）
    "execution_mode": "subprocess",       # subprocess（缺省）| in_process（特权申请）
    "min_core_version": "0.11.0",
    "source_url": "https://example.org/source",
}
```

### execution_mode 语义

- **subprocess（缺省）**：隔离沙箱 + 能力代理（默认路径，无特权申请）。
- **in_process（特权申请）**：进程内运行，走批准矩阵（T1/T2/T3 + 用户确认，无头 fail-closed
  降级 subprocess）；豁免表 `in_process_allowlist` 需 `expires` 必填。**契约 1 一律 T3 最严格档**
  （第 67 轮 P1）。

## 契约 2 能力代理（omnicrawler_sdk）

插件进程内只能经 `omnicrawler_sdk.call(operation, payload)` 访问宿主能力：

| 能力 | 所需权限 | 说明 |
|---|---|---|
| `system.info` | 内置 | 宿主版本/后端/平台 |
| `records.read` / `records.write` | `records:read` / `records:write` | 记录读取（固定 SQL 模板）/写入 |
| `artifacts.read` | `artifacts:read` | 工件清单 |
| `network.fetch` | `network:scoped` | 网络经宿主代理（密钥零暴露默认路径，O2-C）；受 domains + 配额约束 |
| `temp.open` | `temp:write` | 会话临时文件（配额约束） |
| `files.read` | `files:read` | 仅 input_files 白名单内只读（逃逸拒绝，Phase 2b） |
| `secrets.get` | `secrets:read` | **显式例外**（O2-B）：manifest secrets 白名单 + 审计留痕；优先用 auth 注入 |

- **网络密钥零暴露**（O2-C 默认）：`network.fetch` 可用 `auth: {secret_ref, header}`，宿主代理侧
  从密钥库解析注入请求头，插件进程永远看不到明文；注入头值不进审计/日志。
- **secrets.get 为显式例外**（O2-B）：返回明文仅限单次调用，不缓存；调用即审计
  （decision=secret_accessed）。优先使用 auth 注入。
- 未声明权限的越权调用 → `E_PERMISSION`；未知能力 → `E_CONTRACT`。

## 错误码（C4 权威清单，I2 比对源）

| 错误码 | 含义 |
|---|---|
| `E_CONTRACT` | 协议违规（payload 非 dict、未知能力、返回值非对象） |
| `E_PERMISSION` | 未声明权限 / 越权能力 / 路径逃逸 |
| `E_QUOTA` | 会话 temp 配额 / 每日网络配额超限（D4.4） |
| `E_RESOURCE` | 资源不可用（网络失败、插件超时/崩溃） |
| `E_INTERNAL` | 宿主内部错误（含 keyring 配置问题） |
| `E_EGRESS_BLOCKED` | `egress_policy: block` 档下 records.read 后 network.fetch 共现阻断（J2） |
| `E_UNSUPPORTED_ENV` | 沙箱不可用/非受支持环境拒载——运行 `plugins audit --report` 回传（H4） |

## 生命周期（hook 事件，N3 keepalive）

事件：`before_run`、`before_fetch`、`after_fetch`、`after_extract`、`before_export`、
`after_export`、`after_run`、`on_error`、`before_reprocess`、`after_reprocess`。

- 契约 2 插件经 keepalive 长驻会话接收 hook 事件：`handle("hook.<event>", payload)`，
  载荷均为纯数据（N3：hook 放开条件——事件载荷经代理，无宿主对象引用）。
- `plugins.hook_fail_open: true` 时单个 hook 异常记录到插件错误，不阻止主流程；
  认证、主 exporter 和核心 processor 默认失败关闭。

## 环境诊断报告（H4，第 71/72 轮）

`plugins audit --report` 生成脱敏环境诊断报告（零插件明细/零路径/零用户标识）：

- 首行携带 `report_schema: N`（随字段集变更单调递增，H7 语义）。
- **字段白名单**（越界即报错不生成）：`report_schema / os / os_version / kernel /
  python_version / app_version / sandbox_backend / sandbox_available /
  sandbox_detail / sandbox_supported_range / host_exe_present`。
- 用途：E_UNSUPPORTED_ENV 拒载时粘贴至 GitHub Issue 回传（拒载不是死路）。

## 兼容与安全

- 名称在同类型内唯一并转为小写。
- `api_version` 必须等于 1；核心版本范围必须包含当前 0.11.0。
- 契约 2 插件运行于隔离沙箱（-I -S + OS 沙箱探测 fail-closed）；契约 1 的 `plugins.paths`
  仅适用于受信任的本地开发插件——**不能当作操作系统级沙箱**。
- 插件不应直接读取配置中的明文密钥；使用 `secret://`、系统 keyring 或 auth 注入。
- 插件 source/fetcher 不得绕过 ScopePolicy、robots、响应上限和资源守卫。
- 输出必须可 JSON 序列化；证据不得包含 Token/Cookie。
- 插件应提供本地 fixture 集成测试和明确 fallback；发布前跑 `plugins audit --local .` +
  `pytest -m plugin_contract`（F1 本地绿 = CI 绿）。
