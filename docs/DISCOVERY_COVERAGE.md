# 发现清单与选择性补采

> 适用版本：**0.15.0** · 配置协议：v5 · 维护状态：现行

记录质量只检查已经提取的内容。发现清单另行保存未跟随链接及原因，帮助定位深度上限、主题预过滤、follow_xpath、范围和安全规则造成的候选遗漏。深度边界页仍解析链接，但不会自动继续抓取。

清单中的“未跟随”包含符合配置预期的排除项，不等于确认这些地址都应该抓取。范围、安全规则和 robots 不因召回优先而失效。没有进入页面、API 或来源诊断的隐藏地址仍不可知，`site_coverage` 始终为 `unknown`，不生成虚假的全站覆盖百分比。

## 查看

GUI 的任务工具增加“发现遗漏与补采”：读取清单、查看拒绝原因与父页面、本页勾选、读取下一页。任务完成时若发现仍有缺口，日志会提醒；执行成功与发现完整性分别报告。历史遗漏可能过时，需要按运行 ID 核对。

```powershell
omnicrawler recovery --config task.yaml coverage --limit 100 --offset 0
omnicrawler recovery --config task.yaml coverage --run-id RUN_ID --limit 100 --offset 100
```

`gap_paths_total` 是本次清单路径数量；`known_discovery_gaps` 按子请求指纹去重，同一个链接可以有多个发现父页面。`gap_paths` 包含子链接、父请求指纹、拒绝原因和当前队列状态。已在当前工作区完成的子请求不再列为未解决项。输出明确给出 `truncated` 和 `next_offset`，分页最多每页 1000 项，底层已观察到的路径不会随显示分页截断。

发现决策属于指定运行；`workspace_frontier` 是当前工作区队列，包含待处理、处理中、失败与策略拦截。历史未解决路径单独列出运行标识，避免恢复运行后线索消失。旧运行没有逐链接清单时报告 `unrecorded_discovery_steps`，完整性为未知。来源内部没有提供诊断的过滤仍不可见。

## 补采

先核对原因，必要时调整任务的深度、过滤或允许范围。GUI 在配置变化后需重新打开工具并重新读取。选择清单中的父请求指纹：

```powershell
omnicrawler recovery --config task.yaml retry-discovery --run-id RUN_ID --fingerprint PARENT_FINGERPRINT
omnicrawler run --config task.yaml --resume
```

`retry-discovery` 只将明确选中的 GET 父页面放回队列，不直接抓取被拒绝的子链接。一次最多选择 100 个不同父请求，选择无效、父页面处理中、带请求体或不符合当前范围/深度时，整批拒绝且不改变队列。入队和审计一起提交，原记录与人工编辑不被清除；实际重访继续经过 EgressBroker、robots、范围和资源预算。

本次重访请求正文，避免条件请求的 304 响应导致无法重新发现链接。成功完成发现后移除一次性标记，正常条件请求可以继续使用。人工修订来源产生重提取候选时，也继续发现链接，避免保护人工值同时截断采集路径。

失败抓取沿用 `recovery failures`、`recovery retry-failed`；已有待处理页面沿用 `run --resume`。补采不修改已有导出文件，不自动扩大任务授权范围，不自动判断全站总量。旋转文档、跨页表格和真实业务质量校准属于后续独立工作。

账本不保存敏感请求头。如果脱敏后无法还原父请求身份，补采预检明确拒绝，需用当前配置重建入口；不会报告实际未更新的请求为“已入队”。入队完成也不等于抓取完成，应在恢复运行后重新核对清单。
