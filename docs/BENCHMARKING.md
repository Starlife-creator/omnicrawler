# 性能基准：可复现与公平对比

> 适用版本：0.12.0 · 对应阶段：第四阶段（完整体验与 UI）· 维护状态：现行

## 这份文档解决什么

`优化方案.md` §一 要求「用公开、可复现、可比较的任务结果证明各项优势」，§1.2 对「采集能力」
进一步升级为「**具备可复现的任务基准与公平对比能力**」。

这不是形容词，而是三条可被检验的约束：

| 约束 | 含义 | 落地方式 |
|---|---|---|
| 档位真正生效 | 不同档位必须跑**不同的负载** | `apply_profile()` 把档位写入派生配置 |
| 结果自带复现信息 | 他人能重建这次运行 | 源/生效配置指纹 + 档位参数 + 环境 |
| 只有可用运行才成为基线 | 退化检测不能被零吞吐基线废掉 | `BenchmarkResult.usable` |

## 怎么跑

```bash
omnicrawler benchmark -c configs/full_pipeline.yaml --profile all
```

| 参数 | 说明 |
|---|---|
| `--profile` | `low` / `standard` / `high` / `all`（默认 `all`） |
| `--output` | 基准历史 JSON 路径（默认 `bench_history.json`） |
| `--history` | 从已有历史读取基线（默认同 `--output`） |
| `--regression-threshold` | 吞吐量退化告警阈值（默认 `0.1` 即 10%） |

## 三个档位

| 档位 | 并发 | 请求间隔 | 超时 | 页数上限 |
|---|---|---|---|---|
| `low` | 1 | 2.0s | 30s | 10 |
| `standard` | 3 | 1.0s | 25s | 50 |
| `high` | 8 | 0.3s | 20s | 200 |

**档位定义负载，因此它覆盖被跑分配置自身的** `crawl.concurrency`、`crawl.max_pages`、
`http.delay_seconds`、`http.timeout_seconds`。

为什么是「覆盖」而不是「取较小值」：若让配置获胜，同一配置在不同档位下会跑出不可比的负载，
「档位」就只是一个标签。反过来，只要档位固定、源配置相同，任何人都能重建同一次运行。

派生配置写入 `<配置所在目录>/.benchmark/profile-<档位>.yaml`。**刻意使用固定路径**——
复现一次基准时，实际执行的是哪份配置应当可以被直接打开查看，而不是藏在随机临时目录里。

### 复现一次运行

持有结果中的 `config_sha256`、`profile_settings` 与 `environment`，即可：
用同版本的包、同一份源配置、同名档位重跑。`apply_profile()` 是确定性的，
所以派生配置的 `effective_config_sha256` 应当一致。

## 结果里有什么

| 字段 | 含义 |
|---|---|
| `profile` | 档位名 |
| `pages` | 本次落库的响应数（吞吐量的分子） |
| `duration_seconds` | 墙钟耗时 |
| `pages_per_second` / `seconds_per_thousand_pages` | 派生吞吐指标 |
| `peak_memory_bytes` | 峰值内存（见「已知限制」） |
| `bytes_transferred` | **落库响应字节数**（非网络传输量） |
| `errors` | 该次运行的错误计数（口径见「已知限制」） |
| `ok` | 流水线是否以 `succeeded` 结束 |
| `status` | 流水线终态（`succeeded` / `failed` / `cancelled`） |
| `config_sha256` | 源配置指纹 |
| `effective_config_sha256` | 施加档位后实际执行的那份配置的指纹 |
| `profile_settings` | 本次所用档位参数（用于确定性重建） |
| `environment` | 包版本 / Python / 实现 / 平台 / 解释器路径 |

## 判据：`ok` 与 `usable` 是两件事

| 判据 | 含义 |
|---|---|
| `ok` | 流水线是否以成功状态结束——**只陈述运行结局** |
| `usable` | `ok ∧ pages > 0`——**能否作为一次基准测量** |

只有 `usable` 为真的运行才会被 `BenchmarkHistory.baseline()` 选为基线。

原因：吞吐量是「页 / 秒」。一页都没取到时这个比值无从谈起；若这种运行入库并成为基线，
之后任何真实运行都会显得「比基线更好」，**该档位的退化检测就此静默失效**。

## 如何比较两次结果

```bash
# 首次运行建立基线
omnicrawler benchmark -c config.yaml --profile standard

# 改动后再次运行，自动与该档位基线比较
omnicrawler benchmark -c config.yaml --profile standard
```

比较使用 `compare_benchmark()`：以档位最早的一次**可用**运行作为基线，吞吐量下降超过阈值
（默认 10%）即标记退化。跨档位比较无意义——档位不同，负载就不同。

## 已知限制

如实列出，避免把「看起来有值」当作「测到了」：

1. **峰值内存是采样值**：通过流水线事件回调节流采样（间隔 ≥ 50ms），不是逐次分配的精确峰值；
   若运行期间没有事件，退化为「运行前后取较大者」。用于发现数量级变化，不适合精细对比。
2. **`ok` 只接受两种终态**：`succeeded` 与 `partial_success`（有交付但存在错误记录——
   吞吐量仍是一次有效测量）。`failed` / `cancelled` 不予采信。
   > 2026-09-11 之前，流水线在「全部抓取失败」时也报告 `succeeded`，属**异常伪装成功**；
   > 该缺陷已在同日的可靠性批中修正，流水线现在按「是否交付」给出 `failed` / `partial_success`。
   > 详见 `docs/OPERATIONS.md` 的「终态语义」。
3. **`bytes_transferred` 的口径是落库字节**：从状态库聚合 `responses.size_bytes`，
   不含被丢弃的响应、压缩前体积与响应头。
4. **本模块测的是流水线吞吐，不是抽取质量**：数据完整性 / 准确性的测量在
   **任务质量基准**里（见「任务质量基准」一节），两者口径不同、互不替代。

## 任务质量基准：完整、准确、有来源证据

吞吐回答「跑得多快」，质量回答「采到的数据对不对」。两者都是 §1.2 采集能力的要求
（「稳定获得完整、准确、有来源证据的数据；并具备可复现的任务基准与公平对比能力」）。

```bash
python tools/benchmark_quality.py                  # 跑全部内置任务（离线）
python tools/benchmark_quality.py --json report.json
```

| 口径 | 含义 |
|---|---|
| **完整性** | 期望的记录有多少条真被采到 |
| **准确性** | 期望字段值有多少个与真值逐字相符 |
| **来源证据** | 采到的记录里有多少条带 `source_url` |
| **字段自报完整度** | 流水线自己在 `evidence._quality.completeness` 里报的完整度均值 |

最后一项是**互证**设计：前两项是外部按真值比对，这一项是流水线自己的口径；
两者都满分才说明「测到的」与「自报的」一致。

**为什么结果可比**：任务页面、抽取规则与期望结果都是
`omnicrawler.services.quality_benchmark` 里的常量，配置由代码确定性生成，
全程走本地 HTTP 服务——同一版本必然跑出同一任务。结果随带配置指纹与环境信息。

**怎么扩展**：在 `TASKS` 里加一个 `BenchmarkTask` 即可（页面结构、字段数、页数都可不同）。
注意**种子页必须含匹配块**：否则模板会在第一次抓取时被判「关键字段成功率下降」而失效，
整轮抽取退化为通用抽取——实测会得到 0 条记录。

## 相关

- 微基准（模板发现、HTML 抽取耗时）：`tools/benchmark_core.py`
- 转换专项基准：`tools/benchmark_convertx.py`
- 任务质量基准：`tools/benchmark_quality.py`（离线，跑内置任务并打分）
- 端到端公平性回归：`tests/integration/test_benchmark_fairness.py`
- 单元测试：`tests/unit/utils/test_benchmarking.py`
