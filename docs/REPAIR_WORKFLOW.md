# 本地证据驱动的提取修复

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

生产入口为 `omnicrawler repair preview/apply/observe/rollback`，继续使用 LLMCandidateGenerator、生产 HTMLProcessor、L0–L3、ObservationStore 与 ConfigHistory。页面不会触发无限模型重试，默认无网络；命令明确选择 --generate 时才使用配置的 AI，并检查 allow_page_text。只向模型发送训练快照，留出和历史样本留在本地确定性比较。

证据 JSON 为格式 1，含 training 对象、current 留出列表、historical 已验收历史列表。每个样本含 sample_id、url、html、expected；expected 为生产提取器完整记录列表。训练样本可以没有 expected。身份和正文不得在三组中重复；列表必须非空。标注须由人工/已核验交付提供，不能取模型自评分。

本地候选 JSON 仅需 `{"field":"title","rule_type":"css","new_rule":".new-title"}`。旧规则从配置读取，置信度和观察轮次不能由该文件自报。只有完整留出结果改善、无额外误匹配且旧新历史结果都符合标注时才可应用；无证据只生成预览。LLM 支持强度依据留出标注完整匹配的记录身份，用原有 candidate_rule 公式计算，不提高既有置信阈值。

```bash
omnicrawler repair preview -c task.yaml --evidence evidence.json --candidate candidate.json
omnicrawler repair apply -c task.yaml --evidence evidence.json --candidate candidate.json
omnicrawler repair observe -c task.yaml --evidence next-evidence.json
omnicrawler repair rollback -c task.yaml
```

`--generate --max-rounds 1` 代替 --candidate，显式启用模型候选；max-rounds 是本次请求上限，限 1–3，并受原配置更小的请求/字符/费用预算约束，单次输出不超过 4096 token。低置信 LLM 草案停留 L0，不能走 L1 免审通道；用户可以审阅后提供明确的本地规则。禁用 AI 后，既有稳定 LLM 候选也不能自动晋级。

每次只应用一个候选，已有应用须先观察或回滚再更换。L2 的后续独立观察复用同一数据库，按既有规则晋级 L3 或在连续退化后回滚。已有正文、标注与分组的证据不会再次累计稳定/退化轮次；改样本 ID 或顺序不能绕过去重，去重与观察更新在同一 SQLite 事务中。正文和候选对比保留在本地预览，不重复采集。应用期间持有工作区会话锁，活动任务需先退出。写入保留原 YAML 中未知字段和未解析凭据引用，不把 AppConfig 展开的秘密写回。

应用前保留 ConfigHistory 快照及其哈希，并写持久准备记录；进程中断后明确要求 rollback。回滚校验历史范围和哈希，活跃配置被手工修改时拒绝覆盖，原修改保留供审阅。无守护进程、没有第二套质量账本。本轮本地离线回归不代表外部模型可用性、实际费用或公网站点支持已经验收。
