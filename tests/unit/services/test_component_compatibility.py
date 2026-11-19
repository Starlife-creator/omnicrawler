"""`check_compatibility()` 的 ABI / 平台 / 架构判定。

## 为什么需要这些守卫

真实 CI（v1.0.7）暴露了一个**自相矛盾**的判定：

```
组件装配失败：组件要求的 Python ABI 与当前不符：需要 ['cp313']，当前 cp313
```

需要的和当前的是**同一个 ABI**，却被判成不符。根因是：

```python
_require(str(required_abi) == target_abi, ...)
```

`python_abi` 在 recipe 里是**列表**（一份 recipe 可声明多个可接受的 ABI），
`str(["cp313"])` 得到字符串 `"['cp313']"`，与 `"cp313"` 永不相等。

后果：**任何声明了 `python_abi` 的配方都装不上**——包括完全匹配的情况。
这类缺陷不会在"只跑静态门禁"时暴露，只有真的执行安装才看得见。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.component_recipe import check_compatibility


def _recipe(**overrides) -> dict:
    base = {
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],
        "python_abi": ["cp313"],
    }
    base.update(overrides)
    return base


def test_matching_abi_is_accepted() -> None:
    """声明的 ABI 与当前相同时必须通过。

    这是那个真实缺陷的直接复现：列表 `["cp313"]` vs 字符串 `"cp313"`。
    """
    check_compatibility(_recipe(), platform="windows", architecture="x86_64", abi="cp313")


def test_abi_list_with_multiple_entries() -> None:
    """多个可接受 ABI 时任取其一即可。"""
    recipe = _recipe(python_abi=["cp312", "cp313", "cp314"])
    check_compatibility(recipe, platform="linux", architecture="x86_64", abi="cp313")


def test_non_matching_abi_is_rejected() -> None:
    """真正不匹配时必须拒绝——修复不能把判据改成恒真。"""
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(python_abi=["cp312"]),
            platform="windows",
            architecture="x86_64",
            abi="cp313",
        )
    assert "ABI" in str(excinfo.value)


def test_abi_absent_means_no_constraint() -> None:
    """没声明 `python_abi` 时不施加约束（纯 Python 组件）。"""
    recipe = {"platforms": ["linux"], "architectures": ["x86_64"]}
    check_compatibility(recipe, platform="linux", architecture="x86_64", abi="cp313")


def test_platform_mismatch_is_rejected() -> None:
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(platforms=["windows"]),
            platform="linux",
            architecture="x86_64",
            abi="cp313",
        )
    assert "平台" in str(excinfo.value)


def test_architecture_mismatch_is_rejected() -> None:
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(architectures=["aarch64"]),
            platform="linux",
            architecture="x86_64",
            abi="cp313",
        )
    assert "架构" in str(excinfo.value)


@pytest.mark.parametrize("abi_form", [["cp313"], ("cp313",)])
def test_abi_accepts_list_and_tuple(abi_form) -> None:
    """列表与元组两种写法都要支持。"""
    check_compatibility(
        _recipe(python_abi=abi_form),
        platform="linux",
        architecture="x86_64",
        abi="cp313",
    )


def test_abi_string_form_is_tolerated() -> None:
    """单个字符串写法也接受（旧配方可能这么写）。"""
    check_compatibility(
        _recipe(python_abi="cp313"),
        platform="linux",
        architecture="x86_64",
        abi="cp313",
    )


def test_market_recipes_are_installable_on_this_interpreter() -> None:
    """**市场里的真实配方**必须能在当前解释器上通过兼容性检查。

    这条用真实数据：只测构造的 dict 发现不了"配方里的写法与解析器的假设不符"。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    components = market / "components"
    if not components.is_dir():
        pytest.skip("需要 market 仓的 components 目录")

    from omnicrawler.services.component_recipe import current_abi, current_architecture, current_platform

    checked = 0
    for recipe_path in sorted(components.glob("*/recipe.json")):
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        abi = recipe.get("python_abi")
        if not abi:
            continue  # 纯数据构件（如语言包）不声明 ABI
        checked += 1
        check_compatibility(
            recipe,
            platform=current_platform(),
            architecture=current_architecture(),
            abi=current_abi(),
        )
    if checked == 0:
        pytest.skip("当前没有声明 python_abi 的配方")

# ── 构件按平台筛选（一份 recipe 三平台各拉各的轮子） ─────────────────────
def test_artifact_without_platforms_applies_everywhere() -> None:
    """未标注 `platforms` 的构件对所有平台适用（向后兼容）。

    早期配方（如 tesseract 语言包、纯 Python 的 paddleocr）不该因为
    新增了平台筛选就装不上。
    """
    from omnicrawler.services.component_recipe import artifacts_for_platform

    recipe = {"artifacts": [{"url": "https://example.invalid/x.whl", "sha256": "0" * 64}]}
    for platform in ("windows", "linux", "macos"):
        assert len(artifacts_for_platform(recipe, platform)) == 1


def test_artifact_platform_filter_selects_one() -> None:
    """标注了 `platforms` 的构件只在列出的平台出现。"""
    from omnicrawler.services.component_recipe import artifacts_for_platform

    recipe = {
        "artifacts": [
            {"url": "https://example.invalid/win.whl", "sha256": "a" * 64, "platforms": ["windows"]},
            {"url": "https://example.invalid/lin.whl", "sha256": "b" * 64, "platforms": ["linux"]},
            {"url": "https://example.invalid/any.whl", "sha256": "c" * 64},
        ]
    }
    win = artifacts_for_platform(recipe, "windows")
    lin = artifacts_for_platform(recipe, "linux")
    assert [a["url"].rsplit("/", 1)[-1] for a in win] == ["win.whl", "any.whl"]
    assert [a["url"].rsplit("/", 1)[-1] for a in lin] == ["lin.whl", "any.whl"]


def test_platform_without_artifacts_is_rejected() -> None:
    """声明支持某平台却没有任何适用构件 ⇒ 校验必须拒绝。

    否则会「装上了但缺文件」，错误推迟到运行期才暴露。
    """
    from omnicrawler.services.component_recipe import RecipeError, validate_recipe

    recipe = {
        "schema": 1,
        "name": "x",
        "version": "1.0.0",
        "platforms": ["windows", "linux"],
        "architectures": ["x86_64"],
        "artifacts": [
            {"url": "https://example.invalid/w.whl", "sha256": "a" * 64, "platforms": ["windows"]}
        ],
        "entry": {"path": "entry.json"},
    }
    with pytest.raises(RecipeError) as excinfo:
        validate_recipe(recipe)
    assert "linux" in str(excinfo.value)


def test_market_paddle_recipe_covers_three_platforms() -> None:
    """市场里的 ocr-paddle 配方必须每个声明平台都有适用构件。

    这条用**真实配方**：上游给三平台发了不同轮子
    （win_amd64 / manylinux1_x86_64 / macosx_11_0_arm64）。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    recipe_path = market / "components" / "ocr-paddle" / "recipe.json"
    if not recipe_path.is_file():
        pytest.skip("需要 ocr-paddle 配方")

    from omnicrawler.services.component_recipe import artifacts_for_platform, validate_recipe

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    validate_recipe(recipe)  # 含「每个平台都要有构件」的检查

    for platform in recipe["platforms"]:
        items = artifacts_for_platform(recipe, platform)
        assert items, f"{platform} 没有适用构件"
        for item in items:
            assert item.get("sha256"), f"{platform} 的构件缺 sha256"

# ── 平台名必须用代码里认的那套（windows / linux / macos） ────────────────
def test_platform_names_use_canonical_spelling() -> None:
    """`platforms` 只能用 `windows` / `linux` / `macos`。

    **真实缺陷**：`tesseract-chi-sim` 的 `platforms` 曾写成
    `["windows", "linux", "darwin"]` —— `darwin` 是 `sys.platform` 的取值，
    而 `current_platform()` 返回 `macos`。写错一个词，macOS 上就永远装不上，
    而且 `check_compatibility` 只会说「不支持当前平台」，看不出是拼写问题。
    """
    from omnicrawler.services.component_recipe import current_platform

    canonical = {"windows", "linux", "macos"}
    assert current_platform() in canonical, "current_platform() 的取值不在规范集合内"

    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    components = market / "components"
    if not components.is_dir():
        pytest.skip("需要 market 仓的 components 目录")

    checked = 0
    for recipe_path in sorted(components.glob("*/recipe.json")):
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        platforms = recipe.get("platforms") or []
        checked += 1
        bad = [p for p in platforms if p not in canonical]
        assert not bad, (
            f"{recipe_path.parent.name} 的 platforms 含非规范名 {bad}；"
            f"允许 {sorted(canonical)}（`darwin` 是 sys.platform 的值，不是这里用的名字）"
        )
        for artifact in recipe.get("artifacts") or []:
            declared = artifact.get("platforms")
            if declared:
                bad_art = [p for p in declared if p not in canonical]
                assert not bad_art, (
                    f"{recipe_path.parent.name} 的某个构件 platforms 含非规范名 {bad_art}"
                )
    if checked == 0:
        pytest.skip("没有可检查的配方")

# ── 装配必须产出完整运行期元数据 ─────────────────────────────────────────
def test_assembly_manifest_declares_file_hashes() -> None:
    """`component.json` 必须声明每个文件的 sha256。

    `component_runner.verify_component_files()` 在**每次启动前**按它重算哈希
    （不信任「装配时校验过」）。若清单里没有 `files`，一 run 就报
    「component.json 未声明任何文件，拒绝加载」—— 真实 CI 踩到。

    这里做**真实装配**：造一个最小 zip 构件，走完整装配路径，检查产物。
    """
    import hashlib
    import io
    import json
    import zipfile
    from pathlib import Path

    from omnicrawler.services.component_recipe import install_from_recipe

    tmp = Path(__file__).resolve().parent / ".pytest_component_assembly"
    work = tmp
    work.mkdir(parents=True, exist_ok=True)
    try:
        # 造一个含模块文件的 zip 构件
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            bundle.writestr("pkg/__init__.py", "# demo\n")
        blob = payload.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        archive = work / "artifact.zip"
        archive.write_bytes(blob)

        recipe = {
            "schema": 1,
            "name": "demo",
            "version": "1.0.0",
            "platforms": ["windows", "linux", "macos"],
            "architectures": ["x86_64"],
            "artifacts": [
                {
                    "url": "https://example.invalid/artifact.zip",
                    "sha256": digest,
                    "extract_to": "site-packages",
                }
            ],
            "entry": {
                "path": "entry.json",
                "protocol": "demo-v1",
                "engine": "generic",
                "runner": "worker-wheelpack",
                "module": "site-packages/pkg/__init__.py",
            },
        }

        def downloader(_url: str, dest: Path) -> None:
            dest.write_bytes(blob)

        component_root = work / "components"
        component_root.mkdir(parents=True, exist_ok=True)
        summary = install_from_recipe(
            recipe, component_root, downloader=downloader, work_dir=work
        )
        installed = Path(summary["path"])

        manifest = json.loads((installed / "component.json").read_text(encoding="utf-8"))
        assert manifest.get("files"), "component.json 未声明任何文件"
        for relative, value in manifest["files"].items():
            assert str(value).startswith("sha256:"), f"{relative} 缺少 sha256 前缀"
            assert (installed / relative).is_file(), f"{relative} 声明了但不存在"

        entry = json.loads((installed / "entry.json").read_text(encoding="utf-8"))
        assert entry.get("entry") == "site-packages/pkg/__init__.py"
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
