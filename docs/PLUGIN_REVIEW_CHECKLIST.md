# 插件审核清单（G3：审核员工作流）

本文档是 `reviewed` 档审核的 checklist（第 55 轮；第 67 轮统一术语：**AI 不审核、AI 增强审核员**）。

## 审核范围

- 新提交插件：四门禁（CI 自动）→ 人工复核（本清单）→ 签名背书 → 合并。
- 审核周期承诺：社区化后新提交 ≤7 天（第 55 轮）。
- 审核结论 = **人工复核 + 签名背书**；AI 辅助分析仅作输入，零 Pass/Fail 按钮（第 67 轮 P4）。

## 审核维度（第 55 轮）

### 1. 核心逻辑
- [ ] `handle(operation, payload)` 按操作正确分派；返回值均为 dict（协议不变式）。
- [ ] 无危险调用：无 `eval/exec/subprocess/os.system` 等（AST 预检已拦，人工复核确认）。
- [ ] 业务逻辑与 listing.md 描述一致；无隐藏行为。

### 2. 能力面与声明一致性（门 1 复核）
- [ ] 代码实测能力 ⊆ 声明的 permissions/domains/input_files。
- [ ] `execution_mode` 与契约形态一致（契约 2 = subprocess；契约 1 申请 in_process 走 T3）。
- [ ] `dependencies` 与实测导入图双向互证（声明未导入/导入未声明均拒）。
- [ ] `network` 权限有 `domains`；`files:read` 有 `input_files` 白名单。

### 3. 数据外传风险（J2）
- [ ] 是否读取 records 后经 network 外传？`egress_policy` 档位是否匹配企业策略。
- [ ] domains 是否与业务必要性匹配（自声明非防外传边界，审核重点核）。
- [ ] 密钥使用：优先 auth 注入；`secrets.get` 是显式例外（白名单 + 审计）。

### 4. 更新历史与健康度
- [ ] 版本号规范；变更记录与内容一致。
- [ ] 上游依赖许可在白名单内；无新增未声明依赖。
- [ ] 本地 `plugins audit --local .` 全绿（门 1/2/3 + 契约一致性）。

## 签名背书

- [ ] 创作者轨签名（creator.sig + creator.identity）有效。
- [ ] 维护者冷私钥签名 plugin.py（发布动作，CI 不持私钥）。
- [ ] 审核结论 + 签名背书入 catalog（`gates_evidence` 机制，第 78 轮）。

## 审核员工作台（AI 增强，第 67 轮 P4）

- AI 辅助分析区：LLM 源码分析作**辅助输入**（数据结构/数据外传模式/潜在危险调用提示）。
- **UI 硬约束**：AI 区零 Pass/Fail 按钮；签字按钮与 AI 区物理隔离（防误点/自动化点击）。
- 结论以人工复核为准；AI 提示不自动触发任何门禁/豁免变更（H7 规则变更语义）。
