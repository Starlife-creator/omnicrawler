# 困难 OCR 的有界重试

> 适用版本：**0.15.0** · 配置协议：v5 · 维护状态：现行

PDF 项目的本机 Tesseract 支持低质结果触发重试。默认关闭；可在项目 YAML 的 `ocr` 节显式启用。Paddle 与离线组件当前不支持该策略，配置校验会明确拒绝，避免用户以为它们已自动重试。

```yaml
ocr:
  backend: tesseract
  lang: chi_sim+eng
  image_scale: 1
  page_segmentation_mode: 3
  adaptive_retry:
    enabled: true
    maximum_attempts: 3
    total_timeout_seconds: 60
    minimum_characters: 40
    minimum_confidence: 0.8
    maximum_garbled_ratio: 0.03
    profiles:
      - image_scale: 3
        page_segmentation_mode: 6
      - image_scale: 2
        page_segmentation_mode: 11
```

`maximum_attempts` 含首次识别，上限 4。总识别时间预算须大于 0 且不超过 120 秒，每次 Tesseract 子进程只获得剩余时间；像素预算为放大后 2000 万，分配 RGB/放大图前检查。策略去重，首次结果达到触发条件的反面时直接返回，不额外识别。短页不一定有错，阈值是资源调度触发条件，不是正确率。原始值确实是 0 不因此被判为空。

Tesseract 的[官方质量建议](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)介绍分辨率与页面分段的影响：模式 6 假设一个文本块，模式 11 面向稀疏文字。这里把它们用作候选策略，不推断所有页面都符合该布局。旋转、透视和阴影仍需后续独立处理；当前不自动更换模型或训练模型。

所有识别尝试保留文本、分数、倍率、模式、原始输入与文本摘要、失败原因及选中尝试，写入页面 `ocr_structure_json` 的 `metadata.adaptive_retry`。选中的词框仍映射回原图。选择先比较乱码比例，再比较可打印字符量，最后以识别分数打破平局；这是文字覆盖启发式，不能证明较长文本更准确，也不将不同阅读顺序的结果直接拼成正文。其他结果保留供复核；某次失败不抹掉已有可用结果。

识别文本有差异、仍达不到触发条件、时间预算耗尽或任一尝试失败时，来源字段必须复核，即使用户把自动接受阈值设得很低也不能自动放行。现有人工接受记录继续受保护。全无可用文本时明确失败，不把空页冒充成功。复核信息随正式字段验证进入 GUI/导出；全部尝试详情可从工作区数据库查看。

开发诊断使用固定上游版本的 PubTabNet 公开例图与标注，输入和原始输出保存在工作区，不重分发到产品。字词多重集覆盖忽略阅读顺序、单元格归属及表格拓扑；开发样例改善不能用于声称业务准确率。原始失败、仍未覆盖的 token 和新增误识别均保留。

本机固定公开开发样例的文字 token 多重集覆盖：同模式首次识别约 5.3%，有界重试选中结果约 56.9%；其余遗漏与额外识别 token 均记录在 `adaptive-ocr-public-probe-final.json`。触发和选择不读取标注，但候选参数是在开发样例上探索所得，不能解释为留出集收益或业务召回率。真实扫描 PDF 验收另验证重试信息保存至数据库，并强制复核直至结果导出。
