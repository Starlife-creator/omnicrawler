# 插件契约（API v1）

> 本文档是插件的**强制功能契约**。市场只接受契约 2（`handle`）。契约 1
>（`register`/继承）仅用于明确受信任的本地原生 UI 等进程内扩展，不接受公共市场投稿。

## 契约形态

| 形态 | 入口 | 运行模式 | 说明 |
|---|---|---|---|
| **契约 2**（推荐） | 顶层 `handle(operation, payload) -> dict` | 缺省 `subprocess`（隔离沙箱） | 自包含、无 `import omnicrawler`、能力经 `omnicrawler_sdk` 代理 |
| **契约 1**（本地特权） | `register(registry)` | 仅 `in_process`（最高风险审批档） | 仅供明确受信任的本地扩展；不接受公共市场投稿 |

契约形态由加载器静态检测（顶层 `handle` → 契约 2；仅 `register` → 契约 1）；两者共存按契约 2。

### 当前可用的扩展类型

当前契约 2 加载器会自动接入：

- `source` → `handle("source.seed", payload)`；
- `fetcher` → `handle("fetcher.fetch", payload)`；
- `processor` → `handle("processor.process", payload)`；
- `parser` → `handle("parser.process", payload)`；
- `extractor` → `handle("extractor.process", payload)`；
- `auth_provider` → `handle("auth.prepare", payload)`；
- `transformer` → `handle("transformer.transform", payload)`；
- `exporter` → `handle("exporter.export", payload)`；
- `hook` → `handle("hook.<event>", payload)`。
- `resource_provider` → `handle("resource.inventory"|"resource.action", payload)`；
- `view` → `handle("view.describe"|"view.action", payload)`，由宿主渲染固定组件。

`ui` 专指原生 QWidget/QSS/绘制回调，不能跨进程序列化，只允许明确受信任的本地契约 1 插件
使用。公共市场的界面使用契约 2 `view`：插件只返回数据描述，不能提供宿主对象或执行 GUI 代码。
未知运行类型仍会报错；业务分类必须写入 `category/tags`。

本地 UI 插件应优先使用声明式宿主扩展点。`register_background(...)` 只登记 ID、名称和有界默认值；
目录选择、格式白名单、扫描上限、Qt 绘制和播放器生命周期均由应用本体控制，不接受插件提供
QWidget、绘制器或播放器回调。确实需要自定义 QWidget 时才使用 `register_ui_panel`，并维持最高
风险提示。

市场 `view` 当前只允许 label、button、directory_picker、slider、select 和 resource_list。面板可在
宿主允许的左、右、底部区域移动、浮动和调整尺寸；插件不能覆盖核心菜单、中央工作区或安全提示。
目录选择返回插件会话专属的不透明句柄。媒体背景由 `surface.background.*` 控制，不暴露 QWidget；
v2 背景表面是语义化底层槽位：插件可以选择 `application/workspace/canvas` 范围、适配方式、
宿主预设、背景可见度、前景面板不透明度、遮罩和有界静态模糊，但不能控制 Qt 层级。宿主强制
输入穿透，菜单、核心操作和安全对话框始终位于不透明前景；同一时刻每个窗口只有一个活动背景。
本地 HTML 默认经 `render.html.snapshot` 在独立 Chromium context 中转为 PNG，断网并禁用脚本。
`render.html.live.start` 可生成宿主轮询的受限 PNG 帧流，但另需 `render:scripted`，且单插件单流、
最高 5 FPS/1920×1080；两种模式都禁止外部网络、下载、服务工作线程和页面交互。
相对 CSS、JS、图片等子资源只能由宿主从同一授权句柄代理，单资源、数量和总字节均有上限；
绝对路径、符号链接、目录联接和授权目录逃逸失败关闭。

### processor / exporter 返回约定

- `processor.process` 接收 `{"result": FetchResult字典, "options": {...}}`，返回
  `{"records": [...], "requests": [...], "artifact_path": null}`。每条 record 使用
  `source_url / record_type / data / evidence`；响应正文使用 `body_b64`。宿主会移除原始请求体，
  并脱敏 Authorization、Cookie、X-Api-Key 等认证头。
- `exporter.export` 接收 `{"run_id": "...", "options": {...}}` 并返回 JSON 对象。需要读取记录
  时声明并审批 `records:read`，优先通过 `omnicrawler_sdk.call("records.page", ...)` 分页获取；宿主不会
  把 StateStore 或工作区路径直接传入插件进程。
- `parser.process` / `extractor.process` 与 processor 使用相同返回结构；
  `auth.prepare` 返回 `{"request": CrawlRequest字典}` 或 null；`transformer.transform` 返回
  `{"record": 完整记录}`、`{"data": 新数据}` 或空对象（保持原记录）。
  auth 输入中的既有认证头会被脱敏，原始请求体只提供大小与 SHA-256；宿主合并返回值时会保留
  未被插件替换的原认证头和请求体。

`plugin_types` 只描述运行扩展点，不能由开发者自由命名。自由业务分类使用 `category`，检索词
使用 `tags`，自定义能力使用带命名空间的 `capabilities`。

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
    "required_capabilities": {"records.page": ">=1"},
    "state_schema_version": 1,
    "domains": ("example.org",),          # network:scoped 权限必填
    "input_files": ("data/seed.json",),   # files:read 权限必填
    "dependencies": [],                   # 必填；没有依赖时使用空列表
    "license": "MIT",                     # 必填，且必须在 SPDX 白名单内
    "execution_mode": "subprocess",       # subprocess（缺省）| in_process（特权申请）
    "min_core_version": "0.12.0",
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
| `records.read` / `records.write` | `records:read` / `records:write` | 兼容性记录读取（最多 1000 条）/写入 |
| `records.page` | `records:read` | 当前运行记录的稳定分页；游标不透明、单次使用 |
| `responses.page` | `responses:read` | 响应元数据分页；不暴露数据库 ID 与归档路径 |
| `responses.payload` | `responses:payload` | 通过 `response_ref` 有界读取归档正文；独立高风险权限 |
| `artifacts.read` | `artifacts:read` | 工件清单 |
| `artifact.stream.open/write/commit/abort` | `artifacts:write` | 不透明分块工件流；插件看不到路径，不能覆盖文件 |
| `state.get` | `state:read` | 读取插件私有状态键 |
| `state.set/delete/migrate` | `state:write` | 写入、删除或显式复制迁移私有状态 schema |
| `network.fetch` | `network:scoped` | 网络经宿主代理；默认不向插件暴露密钥，且受 domains 和配额约束 |
| `temp.open` | `temp:write` | 会话临时文件（配额约束） |
| `files.read` | `files:read` | 仅允许读取 input_files 白名单中的文件，拒绝路径逃逸 |
| `resources.describe/enumerate/read` | `resources:read` | 访问用户明确授予的会话目录句柄；有扫描、深度、数量和读取大小上限 |
| `render.html.snapshot` | `render:local`；脚本模式另需 `render:scripted` | 独立、断网 Chromium 把本地 UTF-8 HTML 转成不透明 PNG 结果 |
| `render.html.live.start/stop` | `render:scripted` | 断网脚本页转为有界 PNG 帧流；单插件单流，停止/卸载时回收 Chromium |
| `surface.background.set/configure/clear` | `surfaces:background` | 控制宿主语义底层背景；仅接受已授权媒体或宿主渲染结果，v2 配置范围/预设/透明前景/遮罩/静态模糊 |
| `surface.background.capabilities` | `surfaces:background` | 查询宿主允许的范围、预设、数值边界和输入穿透保证 |
| `secrets.get` | `secrets:read` | 明文密钥访问的显式例外：需要 manifest 白名单并记录审计；优先用 auth 注入 |

- **网络密钥默认零暴露**：`network.fetch` 可用 `auth: {secret_ref, header}`，宿主代理侧
  从密钥库解析注入请求头，插件进程永远看不到明文；注入头值不进审计/日志。
- **secrets.get 为显式例外**：返回明文仅限单次调用，不缓存；调用即审计
  （decision=secret_accessed）。优先使用 auth 注入。
- 未声明权限的越权调用 → `E_PERMISSION`；未知能力 → `E_CONTRACT`。
- `system.info.capability_versions` 返回宿主协议版本表。插件用静态
  `required_capabilities` 声明最低版本（正整数或 `>=正整数`）；不满足时在启动插件代码前拒载。
- 状态命名空间绑定项目、插件 ID、作者指纹与 `state_schema_version`，不绑定插件版本或载荷哈希。
  因此正常升级可延续状态，换作者不会继承；schema 升级需调用 `state.migrate` 明确迁移，目标非空
  时拒绝覆盖。单值最大 64 KiB。
- 工件流单块最大 1 MiB、单会话最多同时打开 8 个；未提交、超限或异常流会由宿主删除。

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

- 契约 2 插件在注册表生命周期内复用隔离会话接收 hook 事件：
  `handle("hook.<event>", payload)`。事件载荷经过宿主序列化，只包含纯 JSON 数据；pipeline 等
  宿主对象不会跨边界，请求认证头会脱敏，响应正文只提供哈希和大小摘要。
- `before_fetch` hook 只能返回建议。目前宿主认可
  `{"fetch_advice":{"action":"conditional_revalidate"|"force_fetch","reason":"..."}}`；
  冲突、未知或畸形建议会被忽略。条件头只能从宿主 StateStore 取得，插件不能跳过请求、注入
  URL/请求头，且 ScopePolicy、robots 与重定向校验仍在建议之后执行。
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
- `api_version` 必须等于 1；核心版本范围必须包含当前 0.12.0。
- 契约 2 插件默认运行于独立子进程，并使用 `-I -S`、环境白名单、能力代理和资源限制。
  当前 OS 级 confinement 仍是未来能力；环境探测只提供诊断，不能把现有边界描述为完整的
  AppContainer、seccomp 或 Landlock 沙箱。契约 1 的 `plugins.paths` 仅适用于受信任的本地
  旧插件，更不能当作操作系统级沙箱。
- 插件不应直接读取配置中的明文密钥；使用 `secret://`、系统 keyring 或 auth 注入。
- 插件 source/fetcher 不得绕过 ScopePolicy、robots、响应上限和资源守卫。
- 输出必须可 JSON 序列化；证据不得包含 Token/Cookie。
- 插件应提供本地 fixture 集成测试和明确 fallback；发布前运行 `plugins audit --local .` 和
  `pytest -m plugin_contract`。本地与 CI 使用同一套契约检查。
