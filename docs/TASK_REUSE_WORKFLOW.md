# 公开任务的参数化复用

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

复用现有草案、配置历史、TemplateCatalog 和 CLI，不增加任务协议。首次捕获支持简单公开 HTTP HTML seeds 任务；认证、分页、JSON、API 与插件任务保留本地原配置和历史，不能伪装成已验分享模板。

先审阅任务和样本交付，再显式 capture：

```bash
omnicrawler validate -c task.yaml
omnicrawler plan -c task.yaml
omnicrawler sample -c task.yaml --pages 3
omnicrawler templates capture user/my-list -c task.yaml --acceptance work/task/preflight_acceptance.json -o templates/my-list.yaml
omnicrawler templates render user/my-list --set seed_url_1=https://example.org/new -o new-task.yaml
omnicrawler validate -c new-task.yaml
omnicrawler plan -c new-task.yaml
omnicrawler sample -c new-task.yaml --pages 3
omnicrawler run -c new-task.yaml
```

sample 生成绑定加载配置摘要、当前运行响应哈希/请求指纹、时间、结果与版本的 preflight_acceptance.json；旧摘要、失败或空样本不能 capture。capture 是维护者审阅后的显式保存操作，试跑成功不等于字段语义已人工验收。所有摘要只作历史参考，导入、换参数及跨工作区不会恢复“试跑通过”。版本或内容修改仍需重新试跑。

每个 seeds 网址转换为无默认值的必填参数，避免 URL 查询签名泄露。只保留公开任务允许字段；移除所有请求头、请求正文、AI 配置、代理/登录、数据库、未知扩展设置，保留出口域范围与预算。复杂任务会明确拒绝；原配置及未知 GUI 字段不改。新任务需要的本地上下文由用户重新配置。

可用 --parameters 指定额外 JSON 参数声明，例如：

```json
{"pages":{"path":"crawl.max_pages","type":"integer","required":true,"minimum":1,"maximum":10,"default":3},"title_selector":{"path":"extract.fields.title.selector","type":"string","required":true}}
```

支持范围为 seeds、页数/深度/并发、HTTP 时延/超时和既有字段 CSS/XPath 选择器。保留 attr、all、正则分组和 join 的提取语义；字符串规则转为 selector。附件、自定义提取扩展和未支持的规则属性明确拒绝，不静默丢失语义。每项必须声明类型；路径不能指向请求头或凭据。使用现有参数校验和健康门禁，参数越界或缺失时拒绝。--force 覆盖前保留 ConfigHistory；已有任务 render 也先验证再原子替换。

修改使用现有 templates diff/merge 与配置历史。自然语言只提出可编辑草案，继续沿用已有范围、权限、预算守卫与 GUI crawl_fingerprint；试跑过程中改输入，旧结果只保留为历史。输出/调度由既有独立验证处理。

离线分享使用 templates export-pack/import-pack 的哈希包；公开发布继续走既有签名市场流程。普通导入不等于受信市场发布，不自动批准网络执行。Agent 根据结构化 validate/plan/sample 返回值识别待补项，登录与缺组件按现有预检解释处理；执行、恢复和交付契约见 AGENT_GUIDE.md。

examples/task_reuse/templates/public_list.yaml 包含自建合成 HTML 与完整 expected，模板 metadata 内保存样例、许可、兼容版本与适用限制，正常 render 去除 metadata，不自动执行样例。可在 examples/task_reuse 目录执行 `templates export-pack examples/public-list -o scene.zip`，再在独立目录 import-pack；离线验收复用实际 HTMLProcessor 核对完整记录。包导入限制条目/解压体积、拒绝重复/未列文件、别名 YAML 和空集合；哈希验证成功不代表签名或真实网页验收。

## 2026-10-04 复杂工作流捕获

捕获支持内置 HTML、分页、REST/GraphQL/表单、浏览器动作和附件下载。保留 item_path/path 与分页发现语义；网址、API 正文/查询/变量、动作输入值、认证请求头及会话名生成无默认值必填参数，凭据和登录快照不进入分享模板。下载目录、插件和转换器扩展仍应保留原本地配置；不支持的动作/下载属性明确拒绝。新任务必须 render、validate、plan、sample，不继承历史批准。
