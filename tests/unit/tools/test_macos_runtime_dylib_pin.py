"""macOS 便携运行时的 **dylib 依赖钉住**判据（W4.2，2026-09-17）。

## 这里守的是一个**真产品缺陷**

归档级冒烟（CI run `35165407069`，`portable-smoke (macos-latest)`）在**干净机器**上拿到：

```
Library not loaded: /opt/homebrew/Cellar/tesseract/5.5.3/lib/libtesseract.5.dylib
```

`runtime-verify` 同时报告 `missing_native: []` —— 也就是**文件都在**（归档没丢东西），
但包内 tesseract 仍引用**构建机的 Homebrew 绝对路径**。根因在
`packaging/prepare_macos_runtime.sh`：`resolve_dylibs` 会把这种绝对路径依赖**拷贝**进来，
而**重写循环只匹配 `@rpath/*`** ⇒ **拷了却没改** ⇒ 构建机上能跑（brew 在）、干净机器上 dyld 直接失败。

⇒ 自称"自包含便携包"其实不自包含。构建期冒烟**看不出来**（构建机有 brew），
只有"下载已上传的归档、在干净机器上跑"这一步才会暴露 —— 这正是 W4.2 存在的理由。

## 判据

与 `tools/fix_macos_bundle_openssl.py`（W4.1 的 `_ssl` 修复）**同一条原则**：
**"看起来正常" ≠ "运行时正确"**，判据必须是确定性的 ——
"**该 basename 在本目录有副本 ⇒ 改写**"，而不是"路径长得像 `@rpath` 才改写"。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PREPARE = _REPO_ROOT / "packaging" / "prepare_macos_runtime.sh"
_OPENSSL_FIXER = _REPO_ROOT / "tools" / "fix_macos_bundle_openssl.py"


def _prepare_text() -> str:
    return _PREPARE.read_text(encoding="utf-8")


def test_rewrite_decides_by_local_copy_not_by_prefix_shape() -> None:
    """★ 核心判据：改写依据必须是"**本目录有没有这份副本**"，不是依赖长什么样。"""
    text = _prepare_text()
    assert 'dep_base="$(basename "$dep")"' in text, (
        "重写循环里没有按 basename 取短名的步骤 ⇒ 无法按'本目录副本'判断"
    )
    assert '[[ -f "$TESS_ROOT/$dep_base" ]]' in text, (
        "重写循环没有'本目录是否已有该 basename 副本'这条确定性判据 ⇒ "
        "绝对路径依赖（如 /opt/homebrew/Cellar/...）会被拷进来却仍引用原路径"
    )
    assert '"@loader_path/$dep_base"' in text, (
        "没有把依赖改写成 @loader_path/<短名> ⇒ 包在干净机器上 dyld 会 Library not loaded"
    )


def test_regression_the_old_rpath_only_rewrite_is_gone() -> None:
    """★ 回归钉：旧的"只重写 `@rpath/*`"**改写语句**必须已经不存在。

    旧形式是 `install_name_tool -change "$dep" "@loader_path/${dep#@rpath/}"` ——
    它只覆盖 `@rpath/...`，正是本次缺陷的成因。

    ★ 判据必须**精确到改写语句**：`${dep#@rpath/}` 在 `resolve_dylibs()`（**解析**阶段，
    把 `@rpath/x` 落到真实文件）里是正当用法 —— 我第一版按裸字符串断言，
    结果被自己的正当代码判红（守卫写宽了同样是缺陷）。
    """
    text = _prepare_text()
    assert '"@loader_path/${dep#@rpath/}"' not in text, (
        "检测到旧的按前缀剥壳的**改写**语句 ⇒ `@rpath` 之外的绝对路径依赖仍不会被改写（W4.2 真缺陷复现）"
    )


def test_system_libraries_and_relative_refs_are_left_alone() -> None:
    """不得误改系统库与已是相对引用的依赖（改了会破坏包内一致性）。"""
    text = _prepare_text()
    assert "/usr/lib/*|/System/*|/Library/*" in text, "缺少系统库跳过规则"
    assert "@loader_path/*|@executable_path/*" in text, "缺少相对引用跳过规则"


def test_premise_absolute_dependencies_are_actually_copied() -> None:
    """前提校验：绝对路径依赖确实会被**拷贝**进本地目录。

    若哪天不再拷贝，前两条的"拷了却没改"前提就不成立 ⇒ 守卫会变成空转。
    """
    text = _prepare_text()
    assert 'cp -f "$dep" "$TESS_ROOT/"' in text, (
        "没找到把依赖拷进运行时目录的动作 ⇒ 本文件的前提（拷了却没改）不成立，"
        "请重新评估这些判据是否还有意义"
    )
    # 拷贝路径上只应跳过系统库与相对引用
    assert "系统库不打包" in text, "拷贝逻辑里缺少系统库跳过说明"


def test_same_principle_as_the_openssl_fixer() -> None:
    """两处 macOS 依赖修复必须同原则（`@loader_path` 钉住），避免判据分裂。"""
    assert _OPENSSL_FIXER.is_file(), "W4.1 的 _ssl 修复器不见了"
    fixer = _OPENSSL_FIXER.read_text(encoding="utf-8")
    assert "@loader_path/" in fixer, "fix_macos_bundle_openssl.py 不再钉 @loader_path ⇒ 两处原则已分裂"
