# SDK 使用指南

`omnicrawl.sdk` 包提供公共编程接口，可用于自动化采集任务。

## 快速入门

```python
from omnicrawl import sdk

# 1. 校验并编译执行计划
report = sdk.validate("configs/my_site.yaml")
plan = sdk.compile("configs/my_site.yaml")
print(plan)

# 2. 运行采集任务
result = sdk.run("configs/my_site.yaml")
print(f"采集完成: {result['status']}, 记录数: {result.get('records', 0)}")

# 3. 查询运行状态与数据统计
status = sdk.query("configs/my_site.yaml")
print(f"最新运行: {status['run']}")
print(f"统计: {status['totals']}")
```

## 从 TaskSpec 直接构建

```python
from omnicrawl.sdk import TaskSpec, TaskIR, compile_task_plan

# 从 YAML 加载 TaskSpec
spec = TaskSpec.from_yaml("configs/my_site.yaml")

# 编译为 TaskIR（中间表示）
ir = TaskIR.from_task_spec(spec)
print(f"采集目标: {ir.goal}")

# 编译为可执行计划
plan = compile_task_plan(ir)
print(f"资源上限: {plan.resource_bounds}")
```

## 插件协议扩展

```python
from omnicrawl.sdk.protocols import Fetcher, Extractor, Processor, Exporter

class MyExtractor(Extractor):
    def extract(self, document, result):
        # 自定义提取逻辑
        return {"my_field": "value"}
```

## SDK 公共 API

| 函数 | 稳定性 | 说明 |
|---|---|---|
| `validate(path)` | stable | 校验配置文件 |
| `compile(path)` | stable | 编译执行计划 |
| `run(path, resume=False)` | preview | 运行或恢复采集 |
| `query(path)` | preview | 查询运行状态与统计 |

## 数据契约

```python
from omnicrawl.sdk.data import DatasetReader

reader = DatasetReader("work/my_project/state.sqlite3")
records = reader.read_records(limit=50)
for rec in records:
    print(rec.record_type, rec.source_url)

# 查询附件清单
attachments = reader.read_attachments()
for att in attachments:
    print(f"  附件: {att.local_path} ({att.size_bytes} bytes)")
```

## 稳定性保证

- **stable**：按语义化版本维护，删除前至少一个小版本弃用期
- **preview**：可在小版本演进但会给出迁移说明
- 未在 `__all__` 中导出的名称不构成兼容承诺

详见 `docs/PLUGIN_CONTRACT.md` 和 `docs/SECURITY_AND_COMPLIANCE.md`。
