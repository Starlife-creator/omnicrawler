"""Linux 用户级安装交付件的**唯一判据**（I1a/I1b）。

为什么要有这个工具，而不是在 ``build_linux.sh`` 里内联几条判断
----------------------------------------------------------------

``.sh`` / ``.desktop`` 以 CRLF 或带 BOM 交付时，用户侧**直接不可用**
（``/usr/bin/env: 'bash\\r': No such file or directory``），而构建机是 Linux、
Windows 上的作者**本地看不出来** —— 这正是"构建期报绿、干净机器报红"的典型形态。

因此判据必须满足两件事，缺一不可：

1. **只留一处**：构建期（``build_linux.sh``）与单测走**同一个**函数，不各写一份；
2. **能真的说红**：``tests/unit/tools/test_linux_install_scripts.py`` 会把 CRLF /
   BOM / 缺图标 / 模板被改坏**装回去**，断言这里立刻判红，再字节级还原。

同时校验 ``.desktop`` 模板的契约字段。这些字段是**跨文件契约**，散在散文注释里
会随重构静默漂移：

* ``Exec=@PREFIX@/OmniCrawler-launcher`` —— 占位符给安装脚本替换成绝对路径；目标必须是
  **版本无关的启动器**（它读 ``versions/current.txt`` 决定启动哪一份；指本体的话
  ``--to-versions`` 装了新版也没人启动它）。
* ``Icon=omnicrawler`` —— 必须与 hicolor 落点 ``<N>x<N>/apps/omnicrawler.png`` 同名。
* ``StartupWMClass=omnicrawler`` —— 必须与 ``gui/main.py`` 的
  ``setDesktopFileName("omnicrawler")`` 一致（X11 侧靠它匹配窗口与桌面条目）。

★ 输出**只允许 ASCII**：本工具会在 ``windows-latest`` 的 pytest 里跑，那里的
控制台是 cp1252，任何非 ASCII 字符会变成 ``UnicodeEncodeError``。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# hicolor 需要的 7 个尺寸（16/24/32/48/64/128/256），与品牌资产包一致。
HICOLOR_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

#: 用 bash 写的**安装器脚本**（随包放在 `installer/` 下）。
DELIVERY_SCRIPTS: tuple[str, ...] = ("install-user.sh", "uninstall-user.sh")

#: ★ 放在**应用根**（不是 `installer/`）的交付件：版本无关入口（启动器）。
#:
#: 为什么这个位置区分很重要：`install-user.sh` 会把桌面条目与 PATH 软链指向
#: `<prefix>/OmniCrawler-launcher`，所以启动器必须在**应用根**上；而安装器脚本在
#: `installer/` 下。混成一个清单会让"造忠实安装树"的测试把启动器放进 `installer/`，
#: 于是安装脚本报"便携包不完整"（CI 实测踩到过）。
APP_ROOT_FILES: tuple[str, ...] = (
    "OmniCrawler-launcher",
    "omnicrawler-cli-launcher",
)

# ★ 交付脚本：**POSIX sh** 类（要求 `#!/bin/sh` + `set -eu`）
#   为什么启动器刻意不用 bash：它是用户**唯一**能启动应用的入口。`/bin/sh` 由 POSIX 保证存在
#   （Linux/macOS 都有，Alpine 是 busybox ash 也满足），而 `bash` 在极简镜像里可能缺席 ——
#   启动器因为"找不到 bash"而失败，比它写得少一点花活严重得多。
#   代价是 `pipefail` 在 POSIX sh 里不存在 ⇒ 这一类的严格模式判据是 `set -eu`（等价强度）。
DELIVERY_POSIX_SCRIPTS: tuple[str, ...] = APP_ROOT_FILES

DESKTOP_TEMPLATE = "omnicrawler.desktop.in"

#: 交付件全集（判据只认这一个清单：不管它最终落在哪一层，都要过 LF 与形态检查）。
DELIVERY_FILES: tuple[str, ...] = (
    *DELIVERY_SCRIPTS,
    *DELIVERY_POSIX_SCRIPTS,
    DESKTOP_TEMPLATE,
)

# .desktop 模板必须含有的字段（字面量比对，避免"改了模板这里不知道"）。
DESKTOP_REQUIRED_LINES: tuple[str, ...] = (
    "[Desktop Entry]",
    "Type=Application",
    "Name=OmniCrawler",
    # ★ 指向**启动器**而不是 `OmniCrawler` 本体：`--to-versions` 把新版装到
    #   `<安装根>/versions/<版本>/` 后，只有读 `versions/current.txt` 的启动器才会启动新版。
    #   写死指本体等于"装了新版没人启动它"。
    "Exec=@PREFIX@/OmniCrawler-launcher",
    "Icon=omnicrawler",
    "StartupWMClass=omnicrawler",
    "Terminal=false",
)


def _check_line_endings(path: Path) -> list[str]:
    """纯 LF、无 BOM。CRLF 会被用户侧的 `/usr/bin/env` 直接拒绝。"""
    errors: list[str] = []
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{path.name}: starts with a UTF-8 BOM (must be plain UTF-8)")
    cr_count = raw.count(b"\r")
    if cr_count:
        errors.append(
            f"{path.name}: contains CR ({cr_count} bytes); "
            "delivered scripts must be pure LF or they fail on the user's machine"
        )
    return errors


def _check_script_shape(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    if not text.startswith("#!/usr/bin/env bash\n"):
        errors.append(f"{path.name}: must start with '#!/usr/bin/env bash'")
    if "set -euo pipefail" not in text:
        errors.append(f"{path.name}: must set -euo pipefail")
    return errors


def _check_posix_script_shape(path: Path) -> list[str]:
    """POSIX sh 类的形态判据（与 bash 类同强度：声明解释器 + 严格模式）。

    `pipefail` 在 POSIX sh 里**不存在** ⇒ 这一类只要求 `set -eu`。
    别为了"看起来一致"把它改成 bash：见 `DELIVERY_POSIX_SCRIPTS` 的注释。
    """
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    if not text.startswith("#!/bin/sh\n"):
        errors.append(f"{path.name}: must start with '#!/bin/sh'")
    if "set -eu" not in text:
        errors.append(f"{path.name}: must set -eu")
    if "pipefail" in text:
        errors.append(f"{path.name}: uses pipefail, which POSIX sh does not support")
    return errors


def _check_desktop_template(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for needle in DESKTOP_REQUIRED_LINES:
        if needle not in text:
            errors.append(f"{path.name}: missing required line {needle!r}")
    if text.count("@PREFIX@") != 1:
        errors.append(
            f"{path.name}: expects exactly one '@PREFIX@' placeholder, "
            f"found {text.count('@PREFIX@')}"
        )
    return errors


def _check_branding_sources(branding_src: Path) -> list[str]:
    """I1b 的图标源：单一真源在 ``src/omnicrawler/gui/branding/``。"""
    errors: list[str] = []
    if not branding_src.is_dir():
        return [f"branding source dir not found: {branding_src}"]
    for size in HICOLOR_SIZES:
        candidate = branding_src / f"omnicrawler-icon-{size}.png"
        if not candidate.is_file():
            errors.append(f"missing hicolor source icon: {candidate.name}")
    return errors


def _check_var_adjacent_non_ascii(path: Path) -> list[str]:
    """`$VAR` 后面紧跟非 ASCII 字符 ⇒ 必须写成 `${VAR}`。

    ★ 为什么：**bash 3.2**（macOS 自带）的解析器不是 UTF-8 感知的，会把紧跟其后的
    多字节字符**吞进变量名**。于是 `"…删除 $PREFIX（含…）"` 会被当成变量
    `PREFIX（`：
      * 有 `set -u` ⇒ `PREFIX?: unbound variable` 直接终止（实测：macOS runner 上
        `--purge-data` 因此 rc=1、消息断在上一行）；
      * 没有 `set -u` ⇒ 变量展开为空，**警告信息悄悄丢掉变量值**，比崩溃更难发现。
    加花括号显式终止变量名，两种情形都消失。本规则同时排除转义写法 `\\$VAR`。
    """
    errors: list[str] = []
    var = re.compile(r"(?<!\\)\$[A-Za-z_][A-Za-z0-9_]*")
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in var.finditer(line):
            following = line[match.end() : match.end() + 1]
            if following and ord(following) > 127:
                errors.append(
                    f"{path.name}:{lineno}: '{match.group(0)}' is immediately followed by "
                    f"non-ASCII {following!r}; write ${{{match.group(0)[1:]}}} instead "
                    "(bash 3.2 swallows the multibyte char into the variable name)"
                )
    return errors


def check(delivery_dir: Path, branding_src: Path | None = None) -> list[str]:
    """返回问题列表（空列表 = 全部通过）。构建期与单测都调用这一个入口。"""
    errors: list[str] = []
    if not delivery_dir.is_dir():
        return [f"delivery dir not found: {delivery_dir}"]

    for name in DELIVERY_FILES:
        path = delivery_dir / name
        if not path.is_file():
            errors.append(f"missing delivery file: {name}")
            continue
        errors.extend(_check_line_endings(path))

    for name in DELIVERY_SCRIPTS:
        path = delivery_dir / name
        if path.is_file():
            errors.extend(_check_script_shape(path))
            errors.extend(_check_var_adjacent_non_ascii(path))

    for name in DELIVERY_POSIX_SCRIPTS:
        path = delivery_dir / name
        if path.is_file():
            errors.extend(_check_posix_script_shape(path))
            errors.extend(_check_var_adjacent_non_ascii(path))

    template = delivery_dir / DESKTOP_TEMPLATE
    if template.is_file():
        errors.extend(_check_desktop_template(template))

    if branding_src is not None:
        errors.extend(_check_branding_sources(branding_src))

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--delivery-dir", type=Path, default=Path("packaging/linux"),
        help=(
            "directory holding install-user.sh / uninstall-user.sh / omnicrawler.desktop.in / "
            "OmniCrawler-launcher / omnicrawler-cli-launcher"
        ),
    )
    parser.add_argument(
        "--branding-src", type=Path, default=None,
        help="branding icon source dir (src/omnicrawler/gui/branding); checked when given",
    )
    args = parser.parse_args(argv)

    errors = check(args.delivery_dir, args.branding_src)
    if errors:
        for error in errors:
            print(f"linux delivery check: FAIL: {error}")
        return 1
    checked = len(DELIVERY_FILES)
    extra = " + hicolor sources" if args.branding_src is not None else ""
    print(f"linux delivery check: OK ({checked} files{extra})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
