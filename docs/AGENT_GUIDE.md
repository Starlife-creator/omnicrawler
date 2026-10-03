# Agent 使用指南（AI / 脚本如何稳定接管 CLI）

> 适用版本：0.13.1 · 契约 schema：`agent-surface/1` · 维护状态：现行

## 先读契约，再调命令

不要靠 `--help` 的文本猜。机器可读的契约在这里拿：

```bash
python tools/check_agent_surface.py --json     # 完整契约（命令 / 参数 / 退出码 / stdout 形态）
python tools/check_agent_surface.py            # 一行一个命令的摘要
python tools/check_agent_surface.py --check    # 校验契约完整性（CI 门禁同款）
```

契约里每个命令都有：`options`（参数与默认值）、`observed_exit_codes`（源码里静态提取到的退出码）、
`stdout` 与 `stdout_source`。

## stdout 形态：先确认，别默认它是 JSON

`stdout_source` 只有三种取值，含义不同：

| 取值 | 含义 | 你可以怎么用 |
|---|---|---|
| `verified` | 该命令的 handler 源码里确有 `_json(...)` 调用 ⇒ stdout **是纯 JSON** | 可直接 `json.loads` |
| `declared` | 无法从源码验证，已**显式声明**（如 `text` / `yaml` / `none`） | 按声明的形态处理 |
| `unknown` | 既不可验证也未声明 | ★ **门禁会判红并点名** —— 出现即视为缺陷，不要猜 |

★ 已知的非 JSON 例外（已在契约里声明）：`import-easyspider` 默认输出 **YAML**；
`serve` / `visual-select` / `wizard` / `workbench` 无 stdout 契约（交互或长运行）；
`pdf` / `auto-analyze` / `c4a-fetch` / `gen-templates` / `benchmark` / `stealth-fingerprint` 默认文本。

需要机器消费时优先用命令自带的开关：`--format json`（`plugins audit --report`、
`import-easyspider`）、`--json`（`stealth-fingerprint`）、`--quiet`（`convert`，把人类摘要关掉）。

## 退出码

约定：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 运行期失败（结果未达成） |
| 2 | 用法或校验失败（参数 / 配置不合法） |

契约里的 `observed_exit_codes` 是从源码静态提取到的**实际**码值；两者冲突时以源码为准，并当作缺陷登记。

## 非 TTY 环境下的既定行为

- **插件信任询问**：非 TTY（脚本 / 管道）一律**自动拒绝**（`cli/_handlers.py` 的 `_cli_trust_prompter`）。
  也就是说，agent 调用不会被一个交互提示挂住 —— 但**未受信任的插件会直接加载失败**，这是预期行为。
- **长运行命令**：`serve` / `worker` / `serve` 类命令不会返回；agent 调用时要自行设超时。

## 最小可用示例

```bash
# 1) 拿契约
python tools/check_agent_surface.py --json > /tmp/surface.json

# 2) 转换：stdout 是纯 JSON，可直接解析
omnicrawler convert --from in.csv --to out.jsonl --quiet

# 3) 插件环境诊断：显式选 json
omnicrawler plugins audit --report --format json
```

## 边界（如实说明）

提取修复使用结构化 CLI：先 `omnicrawler repair preview -c task.yaml --evidence evidence.json --candidate candidate.json`，确认完整留出与历史证据后显式 apply，后续 observe 或 rollback。详情见 [修复工作流](REPAIR_WORKFLOW.md)；缺证据不覆盖配置，模型网页内容不能扩展工具权限。

- 契约描述的是**调用面**（命令 / 参数 / 输出形态），**不承诺**任何单个命令的业务语义；
  业务语义以各命令的 `--help` 与对应文档为准。
- `stdout_source=unknown` 一旦出现就是缺陷：它意味着新增命令时既没接 `_json()`、
  也没在 `tools/check_agent_surface.py` 的 `DECLARED_OUTPUT` 里声明形态。


### 自动分析保留中文需求

`omnicrawler auto-analyze <URL> --request "从 <URL> 采集前两页名言正文、作者和全部标签，导出 Excel。" -o task.yaml`
将需求页数、字段、多值及输出格式写入自动配置；无法可靠推断的字段或不支持的自动操作明确报错，不写假成功配置。随后按常规先试跑再运行。HTML 下一页使用 `source.follow_xpath` 仅跟进已识别的下一页链接，避免登录或导航消耗页面预算；它不关闭域名、robots 或出网边界。

自动列表中，标题属性仅在对应可见标题时采用；标签用 `all: true` 保留数组。稳定详情链接对应 `extract.deduplicate_by` 时，同实体、同数据在同轮只交付一次，内容变化或标识缺失保留原记录，不按同名标题合并。
