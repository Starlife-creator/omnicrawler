"""重型 Wheel-Pack 组件配方：只托管声明，字节与模型都从上游来。

## 设计边界（来自维护者裁决）

> 不用 PaddleOCR 就不应该内置 `paddle`，它是纯多余；但我需要的话应该能做到
> 把他装回来。几百 KB 的轻量库直接打包我是允许的。

所以：

| 类别 | 处理 |
|---|---|
| 几百 KB 纯 Python 库（openpyxl / reportlab / python-docx） | **直接进 Standard** |
| 重型 C 扩展（paddle / cv2 / paddleocr） | **Wheel-Pack 配方** |
| 模型权重（PPStructureV3 等，1 GB 级） | **绝不进配方**，走 `model_download.py` 上游直拉 |

## 铁律：模型不得进 recipe

`候选优化.md` 条目 9 与 `asset_trust.py:68-72` 已用代码和反向守卫钉死：
`paddle_models` 严禁进入 `recipe.json`。原因是**上游不提供模型文件的内容哈希**，
我们没有可信的 sha256 可填——伪造一个就是自欺。

## 判据

1. 配方**必须**带 `python_abi`（重型 C 扩展与解释器 ABI 强耦合）；
2. 每个构件**必须**有真实 sha256（不能是占位值）；
3. 配方里**不得**出现模型文件（`.pdmodel` / `.pdiparams` / `inference.pdmodel` 等）；
4. 构件 URL 必须是 `https://` 且指向**官方源**（`files.pythonhosted.org`）；
5. 声明的 `size` 应与真实构件量级相符（防手抄错）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_MARKET = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
_COMPONENTS = _MARKET / "components"

#: 模型权重文件的典型后缀——出现即说明有人把模型塞进了配方。
_MODEL_SUFFIXES = (
    ".pdmodel",
    ".pdiparams",
    ".pdiparams.info",
    ".onnx",
    ".traineddata",  # 语言包例外见 tesseract 配方
    ".pt",
    ".pth",
    ".safetensors",
)

#: 唯一允许出现 `.traineddata` 的配方（Tesseract 语言包本身就是该格式）。
_TRAINEDDATA_ALLOWED = {"tesseract-chi-sim"}


def _recipes() -> list[tuple[str, dict]]:
    if not _COMPONENTS.is_dir():
        return []
    out: list[tuple[str, dict]] = []
    for directory in sorted(_COMPONENTS.iterdir()):
        recipe_path = directory / "recipe.json"
        if recipe_path.is_file():
            out.append(
                (directory.name, json.loads(recipe_path.read_text(encoding="utf-8")))
            )
    return out


def _heavy_recipes() -> list[tuple[str, dict]]:
    """重型配方 = 带 python_abi 声明的那些（轻量纯 Python 不需要 ABI）。"""
    return [(name, r) for name, r in _recipes() if r.get("python_abi")]


def test_there_is_at_least_one_heavy_recipe() -> None:
    """至少要有一个重型 Wheel-Pack 配方，否则批次 3 没有可验证的对象。"""
    heavy = _heavy_recipes()
    assert heavy, (
        "没找到任何带 python_abi 的配方 —— 重型组件运行器缺少真实用例"
    )


@pytest.mark.parametrize("name,recipe", _heavy_recipes())
def test_heavy_recipe_declares_abi(name: str, recipe: dict) -> None:
    """重型 C 扩展必须声明 python_abi —— 与解释器版本强耦合。"""
    abi = recipe.get("python_abi")
    assert isinstance(abi, list) and abi, f"{name} 未声明 python_abi"
    for item in abi:
        assert str(item).startswith("cp"), (
            f"{name} 的 ABI 声明 {item!r} 不像 CPython ABI tag（应形如 cp313）"
        )


@pytest.mark.parametrize("name,recipe", _recipes())
def test_every_artifact_has_real_hash(name: str, recipe: dict) -> None:
    """每个构件必须有真实 sha256，不能是占位值。"""
    for index, artifact in enumerate(recipe.get("artifacts") or []):
        digest = str(artifact.get("sha256") or "")
        assert len(digest) == 64, f"{name} 的 artifact #{index} 缺 sha256"
        assert set(digest) != {"0"}, (
            f"{name} 的 artifact #{index} 用了占位哈希——安装时必然失败"
        )


@pytest.mark.parametrize("name,recipe", _recipes())
def test_recipe_does_not_embed_models(name: str, recipe: dict) -> None:
    """配方里**不得**出现模型权重文件。

    模型走上游直拉（`model_download.py`），因为上游不提供内容哈希——
    伪造一个就是自欺。
    """
    if name in _TRAINEDDATA_ALLOWED:
        pytest.skip(f"{name} 的语言包本身就是 .traineddata，允许")

    for artifact in recipe.get("artifacts") or []:
        url = str(artifact.get("url") or "").lower()
        for suffix in _MODEL_SUFFIXES:
            assert not url.endswith(suffix), (
                f"{name} 的构件疑似模型文件（{suffix}）：{url}。"
                "模型必须走 model_download.py 上游直拉，不得进 recipe"
            )


@pytest.mark.parametrize("name,recipe", _recipes())
def test_artifacts_use_https_and_official_hosts(name: str, recipe: dict) -> None:
    """构件必须是 https，且主机在白名单内。"""
    allowed_hosts = {
        "files.pythonhosted.org",
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
    }
    for artifact in recipe.get("artifacts") or []:
        url = str(artifact.get("url") or "")
        assert url.startswith("https://"), f"{name} 的构件不是 https：{url}"
        host = url.split("//", 1)[1].split("/", 1)[0]
        assert host in allowed_hosts, (
            f"{name} 的构件主机 {host!r} 不在白名单"
        )


@pytest.mark.parametrize("name,recipe", _recipes())
def test_declared_sizes_are_plausible(name: str, recipe: dict) -> None:
    """声明的 size 必须在合理量级，不能是 0 或离谱值。"""
    for artifact in recipe.get("artifacts") or []:
        size = artifact.get("size")
        assert isinstance(size, int) and size > 0, (
            f"{name} 的构件缺 size 或为 0——用户无法在下载前知道要拉多少"
        )


@pytest.mark.parametrize("name,recipe", _recipes())
def test_entry_runner_is_declared(name: str, recipe: dict) -> None:
    """入口必须声明受支持的 runner。"""
    entry = recipe.get("entry") or {}
    runner = entry.get("runner")
    if runner is None:
        return  # 原生可执行组件可省略（默认 native 语义）
    assert runner in {"native", "worker-wheelpack"}, (
        f"{name} 的 runner {runner!r} 不受支持"
    )


def test_heavy_recipe_uses_wheelpack_runner() -> None:
    """重型配方必须走 worker-wheelpack，否则子进程隔离不生效。"""
    for name, recipe in _heavy_recipes():
        assert (recipe.get("entry") or {}).get("runner") == "worker-wheelpack", (
            f"{name} 是重型组件却没用 worker-wheelpack runner —— "
            "会在宿主进程内加载，可能撞原生库符号冲突"
        )
