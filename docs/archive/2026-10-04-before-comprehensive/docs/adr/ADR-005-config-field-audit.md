# ADR-005：AppConfig 与 CrawlConfig 字段差异审计

> 状态：已生效 | 日期：2026-07-27

## 背景

OmniCrawler 存在两个配置模型：

- `config.py::AppConfig` — 运行时核心配置（被 pipeline、fetcher、exporters、state 消费）
- `gui/core/config_model.py::CrawlConfig` — GUI 向导配置模型（被 5 个 Wizard 页面和 serializer 消费）

两者独立定义，新增字段需要在两处同时添加。本文档标注字段归属关系。

> 注：字段级映射清单已从本文档移除，改由 `tests/unit/config/test_field_mapping_contract.py`
> 以可执行断言固化（见结论第 5 条），避免文档与代码脱节后误导后人。

## 结论

1. **不做合并**。两模型职责不同：AppConfig 是运行时真相源，CrawlConfig 是 GUI 视图 + UI 状态。
2. **命名对齐优先**。`output_formats`（CrawlConfig）↔ `outputs.*`（AppConfig.raw）等命名差异是纯粹的遗留问题，应统一。
3. **新字段决策树**：
   - 运行时需要的 → 入 `raw`（AppConfig）
   - GUI 临时状态（unsaved/样本值/标记） → 入 CrawlConfig
   - 运行时 + GUI 都需要的 → 入 `raw`，CrawlConfig 通过 serializer 读写
4. **不采纳**：曾考虑添加 `CrawlConfig.to_app_config()` / `from_app_config()` 双向转换方法。
   经评估，YAML 文件解耦是既定设计（GUI 与运行时通过文件交换，而非方法调用），
   显式方法会引入直接耦合，违背 ADR 初衷。维持现状。
5. **字段映射契约**：字段对应关系不再以文档形式维护，改由
   `tests/unit/config/test_field_mapping_contract.py` 以可执行断言固化。
   仅覆盖 A 类（GUI 可编辑）字段的正向链路（CrawlConfig → YAML → AppConfig.raw）；
   B 类（passthrough 透传）字段由 `test_gui_config_preservation.py` 做往返保活断言。
   修改字段映射者，需同步更新该测试文件。
