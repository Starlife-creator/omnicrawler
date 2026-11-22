"""条目 5 形态 B：**声明式 recipe 装配**——宿主只维护"怎么组装"。

**定位（与用户的决定一致）**

> 下载也从上游拉；需要组装就在本地组装；我们只维护组装方式。

所以本模块**不做**：
* 不托管任何字节（没有我们的 CDN/镜像）；
* 不内嵌可执行脚本（recipe 是**声明式**的，不是 `install.sh`）——
  给脚本执行权限等于在信任边界上开任意执行口。

**recipe 必须能回答的四件事**

1. **从哪拉**：每个构件一条「URL + 内容哈希」；
2. **怎么拼**：解压到哪、只取哪些路径（避免 zip-slip 与路径穿越）；
3. **对不对**：ABI/平台/架构标签必须与当前解释器匹配；
4. **装到哪**：固定落在组件根目录下的独立子目录。

**本文件钉住的判据**

* recipe **不是**可执行文件格式（拒绝 `.sh/.bat/.py/.exe`）；
* 每个构件都**必须**有 sha256，缺一条就拒绝整份 recipe（不能部分信任）；
* 平台/架构/ABI 不匹配时**拒绝装配**，而不是"装上去再报错"；
* 解压严格限制在目标目录内（防路径穿越）；
* 装配是**两阶段**：先全部校验 → 再落盘；中途失败不留半成品。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from omnicrawler.services.component_recipe import current_architecture, current_platform


def _recipe(**overrides) -> dict:
    """一份最小合法 recipe。"""
    base = {
        "schema": 1,
        # `kind` 与 `smoke` 是后加的**必填**字段：
        # kind=wheel-pack 必须声明 smoke（「能不能跑」由组件自己承诺）。
        # 这个 fixture 是 8 条用例的共同构造器，缺了它们会全体先被拒绝，
        # 测不到各自真正想测的判据。
        "kind": "wheel-pack",
        "smoke": {"expect": ["ok"]},
        "name": "demo-ocr",
        "version": "1.0.0",
        "platforms": [current_platform()],
        "architectures": [current_architecture()],
        "python_abi": "cp312",
        "artifacts": [
            {
                "url": "https://example.org/demo.zip",
                "sha256": "a" * 64,
                "size": 1234,
                "extract_to": "runtime",
            }
        ],
        # `entry.runner` 也是后加的必填判据：kind=wheel-pack 的入口必须
        # 由宿主的 worker 挂载执行（组件不自带脚本，见 asset_trust 纪律）。
        "entry": {
            "path": "runtime/ocr.exe",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "runner": "worker-wheelpack",
        },
    }
    base.update(overrides)
    return base


def test_recipe_is_declarative_not_executable() -> None:
    """recipe 必须是声明式格式；带可执行脚本的 recipe 一律拒绝。"""
    from omnicrawler.services.component_recipe import RecipeError, validate_recipe

    poisoned = _recipe()
    poisoned["post_install"] = "install.sh"  # 可执行钩子
    with pytest.raises(RecipeError, match="声明式|可执行|不允许"):
        validate_recipe(poisoned)


@pytest.mark.parametrize("bad_ext", [".sh", ".bat", ".py", ".exe", ".ps1"])
def test_recipe_file_must_not_be_executable(bad_ext: str, tmp_path: Path) -> None:
    """recipe 文件本身不能是可执行脚本。"""
    from omnicrawler.services.component_recipe import RecipeError, load_recipe

    path = tmp_path / f"recipe{bad_ext}"
    path.write_text("echo hi", encoding="utf-8")
    with pytest.raises(RecipeError):
        load_recipe(path)


def test_every_artifact_requires_sha256() -> None:
    """缺哈希就拒绝整份 recipe——不能"部分信任"。"""
    from omnicrawler.services.component_recipe import RecipeError, validate_recipe

    missing = _recipe(artifacts=[{"url": "https://example.org/a.zip", "extract_to": "runtime"}])
    with pytest.raises(RecipeError, match="sha256|哈希"):
        validate_recipe(missing)

    bad = _recipe(artifacts=[{"url": "https://example.org/a.zip", "sha256": "xyz",
                              "extract_to": "runtime"}])
    with pytest.raises(RecipeError, match="sha256|哈希"):
        validate_recipe(bad)


def test_platform_mismatch_is_refused_before_extraction() -> None:
    """平台不匹配时**在装配前**拒绝，而不是装完再报错。"""
    from omnicrawler.services.component_recipe import RecipeError, check_compatibility

    recipe = _recipe(platforms=["plan9"])
    with pytest.raises(RecipeError, match="平台|platform"):
        check_compatibility(recipe, platform="windows", architecture="x86_64", abi="cp312")


def test_abi_mismatch_is_refused() -> None:
    """ABI 与当前解释器不符时拒绝（cp313 的包不能装进 cp312 的宿主）。"""
    from omnicrawler.services.component_recipe import RecipeError, check_compatibility

    recipe = _recipe(python_abi="cp313")
    with pytest.raises(RecipeError, match="ABI|abi|Python"):
        check_compatibility(recipe, platform="windows", architecture="x86_64", abi="cp312")


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    """解压必须防路径穿越（zip-slip）。"""
    from omnicrawler.services.component_recipe import extract_artifact

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escape.txt", b"pwned")

    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(Exception) as excinfo:
        extract_artifact(archive, target)
    message = str(excinfo.value)
    assert ("不安全" in message) or ("路径" in message) or ("traversal" in message.lower())
    assert not (tmp_path / "escape.txt").exists(), "文件逃出了目标目录"


def test_extract_verifies_hash_before_writing(tmp_path: Path) -> None:
    """哈希不符时不得落盘（两阶段：先校验，再写）。"""
    from omnicrawler.services.component_recipe import RecipeError, extract_artifact

    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("data.bin", b"real-content")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()

    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(RecipeError, match="哈希|sha256"):
        extract_artifact(archive, target, expected_sha256="0" * 64)
    assert list(target.iterdir()) == [], "哈希不符却已经落盘"

    # 用正确哈希则成功
    extract_artifact(archive, target, expected_sha256=actual)
    assert (target / "data.bin").read_bytes() == b"real-content"


def test_recipe_round_trip_is_stable(tmp_path: Path) -> None:
    """recipe 的规范化序列化必须稳定（哈希可复现）。"""
    from omnicrawler.services.component_recipe import recipe_digest

    first = recipe_digest(_recipe())
    second = recipe_digest(_recipe())
    assert first == second
    # 改一个字段必须改变摘要
    assert recipe_digest(_recipe(version="1.0.1")) != first


def test_load_recipe_reads_json_and_rejects_unknown_schema(tmp_path: Path) -> None:
    """只接受受支持的 schema 版本——未知版本必须拒绝。"""
    from omnicrawler.services.component_recipe import RecipeError, load_recipe

    good = tmp_path / "recipe.json"
    good.write_text(json.dumps(_recipe()), encoding="utf-8")
    loaded = load_recipe(good)
    assert loaded["name"] == "demo-ocr"

    future = tmp_path / "future.json"
    future.write_text(json.dumps(_recipe(schema=99)), encoding="utf-8")
    with pytest.raises(RecipeError, match="schema|版本"):
        load_recipe(future)


# ── 端到端装配 ────────────────────────────────────────────────────────────


def _build_artifact(tmp_path: Path, name: str = "artifact.zip") -> tuple[Path, str]:
    """造一个构件归档，返回 (路径, sha256)。"""
    import zipfile

    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ocr.exe", b"fake-binary")
        zf.writestr("models/weights.bin", b"w" * 128)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, digest


def test_install_from_recipe_end_to_end(tmp_path: Path) -> None:
    """完整装配：下载 → 校验 → 解压 → 入口存在。

    这是本条目的**唯一实证**——recipe 说得再对，装不出东西就是没写。
    :param downloader: 用假下载器（本模块**刻意不内置网络出口**）。
    """

    from omnicrawler.services.component_recipe import (
        current_abi,
        current_architecture,
        current_platform,
        install_from_recipe,
    )

    archive, digest = _build_artifact(tmp_path)
    recipe = _recipe(
        platforms=[current_platform()],
        architectures=[current_architecture()],
        python_abi=current_abi(),
        artifacts=[{
            "url": "https://example.org/artifact.zip",
            "sha256": digest,
            "size": archive.stat().st_size,
            "extract_to": "runtime",
        }],
        entry={
            "path": "runtime/ocr.exe",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "runner": "worker-wheelpack",
        },
    )

    def fake_download(url: str, dest: Path) -> None:
        assert url == "https://example.org/artifact.zip"
        dest.write_bytes(archive.read_bytes())

    root = tmp_path / "components"
    root.mkdir()
    summary = install_from_recipe(recipe, root, downloader=fake_download)

    assert summary["name"] == "demo-ocr"
    assert Path(summary["entry"]).is_file(), "装配完成但入口不存在"
    assert Path(summary["entry"]).read_bytes() == b"fake-binary"
    # 模型文件也应就位
    assert (Path(summary["path"]) / "runtime" / "models" / "weights.bin").is_file()


def test_install_refuses_bad_hash_before_extracting(tmp_path: Path) -> None:
    """构件哈希不符时整体放弃，**不留半装目录**。"""
    from omnicrawler.services.component_recipe import (
        RecipeError,
        current_abi,
        current_architecture,
        current_platform,
        install_from_recipe,
    )

    archive, _ = _build_artifact(tmp_path)
    recipe = _recipe(
        platforms=[current_platform()],
        architectures=[current_architecture()],
        python_abi=current_abi(),
        artifacts=[{
            "url": "https://example.org/artifact.zip",
            "sha256": "0" * 64,  # 故意错
            "extract_to": "runtime",
        }],
    )

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(archive.read_bytes())

    root = tmp_path / "components"
    root.mkdir()
    with pytest.raises(RecipeError, match="哈希|sha256"):
        install_from_recipe(recipe, root, downloader=fake_download)

    leftovers = [p for p in root.iterdir() if p.name.startswith("demo-ocr")]
    assert leftovers == [], f"哈希不符却留下了装配目录：{leftovers}"


def test_install_requires_explicit_downloader(tmp_path: Path) -> None:
    """不给下载器就拒绝——装配器不该成为第二个网络出口。"""
    from omnicrawler.services.component_recipe import (
        RecipeError,
        current_abi,
        current_architecture,
        current_platform,
        install_from_recipe,
    )

    recipe = _recipe(
        platforms=[current_platform()],
        architectures=[current_architecture()],
        python_abi=current_abi(),
    )
    root = tmp_path / "components"
    root.mkdir()
    with pytest.raises(RecipeError, match="下载器|downloader"):
        install_from_recipe(recipe, root)


def test_install_verifies_hashes_itself_not_only_via_extract(tmp_path: Path, monkeypatch) -> None:
    """`install_from_recipe` 必须**自己**在下载阶段就校验哈希。

    **这条测试的存在理由（一次真实的守卫缺口）**

    反向断言时我只把 `install_from_recipe` 的阶段一比较改成 `True`
    （即"下载后不校验、交给后面解压时校验"），期望某条测试变红——
    结果**全绿**。

    原因：`extract_artifact` 也会比对同一个期望值，于是"阶段一被削弱"
    这个事实被下游兜住了。但两者的差别是真实存在的：

    * 阶段一拦截 ⇒ 坏构件在**下载完立刻**被发现，不必解压任何东西；
    * 只靠下游拦截 ⇒ 前面的合法构件已经解压进暂存区，
      多构件时白做一遍解压（大构件是几百 MB 级）。

    所以这里**把下游的校验摘掉**（monkeypatch 掉 expected_sha256），
    让阶段一成为唯一防线——这样它若被削弱，本测试必红。
    """
    from omnicrawler.services import component_recipe as mod

    good, good_digest = _build_artifact(tmp_path, "good.zip")

    recipe = _recipe(
        platforms=[mod.current_platform()],
        architectures=[mod.current_architecture()],
        python_abi=mod.current_abi(),
        artifacts=[{
            "url": "https://example.org/good.zip",
            "sha256": good_digest,
            "extract_to": "runtime",
        }],
        entry={
            "path": "runtime/ocr.exe",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "runner": "worker-wheelpack",
        },
    )

    real_extract = mod.extract_artifact

    def extract_without_hash_check(archive, target, *, expected_sha256=None, include=None):
        # 摘掉下游校验，让阶段一成为唯一防线
        return real_extract(archive, target, expected_sha256=None, include=include)

    monkeypatch.setattr(mod, "extract_artifact", extract_without_hash_check)

    def fake_download(url: str, dest: Path) -> None:
        # 内容与声明哈希不符（写入不同字节）
        dest.write_bytes(good.read_bytes() + b"tampered")

    root = tmp_path / "components"
    root.mkdir()
    with pytest.raises(mod.RecipeError, match="哈希|sha256"):
        mod.install_from_recipe(recipe, root, downloader=fake_download)


def test_entry_runner_validation() -> None:
    """entry.runner 仅允许受支持的运行器枚举（native / worker-wheelpack）。"""
    from omnicrawler.services.component_recipe import RecipeError, validate_recipe

    valid_native = _recipe(entry={"path": "ocr.exe", "runner": "native"})
    assert validate_recipe(valid_native)["entry"]["runner"] == "native"

    valid_worker = _recipe(entry={"path": "runner.py", "runner": "worker-wheelpack"})
    assert validate_recipe(valid_worker)["entry"]["runner"] == "worker-wheelpack"

    invalid = _recipe(entry={"path": "ocr.exe", "runner": "arbitrary-eval"})
    with pytest.raises(RecipeError, match="entry.runner 不受支持"):
        validate_recipe(invalid)


def test_raw_single_file_artifact_assembly(tmp_path: Path) -> None:
    """raw 构件：单文件原子落地、支持 filename、且防路径穿越。"""
    from omnicrawler.services.component_recipe import (
        RecipeError,
        current_abi,
        current_architecture,
        current_platform,
        install_from_recipe,
        validate_recipe,
    )

    raw_file = tmp_path / "eng.traineddata"
    raw_payload = b"fake-tessdata-traineddata"
    raw_file.write_bytes(raw_payload)
    digest = hashlib.sha256(raw_payload).hexdigest()

    recipe = _recipe(
        platforms=[current_platform()],
        architectures=[current_architecture()],
        python_abi=current_abi(),
        artifacts=[
            {
                "url": "https://example.org/eng.traineddata",
                "sha256": digest,
                "size": len(raw_payload),
                "format": "raw",
                "extract_to": "tessdata",
                "filename": "eng.traineddata",
            }
        ],
        entry={
            "path": "tessdata/eng.traineddata",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "runner": "worker-wheelpack",
        },
    )
    validate_recipe(recipe)

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(raw_file.read_bytes())

    root = tmp_path / "components"
    root.mkdir()
    summary = install_from_recipe(recipe, root, downloader=fake_download)
    installed_entry = Path(summary["entry"])
    assert installed_entry.is_file()
    assert installed_entry.read_bytes() == raw_payload

    # 反向断言：raw 构件试图通过 filename 穿越目录
    evil_recipe = _recipe(
        artifacts=[
            {
                "url": "https://example.org/eng.traineddata",
                "sha256": digest,
                "format": "raw",
                "extract_to": "tessdata",
                "filename": "../escape.bin",
            }
        ]
    )
    with pytest.raises(RecipeError, match="单个安全文件名|路径穿越"):
        validate_recipe(evil_recipe)


def test_tar_gz_artifact_assembly_and_strip_components(tmp_path: Path) -> None:
    """tar.gz 构件：流式安全提取、支持 strip_components 剥离外层目录。"""
    import io
    import tarfile

    from omnicrawler.services.component_recipe import (
        current_abi,
        current_architecture,
        current_platform,
        install_from_recipe,
    )

    tar_path = tmp_path / "bundle.tar.gz"
    payload = b"tar-binary-content"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="bundle-v1/bin/tool.exe")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    recipe = _recipe(
        platforms=[current_platform()],
        architectures=[current_architecture()],
        python_abi=current_abi(),
        artifacts=[
            {
                "url": "https://example.org/bundle.tar.gz",
                "sha256": digest,
                "format": "tar.gz",
                "strip_components": 1,
                "extract_to": "runtime",
            }
        ],
        entry={
            "path": "runtime/bin/tool.exe",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "runner": "worker-wheelpack",
        },
    )

    def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(tar_path.read_bytes())

    root = tmp_path / "components_tar"
    root.mkdir()
    summary = install_from_recipe(recipe, root, downloader=fake_download)
    assert (Path(summary["path"]) / "runtime" / "bin" / "tool.exe").read_bytes() == payload

