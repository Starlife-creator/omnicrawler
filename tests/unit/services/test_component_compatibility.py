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

# ── Wheel-Pack 由宿主 worker 执行（组件不自带脚本） ────────────────────────
def test_runner_uses_host_worker_not_component_script() -> None:
    """runner 必须执行**宿主提供的 worker**，不得直接跑组件里的文件。

    早先是 `python <spec.entry>` —— 要求 entry 是可执行脚本。而 Wheel-Pack
    组件的 entry 是 `paddleocr/__init__.py` 这种**库入口**，直接跑会触发
    它自己的命令行解析（真实 CI 报 argparse 错误）。

    让组件自带脚本更糟：recipe 来自市场，允许下发脚本等于允许第三方代码执行
    （`asset_trust.py` 铁律禁止）。所以「怎么执行」必须固定在宿主。
    """
    import pathlib as _pathlib

    runner = _pathlib.Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "services" / "component_runner.py"
    assert runner.is_file()
    source = runner.read_text(encoding="utf-8")

    assert "omnicrawler.services.component_worker" in source, (
        "runner 没有调用宿主 worker —— 会退回到直接执行组件文件"
    )
    assert "[sys.executable, str(spec.entry)]" not in source, (
        "runner 仍在直接执行组件路径 —— Wheel-Pack 的库入口不是脚本"
    )


def test_worker_module_name_resolution() -> None:
    """`site-packages/xxx/__init__.py` 要解析成可 import 的 `xxx`。"""
    from pathlib import Path

    from omnicrawler.services.component_worker import _resolve_module_name

    site = Path("/tmp/sp")
    assert _resolve_module_name(site / "paddleocr" / "__init__.py", site) == "paddleocr"
    assert _resolve_module_name(site / "a" / "b" / "__init__.py", site) == "a.b"
    assert _resolve_module_name(site / "single.py", site) == "single"


def test_worker_rejects_path_escape() -> None:
    """入口不在 site-packages 内必须拒绝（防 `../` 越界）。"""
    from pathlib import Path

    import pytest as _pytest

    from omnicrawler.services.component_worker import _resolve_module_name

    site = Path("/tmp/sp")
    with _pytest.raises(SystemExit):
        _resolve_module_name(Path("/tmp/elsewhere/x.py"), site)


def test_worker_engine_registry_is_not_empty() -> None:
    """engine 适配器表不能为空 —— 否则任何组件都跑不起来。"""
    from omnicrawler.services.component_worker import _ADAPTERS

    assert "generic" in _ADAPTERS
    assert "paddle" in _ADAPTERS

# ── 冻结环境：不能用 `python -m`（宿主 exe 不认） ────────────────────────
def test_worker_command_handles_frozen() -> None:
    """冻结包里必须走 CLI 内部 dispatch，而不是 `python -m`。

    **真实 CI 踩到**：冻结包的 `sys.executable` 是宿主 exe 本身，
    `[sys.executable, "-m", "omnicrawler.services.component_worker"]`
    会变成 `omnicrawler-cli -m ...` —— CLI 不认识 `-m`，argparse 报
    `invalid choice`，日志里只剩一长串子命令列表。
    """
    import sys as _sys

    from omnicrawler.services import component_runner as runner

    original = getattr(_sys, "frozen", None)
    try:
        _sys.frozen = True  # type: ignore[attr-defined]
        command = runner._worker_command()
        assert "-m" not in command, f"冻结环境仍在用 -m：{command}"
        assert command[0] == _sys.executable
        assert (
            command[1] == runner._cli_internal_command()
        ), "冻结环境应使用 CLI 的内部 dispatch 命令"
    finally:
        if original is None:
            del _sys.frozen  # type: ignore[attr-defined]
        else:
            _sys.frozen = original  # type: ignore[attr-defined]


def test_worker_command_in_source_env() -> None:
    """源码环境仍用 `python -m`（可读、可调试）。"""

    from omnicrawler.services import component_runner as runner

    command = runner._worker_command()
    assert "-m" in command
    assert "omnicrawler.services.component_worker" in command


def test_internal_commands_are_dispatchable() -> None:
    """CLI 内部 dispatch 表里的每个模块都必须能 import 且有 main()。"""

    from omnicrawler.cli import _main as cli_main
    from omnicrawler.services import component_runner as runner

    assert runner.INTERNAL_WORKER_COMMAND == cli_main.INTERNAL_WORKER_COMMAND, (
        f"常量漂移：services={runner.INTERNAL_WORKER_COMMAND!r} "
        f"vs cli={cli_main.INTERNAL_WORKER_COMMAND!r}"
    )
    assert runner.INTERNAL_WORKER_COMMAND in cli_main._INTERNAL_COMMANDS, (
        "CLI 没有注册这个内部命令"
    )


def test_services_does_not_import_cli() -> None:
    """`services` 层不得 import `omnicrawler.cli`。

    否则架构门禁的环预算会被顶超。这条是上面那个「两侧各写常量」决定的守门人 ——
    没有它，后来者「顺手」加一句 `from ..cli import _main` 就会静默破坏分层。
    """
    import pathlib as _pathlib

    services = _pathlib.Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "services"
    offenders = []
    for path in services.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from ..cli" in stripped or "from omnicrawler.cli" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, f"services 层依赖了 CLI：{offenders}"

def test_paddle_probe_does_not_check_whole_machine() -> None:
    """paddle 适配器不得调用 `paddle.utils.run_check()`。

    那个函数检查的是**整机运行时**（Tesseract / ChromeDriver / CUDA 等），
    而 Standard 包本来就不带 Tesseract/ChromeDriver —— 用它判组件成败，
    会把「组件没问题」误报成「组件坏了」（真实 CI 踩到）。

    组件要证明的只是「它的原生扩展能在隔离子进程里 import 成功」。
    """
    import pathlib as _pathlib

    worker = (
        _pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "omnicrawler"
        / "services"
        / "component_worker.py"
    )
    source = worker.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "run_check()" not in code, (
        "仍在校验整机运行时 —— 会把组件自身没问题误报成失败"
    )

def test_worker_error_extracted_from_stdout() -> None:
    """worker 自报的错误必须从 **stdout 的 JSON** 里取，不能被 stderr 告警淹没。

    **真实 CI**：`组件 ocr-paddle 退出码 1：` 后面跟的全是
    `内置 Tesseract 缺失` / `内置 ChromeDriver 缺失` 这类无关告警，
    真正的失败原因（stage + error）在 stdout 的 JSON 里，被完全忽略。
    """
    from omnicrawler.services.component_runner import _extract_worker_error

    payload = '{"ok": false, "stage": "import_component", "error": "ImportError: boom"}'
    detail = _extract_worker_error(payload)
    assert "import_component" in detail
    assert "ImportError: boom" in detail


def test_worker_error_ignores_success_payload() -> None:
    """成功的 JSON 不算错误（否则正常路径会被误报成失败）。"""
    from omnicrawler.services.component_runner import _extract_worker_error

    assert _extract_worker_error('{"ok": true, "engine": "generic"}') == ""


def test_worker_error_ignores_non_json_stdout() -> None:
    """stdout 不是 JSON 时返回空串（由调用方退回 stderr）。"""
    from omnicrawler.services.component_runner import _extract_worker_error

    assert _extract_worker_error("not json at all") == ""
    assert _extract_worker_error("") == ""

def test_paddle_recipe_declares_full_dependency_chain() -> None:
    """`ocr-paddle` 配方必须覆盖 `paddleocr` 的**硬依赖**。

    真实 CI 报 `[import_component] ModuleNotFoundError: No module named 'paddlex'`：
    `paddleocr 3.7.0` 的 requires_dist 是 `paddlex[ocr-core]<3.8.0,>=3.7.0`，
    而配方原本只声明了 paddlepaddle + paddleocr 两个 wheel ——
    装得上，但 import 就炸。

    这条守卫用**真实配方**，逐个检查 paddleocr 声明的必需依赖是否都在
    配方里出现（按包名前缀匹配 wheel 文件名）。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    recipe_path = market / "components" / "ocr-paddle" / "recipe.json"
    if not recipe_path.is_file():
        pytest.skip("需要 ocr-paddle 配方")

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    filenames = " ".join(a["url"].rsplit("/", 1)[-1].lower() for a in recipe["artifacts"])

    # paddleocr 3.7.0 的必需依赖（来自 PyPI requires_dist）
    required = [
        "paddlex",
        "pyyaml",
        "requests",
        "aiohttp",
        "typing-extensions",
        # paddlex[ocr-core] 的额外依赖
        "opencv-contrib-python",
        "pyclipper",
        "pypdfium2",
        "python-bidi",
        "shapely",
        "imagesize",
        "pandas",
        "prettytable",
        "py-cpuinfo",
        "pydantic",
        # paddlex 自身的必装依赖（pypi requires_dist 无 extra 条件部分）
        "aistudio-sdk",
        "chardet",
        "colorlog",
        "filelock",
        "huggingface-hub",
        "modelscope",
        "numpy",
        "packaging",
        "pillow",
        "ruamel.yaml",
        "ujson",
        # 传递依赖（颜色/HTTP/加密/工具链）
        "click",
        "colorama",
        "cryptography",
        "frozenlist",
        "fsspec",
        "h11",
        "idna",
        "multidict",
        "propcache",
        "psutil",
        "pycryptodome",
        "pydantic-core",
        "python-dateutil",
        "six",
        "tqdm",
        "typing-inspection",
        "tzdata",
        "urllib3",
        "wcwidth",
        "yarl",
        # 闭包补齐（pydantic / aiohttp / cryptography 的传递依赖）
        "aiohappyeyeballs",
        "aiosignal",
        "annotated-types",
        "anyio",
        "attrs",
        "bce-python-sdk",
        "certifi",
        "cffi",
        "charset-normalizer",
    ]
    def normalized(text: str) -> str:
        """包名归一：`-` / `.` / `_` 在 wheel 文件名里可互换。

        PyPI 的规范名是 `ruamel.yaml`，而 wheel 文件名写 `ruamel_yaml`
        —— 只换 `-` 会漏掉这一条。
        """
        return text.replace("-", "_").replace(".", "_").lower()

    haystack = normalized(filenames)
    missing = [name for name in required if normalized(name) not in haystack]
    assert not missing, f"配方缺少 paddleocr 的硬依赖：{missing}"

def test_paddle_recipe_wheels_are_abi_compatible() -> None:
    """配方里的 wheel 必须能在 **cp313** 上加载。

    真实坑：我第一版按「平台标签」选轮子，给 pyyaml / pandas 选中了
    `cp310` / `cp311` 的轮子 —— 平台对了，但 ABI 不对，3.13 下 import 就炸。

    判据（三者之一即可）：
    * `py3-none-any` —— 纯 Python；
    * `py3-none-<platform>` —— 纯 Python + 平台标签（如 pypdfium2，
      它加载的是独立 .so/.dll，不依赖 CPython ABI）；
    * 含 `cp313` 或 `abi3` —— 明确兼容当前解释器。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    recipe_path = market / "components" / "ocr-paddle" / "recipe.json"
    if not recipe_path.is_file():
        pytest.skip("需要 ocr-paddle 配方")

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    bad = []
    for artifact in recipe["artifacts"]:
        filename = artifact["url"].rsplit("/", 1)[-1].lower()
        if not filename.endswith(".whl"):
            continue
        if "py3-none-" in filename:
            continue
        if "cp313" in filename or "abi3" in filename:
            continue
        bad.append(filename)
    assert not bad, f"这些 wheel 在 cp313 上不可用：{bad}"

def test_multiple_artifacts_into_same_dir_are_merged() -> None:
    """多个构件解到**同一个目录**时必须合并，不能后一个删掉前一个。

    **真实 CI**：ocr-paddle 的 17 个 wheel 全部 `extract_to: site-packages`。
    早先 `extract_artifact` 在目标已存在时 `rmtree(target)` 再 `replace()`，
    于是**最后一个构件把前面解出来的内容整目录删了** ——
    `paddleocr/` 就这样消失，装完后报
    「组件入口不存在：site-packages/paddleocr/__init__.py」。

    这条守卫做**真实装配**：两个构件解到同一目录，断言两者内容都在。
    """
    import hashlib
    import io
    import json
    import shutil
    import zipfile
    from pathlib import Path

    from omnicrawler.services.component_recipe import install_from_recipe

    work = Path(__file__).resolve().parent / ".pytest_merge_check"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        def make_zip(prefix: str) -> tuple[bytes, str]:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as bundle:
                bundle.writestr(f"{prefix}/__init__.py", f"# {prefix}\n")
            blob = buf.getvalue()
            return blob, hashlib.sha256(blob).hexdigest()

        blob_a, sha_a = make_zip("alpha")
        blob_b, sha_b = make_zip("beta")

        recipe = {
            "schema": 1,
            "name": "merge-check",
            "version": "1.0.0",
            "platforms": ["windows", "linux", "macos"],
            "architectures": ["x86_64"],
            "artifacts": [
                {
                    "url": "https://example.invalid/a.zip",
                    "sha256": sha_a,
                    "extract_to": "site-packages",
                },
                {
                    "url": "https://example.invalid/b.zip",
                    "sha256": sha_b,
                    "extract_to": "site-packages",
                },
            ],
            "entry": {
                "path": "entry.json",
                "protocol": "demo",
                "engine": "generic",
                "runner": "worker-wheelpack",
                "module": "site-packages/alpha/__init__.py",
            },
        }

        blobs = {"https://example.invalid/a.zip": blob_a, "https://example.invalid/b.zip": blob_b}

        def downloader(url: str, dest: Path) -> None:
            dest.write_bytes(blobs[url])

        components = work / "components"
        components.mkdir()
        summary = install_from_recipe(recipe, components, downloader=downloader, work_dir=work)
        installed = Path(summary["path"])

        assert (installed / "site-packages" / "alpha" / "__init__.py").is_file(), (
            "第一个构件的内容被后一个删掉了"
        )
        assert (installed / "site-packages" / "beta" / "__init__.py").is_file(), (
            "第二个构件的内容缺失"
        )

        # component.json 必须覆盖合并后的全部文件
        manifest = json.loads((installed / "component.json").read_text(encoding="utf-8"))
        declared = set(manifest["files"])
        assert "site-packages/alpha/__init__.py" in declared
        assert "site-packages/beta/__init__.py" in declared
    finally:
        shutil.rmtree(work, ignore_errors=True)

def test_paddle_recipe_wheels_match_platform_arch() -> None:
    """每个平台的 wheel 必须匹配该平台**架构**，不只是平台名。

    **真实 CI**：`ModuleNotFoundError: No module named 'bidi.bidi'` ——
    表面像缺包，实为选中的是 `manylinux_2_17_aarch64` 轮子，
    x86_64 runner 加载不了那个 `.so`，Python 只能报「子模块不存在」。

    判据（对每个平台筛选后的构件）：
    * linux ⇒ 含 `manylinux` 且含 `x86_64`，不含 `aarch64`；
    * windows ⇒ 含 `win_amd64`；
    * macos ⇒ 含 `macosx`；
    * 任何平台都不得是 `cp313t`（自由线程，普通解释器加载不了）。
    """
    import json
    from pathlib import Path

    from omnicrawler.services.component_recipe import artifacts_for_platform

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    recipe_path = market / "components" / "ocr-paddle" / "recipe.json"
    if not recipe_path.is_file():
        pytest.skip("需要 ocr-paddle 配方")

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for platform in ("windows", "linux", "macos"):
        for artifact in artifacts_for_platform(recipe, platform):
            name = artifact["url"].rsplit("/", 1)[-1].lower()
            if not name.endswith(".whl"):
                continue
            if "cp313t" in name or "cp314" in name:
                problems.append(f"{platform}: 自由线程版 {name}")
                continue
            if "none-" in name or "abi3" in name:
                # 纯 Python / abi3 仍需平台标签正确
                if platform == "linux" and ("macosx" in name or "win" in name):
                    problems.append(f"{platform}: 平台不符 {name}")
                if platform == "windows" and "macosx" in name:
                    problems.append(f"{platform}: 平台不符 {name}")
                if platform == "macos" and ("manylinux" in name or "win_" in name):
                    problems.append(f"{platform}: 平台不符 {name}")
                continue
            if platform == "linux" and (
                "manylinux" not in name or "x86_64" not in name or "aarch64" in name
            ):
                problems.append(f"linux: 架构不符 {name}")
            if platform == "windows" and "win_amd64" not in name:
                problems.append(f"windows: 架构不符 {name}")
            if platform == "macos" and "macosx" not in name:
                problems.append(f"macos: 平台不符 {name}")
    assert not problems, "轮子与平台/架构不匹配：\n" + "\n".join(problems)

def test_component_env_does_not_inherit_host_library_path() -> None:
    """组件子进程**不得继承宿主的 LD_LIBRARY_PATH / DYLD_LIBRARY_PATH**。

    **真实 CI**：组件 run 阶段 `exit 1`，日志里真正的错误是

        cat: .../_internal/libsystemd.so.0: version `LIBSYSTEMD_254' not found

    冻结包里 `LD_LIBRARY_PATH` 指向包内 `_internal/`（含 PyInstaller 收集的
    系统库）。组件子进程是真正的 python 解释器，继承这条路径后，
    它启动的任何子命令都会先命中包内旧版库 —— 组件本身没问题却跑不起来。
    """
    import os as _os
    from pathlib import Path as _Path

    from omnicrawler.services.component_runner import (
        WheelPackSpec,
        component_runner_env,
    )

    spec = WheelPackSpec(
        name="demo",
        version="1.0.0",
        component_root=_Path("/tmp/demo"),
        site_packages=_Path("/tmp/demo/site-packages"),
        entry=_Path("/tmp/demo/site-packages/x.py"),
        engine="generic",
    )

    # 模拟冻结包里的宿主环境
    original = _os.environ.get("LD_LIBRARY_PATH")
    _os.environ["LD_LIBRARY_PATH"] = "/bundle/_internal"
    try:
        env = component_runner_env(spec)
    finally:
        if original is None:
            del _os.environ["LD_LIBRARY_PATH"]
        else:
            _os.environ["LD_LIBRARY_PATH"] = original

    assert "/bundle/_internal" not in env["LD_LIBRARY_PATH"], (
        "宿主打包用的库路径泄漏进了组件子进程"
    )
    assert str(spec.site_packages) in env["LD_LIBRARY_PATH"], (
        "组件 site-packages 未进入库搜索路径"
    )
