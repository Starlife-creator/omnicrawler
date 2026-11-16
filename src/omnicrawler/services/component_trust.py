"""组件信任根解析（条目 3.2 / 条目 5 的前置）。

**为什么需要单独一个模块**

组件验签此前从 ``portable_data_root()/.omnicrawler/component_trust.pub.pem``
取信任根——那是**运行期数据目录**，不在随包发布的内容里。后果是：
用户必须自己把公钥放进去，否则组件功能默认不可用（`COMPONENTS.md` 写着
「不存在信任公钥时默认拒绝安装」）。这与「组件开箱可用」的目标冲突。

另外两把钥匙（`configs/update_trust.pub.pem`、`configs/plugin_trust.pub.pem`）
都是**随包发布**的，查找方式也一致。本模块让组件跟上同一套：

**两级查找（显式优先，随包兜底，fail-closed）**

1. 显式配置：``components.trust_public_key``（PEM 文本或文件路径）；
2. 随包内置：``<app>/configs/component_trust.pub.pem``；
3. 都没有 ⇒ 返回 ``None``，调用方**拒绝安装**（不存在"跳过验签"的降级）。

**为什么组件要单独一把钥匙（而不是复用现有两把）**

| 信任根 | 签什么 | 被签内容来源 | 吊销影响面 |
|---|---|---|---|
| `plugin_trust` | 插件代码 | 第三方作者创作 | 所有插件 |
| `update_trust` | 应用本体 | 本项目构建 | 所有用户的应用更新 |
| `component_trust` | 运行时 + 模型引用 | **可能是第三方** | 所有组件 |

撤销域刻意分开：组件出问题时应只吊销组件，不该逼着换掉**应用更新**的信任根
（那会让所有用户的自更新跟着换根）。这沿用了仓库既有的「市场与更新刻意是两把
钥匙」的判断（`tools/manifest_common.py:58`）。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..core.runtime_paths import application_dir, portable_data_root
from .update_feed import decode_public_key

#: 随包内置的组件信任根（与另外两把同目录、同一命名风格）。
BUNDLED_TRUST_NAME = "component_trust.pub.pem"

#: 用户可选的覆盖位置（运行期数据目录，历史上就是这个位置）。
LEGACY_TRUST_NAME = "component_trust.pub.pem"


def bundled_trust_path() -> Path:
    """随包信任根的路径（**不保证存在**）。"""
    return application_dir() / "configs" / BUNDLED_TRUST_NAME


def legacy_trust_path() -> Path:
    """历史位置：运行期数据目录下的同名文件（用户自备时用）。"""
    return portable_data_root() / ".omnicrawler" / LEGACY_TRUST_NAME


def explicit_trust_setting() -> str:
    """环境变量形式的显式配置（便于测试与无人值守部署）。

    用环境变量而不是新增配置文件键：组件信任根是**部署级**参数，
    不该混进任务配置（那会让"换一把钥匙"变成"改所有任务"）。
    """
    return os.environ.get("OMNICRAWL_COMPONENT_TRUST", "").strip()


def resolve_trust_path() -> Path | None:
    """按优先级找出实际生效的信任根文件；都没有则 ``None``。

    返回 ``Path`` 而非解析后的字节，便于调用方把路径写进错误信息
    （"你配的是哪个文件"是排障时第一个要问的问题）。
    """
    explicit = explicit_trust_setting()
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    bundled = bundled_trust_path()
    if bundled.is_file():
        return bundled
    legacy = legacy_trust_path()
    if legacy.is_file():
        return legacy
    return None


def load_trusted_key() -> bytes | None:
    """读取并解析信任根公钥；没有可用文件时返回 ``None``。

    **不抛异常**：调用方需要区分"没配"和"配了但坏了"——
    前者是预期状态（可给安装指引），后者必须显式报错（不能被当成"没配"）。
    """
    path = resolve_trust_path()
    if path is None:
        return None
    return decode_public_key(path.read_text(encoding="utf-8"))


def describe_missing() -> str:
    """"没有信任根"时给用户的可操作说明。"""
    return (
        "缺少组件受信公钥，无法验证组件签名（按设计拒绝安装）。\n"
        f"请把维护者发布的 {BUNDLED_TRUST_NAME} 放到下列任一位置：\n"
        f"  1) 随包目录: {bundled_trust_path()}\n"
        f"  2) 数据目录: {legacy_trust_path()}\n"
        "或用环境变量 OMNICRAWL_COMPONENT_TRUST 指向该文件。"
    )
