"""raw 单文件构件：真实语言包能装、且不会绕过哈希校验。

## 为什么需要

组件市场首个真实组件 `tesseract-chi-sim` 托管的是 `.traineddata` 语言包——
它是**裸文件而不是归档**。第一次端到端验证时暴露了三个真实约束：

1. **格式必须显式声明**：`infer_artifact_format()` 对 `.traineddata` 后缀
   推断出 `zip`（因为没有已知归档后缀），导致 `BadZipFile`。
   设计要求 `raw` 显式声明，正是为了避免这种误判。
2. **raw 不支持 `include` / `strip_components`**：这两个概念只对归档有意义，
   校验器会明确拒绝，而不是静默忽略。
3. **哈希仍然强制**：raw 走的也是"先校验后落盘"，没有因为"是单文件"就放松。

## 判据

* 正确声明的 raw recipe 能装配出**字节完全一致**的入口；
* 缺 `format: raw` 时（按后缀推成 zip）必须失败；
* raw + `include` 必须在**校验阶段**被拒绝（不是装到一半才炸）；
* 篡改 sha256 必须阻断，且不留半成品。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from omnicrawler.services.component_install import RecipeInstallError, install_recipe_file

#: 模拟真实语言包：约 1 KB 的二进制内容，不依赖网络。
_PAYLOAD = bytes(range(256)) * 4


def _recipe(**artifact_overrides: object) -> dict:
    artifact = {
        "url": "https://example.invalid/chi_sim.traineddata",
        "sha256": hashlib.sha256(_PAYLOAD).hexdigest(),
        "size": len(_PAYLOAD),
        "extract_to": "tessdata",
        "format": "raw",
        "filename": "chi_sim.traineddata",
    }
    artifact.update(artifact_overrides)
    return {
        "schema": 1,
        # **必须声明 kind=data**：这是 tesseract 语言包（裸文件），
        # 不是 Python 包集合。缺这个字段会按默认 wheel-pack 处理，
        # 而 wheel-pack 强制要求 `smoke` 声明 —— 于是在装配阶段就被拒
        # （实测：`kind=wheel-pack 的组件必须声明 smoke`）。
        "kind": "data",
        "name": "raw-language-pack",
        "version": "1.0.0",
        "description": "raw 单文件构件测试",
        "platforms": ["windows", "linux", "darwin"],
        "architectures": ["x86_64"],
        "python_abi": [],
        "artifacts": [artifact],
        "entry": {
            "path": "tessdata/chi_sim.traineddata",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
        },
    }


def _downloader(tmp_path: Path):
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(_PAYLOAD)

    def _download(url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload_file, destination)

    return _download


def _write(tmp_path: Path, recipe: dict) -> Path:
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    return path


def test_raw_artifact_installs_byte_identical(tmp_path: Path) -> None:
    """raw 构件必须装配出与上游**字节完全一致**的文件。"""
    recipe_path = _write(tmp_path, _recipe())
    summary = install_recipe_file(
        recipe_path, tmp_path / "components", downloader=_downloader(tmp_path)
    )
    entry = Path(summary["entry"])
    assert entry.is_file(), "raw 装配后入口不存在"
    assert entry.read_bytes() == _PAYLOAD, "raw 装配内容与上游不一致"
    assert entry.parent.name == "tessdata", f"extract_to 未生效：{entry.parent}"


def test_raw_without_explicit_format_fails(tmp_path: Path) -> None:
    """不声明 `format: raw` 时，`.traineddata` 会被推成 zip 并失败。

    这条断言记录的是**第一次端到端验证踩到的真实问题**：
    `BadZipFile: File is not a zip file`。
    """
    recipe = _recipe()
    recipe["artifacts"][0].pop("format")
    recipe_path = _write(tmp_path, recipe)

    with pytest.raises(RecipeInstallError):
        install_recipe_file(
            recipe_path, tmp_path / "components", downloader=_downloader(tmp_path)
        )


def test_raw_rejects_include_at_validation(tmp_path: Path) -> None:
    """raw + include 必须在**校验阶段**被拒，而不是装到一半才发现。"""
    recipe = _recipe(include=["chi_sim.traineddata"])
    recipe_path = _write(tmp_path, recipe)

    with pytest.raises(RecipeInstallError) as excinfo:
        install_recipe_file(
            recipe_path, tmp_path / "components", downloader=_downloader(tmp_path)
        )
    message = str(excinfo.value)
    assert "raw" in message and ("include" in message or "strip_components" in message), (
        f"应以 raw 不支持 include 为由拒绝，实际：{message[:200]}"
    )


def test_raw_hash_mismatch_blocks_and_leaves_nothing(tmp_path: Path) -> None:
    """篡改 raw 的 sha256 必须阻断，且不留下装配目录。"""
    recipe = _recipe(sha256="f" * 64)
    recipe_path = _write(tmp_path, recipe)
    components = tmp_path / "components"

    with pytest.raises(RecipeInstallError) as excinfo:
        install_recipe_file(recipe_path, components, downloader=_downloader(tmp_path))
    assert "哈希不符" in str(excinfo.value)

    leftovers = [p.name for p in components.iterdir()] if components.is_dir() else []
    assert not leftovers, f"失败后留下半成品：{leftovers}"


def test_market_recipe_declares_raw_format() -> None:
    """市场仓的 Tesseract 语言包必须显式声明 raw，且带真实哈希。

    这条守卫盯的是**市场里的真实数据**：占位哈希（全 0）或缺少 format
    都会让用户安装时失败。
    """
    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    recipe_path = market / "components" / "tesseract-chi-sim" / "recipe.json"
    if not recipe_path.is_file():
        pytest.skip("需要 market 仓的组件 recipe")

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    artifact = recipe["artifacts"][0]

    assert artifact.get("format") == "raw", (
        "Tesseract 语言包是裸文件，必须显式声明 format: raw —— "
        "否则会被按后缀推成 zip 并在解压时报 BadZipFile"
    )
    digest = str(artifact.get("sha256") or "")
    assert len(digest) == 64 and set(digest) != {"0"}, (
        f"sha256 不能是占位值（当前 {digest[:16]}…）——占位值会让安装必然失败"
    )
    assert "include" not in artifact, "raw 构件不支持 include，校验器会拒绝"
