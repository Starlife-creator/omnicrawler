# 插件契约（API v1）

插件是项目目录内的 Python 文件，必须定义 `register(registry)`。加载前会从 AST 读取字面量 `PLUGIN_METADATA` 权限；外部路径和未批准权限默认拒绝。

## 元数据

> **注意**：`PLUGIN_METADATA` 是**静态 dict 字面量**（非 dataclass 实例）。加载器与静态检查器
> 通过 `ast.literal_eval` 解析它，实例形态会被拒。无需 import。

```python
PLUGIN_METADATA = {
    "name": "my-plugin",
    "version": "1.0.0",
    "api_version": 1,
    "description": "...",
    "plugin_types": ("source", "transformer"),
    "capabilities": ("cursor-pagination",),
    "domains": ("example.org",),
    "permissions": ("network",),
    "optional_dependencies": ("vendor-sdk>=1,<2",),
    "license": "MIT",
    "source_url": "https://example.org/source",
    "min_core_version": "1.0.0",
    "fallback": "generic",
    "resource_limits": {"max_concurrency": 4},
}
```

## 注册点

- `register_source(name, factory)`：构造 `factory(config)`；实现 `seed()` 与 `discover(result)`。
- `register_fetcher(name, factory)`：构造 `factory(config, limiter)`；实现 `fetch(request)`。
- `register_parser/register_extractor/register_processor`：构造函数可接收 `(config, options)`、`(config)` 或无参数；processor 实现 `process(FetchResult) -> ProcessResult`。
- `register_auth_provider`：实例可调用或实现 `prepare(CrawlRequest)`，返回请求或 `None`。
- `register_transformer`：实例可调用或实现 `transform(ExtractedRecord)`，返回记录、字典或 `None`。
- `register_exporter`：函数签名 `(config, state, run_id[, options])`；类实现 `export(state, run_id[, options])`。
- `register_hook(event, callback)`：接收事件上下文关键字参数。

## 生命周期

事件：`before_run`、`before_fetch`、`after_fetch`、`after_extract`、`before_export`、`after_export`、`after_run`、`on_error`、`before_reprocess`、`after_reprocess`。

`plugins.hook_fail_open: true` 时单个 hook 异常记录到插件错误，不阻止主流程；认证、主 exporter 和核心 processor 默认失败关闭。

## 兼容与安全

- 名称在同类型内唯一并转为小写。
- `api_version` 必须等于 1；核心版本范围必须包含当前 0.8.0。
- 当前 `plugins.paths` 仅适用于受信任的本地开发插件：它仍在主进程注册，不能当作操作系统级沙箱。未受信插件不得加载；签名子进程插件迁移完成前，请使用最小权限、受限工作区和审计日志。
- 网络能力令牌不会显示在常规对象表示中；插件注册失败时，已签发的令牌会立即撤销，避免部分初始化对象继续持有可用出口权限。
- 插件不应直接读取配置中的明文密钥；使用 `secret://` 或系统 keyring。
- 插件 source/fetcher 不得绕过 ScopePolicy、robots、响应上限和资源守卫。
- 输出必须可 JSON 序列化；证据不得包含 Token/Cookie。
- 插件应提供本地 fixture 集成测试和明确 fallback。
