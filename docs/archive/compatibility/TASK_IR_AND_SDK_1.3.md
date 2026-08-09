# Task IR v1、TaskPlan 与内部应用服务

## 输入与无损往返

`TaskIR.from_config` 将配置 v5 转换为 IR；`extensions.config_passthrough` 保存内核或未来版本尚未显式
建模的字段，`to_config` 再以稳定深合并恢复。`recording_fragment`、`api_candidate_fragment` 和
`template_fragment` 使用同一合并契约。高于 v1 的 IR 会拒绝加载，避免静默误解释。

IR 的正式 Schema 是 `docs/TASK_IR_V1_SCHEMA.json`。简单模式仍可使用 `TaskSpec`，然后通过
`TaskIR.from_task_spec` 进入共同底座。

## TaskPlan

`compile_task_plan` 输出：

- 能力需求与缺失能力冲突；
- 网络域名、凭据域名、AI Provider、组件和存储权限；
- 最大页面数、请求数估计和响应字节上界；
- "系统将做什么"的中文解释；
- 对敏感凭据值归一化后的稳定 SHA-256 计划哈希；
- `diff_plans` 字段级差异。

试跑把哈希写入工作区 `plan-bindings.json`。正式运行可要求与最近试跑哈希一致；配置改变时安全拒绝。

## Application Service 内部基线

`ApplicationService` 提供 `load/validate/compile/run/sample/pause/resume/stop/query/export/diff`。事件包含
`category/name/timestamp/payload`，category 预留 stage、progress、record、warning、error、review、
resource、security。返回值只含普通字典、列表、字符串、数字和布尔值；Qt、SQLite 连接和 Pipeline
实例不进入接口。

这是 1.3 内部基线，1.6 才发布公共 SDK Preview；在此之前允许新增接口，但不能破坏配置 v5、插件
API v1、Task IR v1 和计划哈希的确定性规则。
