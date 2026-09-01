# `plugins/`：本地插件工作目录

本目录用于存放你正在开发或通过第三方分享导入的插件，不是市场正式发布目录。

## 与其他目录的区别

| 目录 | 用途 | 内容来源 |
|---|---|---|
| `plugins/` | 本地开发和私下分享插件 | 当前用户 |
| `plugins_installed/` | 从市场验签安装的插件 | 市场客户端 |
| `examples/plugins/` | 示例和测试素材 | 项目仓库 |
| `market/` | 随应用提供的离线市场快照 | 发布流程 |

## 新插件的推荐形态

新插件必须采用[插件契约 2](../docs/PLUGIN_CONTRACT.md)：

- 顶层入口为 `handle(operation, payload) -> dict`；
- `PLUGIN_METADATA` 是可由 AST 静态读取的 `dict` 字面量；
- 默认 `execution_mode` 为 `subprocess`；
- 不导入 `omnicrawler`，宿主能力统一通过 `omnicrawler_sdk.call(...)` 请求；
- 权限、域名、输入文件和依赖必须完整且最小化声明。

需要市场界面的插件使用契约 2 `view` 和宿主固定组件；资源目录使用 `resource_provider` 与用户授权
句柄，背景媒体使用宿主 `surface.background.*`。唯一例外是明确受信任、只在本机加载的原生 UI
扩展；它使用契约 1 且不得投稿公共市场。
契约 2 插件如果依赖特定代理协议，应声明 `required_capabilities`，持久状态则声明
`state_schema_version`，不要自行打开数据库或隐藏状态文件。

生成脚手架：

```powershell
python -m omnicrawler.cli plugins scaffold-contract2 `
  --plugin-id my_plugin `
  --display-name "My Plugin" `
  --output-dir plugins
```

## 开发与验证

实现插件后，在仓库根目录运行：

```powershell
python -m omnicrawler.cli plugins audit --local plugins\my_plugin
python -m pytest -m plugin_contract
```

审计会核对静态元数据、`plugin.yaml`、依赖、权限、域名、输入文件、许可和运行环境。
未通过审计的插件不应签名或分享。

## 完成并签名

创作者签名覆盖整个插件目录，而不只是 `plugin.py`。签名包至少包括：

- `plugin.py`、`plugin.yaml` 和 `listing.md`；
- `creator.identity`；
- `package.manifest.json`；
- `package.manifest.creator.sig`。

完成签名后，该文件夹已经可以私下分享。接收方必须先验证整包签名，再查看创作者指纹、
权限和域名并明确确认。私下分享不代表市场审核，也不会自动信任该作者未来发布的其他插件。

## 投稿市场

投稿是签名后的可选步骤。应用会把同一份创作者签名包放入市场仓库的
`submissions/plugins/<creator_fingerprint>/<plugin_id>/`，贡献者不能直接修改正式
`plugins/`、`authors/`、`catalog.json` 或维护者签名。

投稿前必须填写 `listing.md` 并明确接受 DCO。正式发布还需要 CI 静态检查、人工审核、
维护者对同一份 manifest 复签以及已签名 catalog 收录。

完整流程见[插件作者指南](../docs/AUTHOR_GUIDE.md)和
[市场生态与分发协议](../docs/MARKET_ECOSYSTEM.md)。

## 安全边界

- 不要把私钥、Token、Cookie 或真实凭据放进插件目录；
- 网络访问必须声明 `network:scoped` 和精确 `domains`；
- 文件读取必须声明 `files:read` 和精确 `input_files`；
- 用户选择的资源目录使用 `resources:read` 不透明句柄，不得把绝对路径传入插件；
- 背景表面使用 `surfaces:background`；本地 HTML 快照使用 `render:local`，脚本模式另需
  `render:scripted` 高风险授权；
- 响应元数据与正文分别使用 `responses:read`、`responses:payload`；正文权限属于高风险；
- 导出使用 `artifact.stream.*` 与 `artifacts:write`，不要依赖宿主真实输出路径；
- 跨运行状态使用 `state:*`，状态迁移必须显式且不得覆盖非空目标；
- 优先使用宿主认证注入，避免插件进程接触明文密钥；
- 权限扩大必须重新获得用户确认；
- 未签名、签名不符、文件集合不符或越权的插件应失败关闭。
