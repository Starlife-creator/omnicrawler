"""模型上游下载：**本项目不托管、不镜像、不校验模型字节**。

设计前提（与《候选优化》条目 4 一致）
------------------------------------
所有模型一律从**上游**获取。本模块只负责三件事：

1. 配置下载源与缓存目录（``PADDLE_PDX_MODEL_SOURCE`` / ``PADDLE_PDX_CACHE_HOME``）；
2. 触发 PaddleX 自带的下载器（**不自己发任何 HTTP 请求**）；
3. 下载完成后用 ``core.capabilities`` 的口径复检"到底能不能用"。

**责任边界**：模型字节的内容完整性由上游负责，本项目不为其背书。
这与 ``tools/download_and_smoke_test.py`` 的既有语义一致——那里也是
"构造 ``PPStructureV3`` 让 PaddleX 自己去拉"，并明确写着
"CDN 下载、无哈希钉 … 属已知责任边界"。

为什么不自己写下载器
--------------------
- PaddleX 已实现多源（bos / huggingface / modelscope）与自动 fallback，
  见 ``paddlex/inference/utils/official_models.py`` 的 ``_build_hosters()``：
  ``MODEL_SOURCE`` 指定的源排首位，其余源参与健康探测，全部不可用时才报错。
- 自己再写一份会**引入第二套网络行为**，与仓库"市场下载复用任务抓取同一条出口"
  的既有纪律冲突；
- 而且自己下载还得自己处理断点、镜像、重试——正是要避免的重复劳动。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 上游下载源。与 PaddleX 的 ``hoster_candidates`` alias 一一对应。
MODEL_SOURCES: tuple[str, ...] = ("aistudio", "huggingface", "modelscope", "bos")

#: 默认源。**不选 huggingface**：本机实测 huggingface.co 直连 SSL 失败，
#: 而 aistudio（百度自家 CDN）在中文网络环境下可达性最好。
DEFAULT_MODEL_SOURCE = "aistudio"

#: 各源的可读说明，用于界面提示与失败建议。
SOURCE_HINTS: dict[str, str] = {
    "aistudio": "百度 AI Studio（国内网络通常最快）",
    "huggingface": "HuggingFace（部分网络需镜像或代理）",
    "modelscope": "魔搭 ModelScope（国内可用）",
    "bos": "百度对象存储直连（无需鉴权）",
}


def normalize_source(source: str | None) -> str:
    """把用户输入收敛成已知源；未知值回落到默认源而不是抛错。

    理由：下载源是"偏好"而非"契约"，用户传错一个词不应该让整个下载失败。
    但回落必须**留痕**，不能静默。
    """
    value = str(source or "").strip().lower()
    if value in MODEL_SOURCES:
        return value
    if value:
        logger.warning("未知的模型下载源 %r，回落到 %s", source, DEFAULT_MODEL_SOURCE)
    return DEFAULT_MODEL_SOURCE


def configure_environment(source: str | None = None, cache_dir: Path | None = None) -> dict[str, str]:
    """设置 PaddleX 读取的环境变量，返回生效的设置（便于回显与排查）。

    使用 ``setdefault`` 语义与 ``core.runtime_paths`` 保持一致：
    **不覆盖用户显式设置的值**，只补默认值。
    """
    resolved = normalize_source(source)
    applied: dict[str, str] = {}

    if cache_dir is not None:
        cache_dir = Path(cache_dir).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        value = str(cache_dir)
        if not os.environ.get("PADDLE_PDX_CACHE_HOME"):
            os.environ["PADDLE_PDX_CACHE_HOME"] = value
        applied["PADDLE_PDX_CACHE_HOME"] = os.environ["PADDLE_PDX_CACHE_HOME"]

    if not os.environ.get("PADDLE_PDX_MODEL_SOURCE"):
        os.environ["PADDLE_PDX_MODEL_SOURCE"] = resolved
    applied["PADDLE_PDX_MODEL_SOURCE"] = os.environ["PADDLE_PDX_MODEL_SOURCE"]

    # Paddle 3.3 的 oneDNN/PIR 路径无法执行每个 PPStructureV3 detector
    # （与 core/runtime_paths.py 的 Windows/Linux 规避同因，见其注释）。
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    return applied


def cache_root() -> Path:
    """当前生效的模型缓存根目录（读 PaddleX 自己的环境变量）。"""
    raw = os.environ.get("PADDLE_PDX_CACHE_HOME", "")
    if raw:
        return Path(raw)
    return Path.home() / ".paddlex" / "official_models"


def downloaded_models(root: Path | None = None) -> list[str]:
    """列出**权重完整**的模型目录。

    判据与 ``core/capabilities.py`` 的 F41 一致：必须有 ``inference.pdiparams``，
    否则"目录存在"不等于"可用"——半截下载的目录会让用户以为已经装好了。
    """
    base = Path(root) if root is not None else cache_root()
    official = base / "official_models" if (base / "official_models").is_dir() else base
    if not official.is_dir():
        return []
    return sorted(
        path.name for path in official.iterdir()
        if path.is_dir() and (path / "inference.pdiparams").is_file()
    )


def probe() -> dict[str, Any]:
    """只读探测：当前源、缓存位置、已下载模型。**不联网、不下载。**"""
    source = os.environ.get("PADDLE_PDX_MODEL_SOURCE", "")
    root = cache_root()
    models = downloaded_models(root)
    return {
        "source": source or None,
        "source_hint": SOURCE_HINTS.get(source, ""),
        "cache_root": str(root),
        "cache_exists": root.is_dir(),
        "models": models,
        "model_count": len(models),
        "ready": bool(models),
    }


def download(source: str | None = None, *, cache_dir: Path | None = None,
             should_stop: Any = None) -> dict[str, Any]:
    """触发上游下载并复检。

    ``should_stop`` 是可选的无参回调：返回 True 时在**开始前**放弃
    （下载本身由 PaddleX 执行，无法逐字节中断；这里只保证不在用户已取消时启动）。

    返回结构刻意区分三种状态：
      - ``ok``：下载完成且有可用模型；
      - ``unavailable``：环境里没有 PaddleX（不是失败，是这项能力不在这台机器上）；
      - ``failed``：真的出错，带原因。
    """
    if should_stop is not None and should_stop():
        return {"status": "cancelled", "reason": "用户已取消下载"}

    applied = configure_environment(source, cache_dir)

    try:
        import paddleocr  # noqa: F401  # 只探测可导入性
    except ImportError as exc:
        # 这是**预期内的降级**，不是错误：Standard 版本来就不含 OCR 运行时。
        return {
            "status": "unavailable",
            "reason": (
                "本机没有 PaddleOCR 运行时，无法下载模型。"
                "模型只是权重，需要推理包才能使用——请先安装 OCR 组件，"
                "或改用 Full 版。"
            ),
            "error": f"{type(exc).__name__}: {exc}",
            "environment": applied,
        }

    try:
        from paddleocr import PPStructureV3

        # 构造 pipeline 即触发 PaddleX 自己的下载：这是**唯一**的下载途径。
        # 参数与 tools/download_and_smoke_test.py 保持一致，避免两处行为漂移。
        pipeline = PPStructureV3(
            lang="ch",
            device="cpu",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            use_table_recognition=True,
            use_formula_recognition=False,
            use_chart_recognition=False,
        )
        del pipeline
    except Exception as exc:  # noqa: BLE001 - 上游失败原因必须原样透传给用户
        return {
            "status": "failed",
            "reason": "模型下载失败",
            "error": f"{type(exc).__name__}: {exc}",
            "environment": applied,
        }

    report = probe()
    report["status"] = "ok" if report["ready"] else "failed"
    report["environment"] = applied
    if not report["ready"]:
        report["reason"] = (
            "下载流程结束但未找到完整模型权重（缺 inference.pdiparams）。"
            "可能是网络中断或上游源不可用，请换一个源重试。"
        )
    return report
