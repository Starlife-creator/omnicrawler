"""镜像加速的预置清单与「一键启用」配置补丁（决策七）。

设计要点（合规边界，见 ``sources/mirror_registry.py:5-11``）：

* 预置的是**候选清单**（已知可用的国内 PyPI 镜像），而不是**生效配置**；
* 默认配置中**没有** ``mirrors`` 节 ⇒ 引擎零开销直通，绝不偷偷导流；
* 只有在**用户显式点击启用**（或对"官方源连接失败"提示点"是"）时，
  才把预置清单合入配置。

因此本模块只提供**纯函数**：给定当前配置字典 → 返回"启用镜像后"的新字典。
真正落盘由既有配置保存路径完成，便于单测（不碰文件系统、不碰网络）。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CANONICAL_PYPI",
    "preset_mirror_group",
    "enable_mirrors_patch",
    "mirrors_enabled",
    "should_offer_mirror_acceleration",
    "mirror_endpoints_from_patch",
]

CANONICAL_PYPI = "pypi.org"

#: pip 失败归因中「网络/传输故障」这一类；只有它才说明"换镜像可能救得了"。
_NETWORK_KINDS: frozenset[str] = frozenset({"network"})

#: 超时归因记录用的 kind（属网络类失败的子情形）。
_TIMEOUT_KINDS: frozenset[str] = frozenset({"timeout-retry"})

#: 预置候选（host, weight）。官方源权重最高，保证健康时置顶（决策一）；
#: 其余镜像给出递增优先级，顺序回退时按健康分自然排序。
_PRESET_PYPI: tuple[tuple[str, float], ...] = (
    ("pypi.org", 3.0),
    ("mirrors.tuna.tsinghua.edu.cn", 2.0),
    ("mirrors.aliyun.com", 1.5),
    ("mirrors.cloud.tencent.com", 1.2),
    ("pypi.mirrors.ustc.edu.cn", 1.0),
)


def preset_mirror_group() -> dict[str, list[dict[str, Any]]]:
    """返回 ``mirrors.groups`` 的预置内容（PyPI 镜像组）。

    纯数据，可被 GUI 展示为"将启用的镜像"清单，也可直接合入配置。
    """
    return {
        CANONICAL_PYPI: [
            {"host": host, "weight": weight} for host, weight in _PRESET_PYPI
        ]
    }


def mirrors_enabled(config_raw: dict[str, Any]) -> bool:
    """当前配置是否已启用镜像路由。"""
    mirrors = (config_raw or {}).get("mirrors")
    if not isinstance(mirrors, dict):
        return False
    return bool(mirrors.get("enabled"))


def enable_mirrors_patch(config_raw: dict[str, Any]) -> dict[str, Any]:
    """返回"启用镜像加速"的 ``patch_config`` 补丁（不改动调用方字典）。

    ``enabled=True`` + 预置 PyPI 组。若原配置已有 ``mirrors`` 节，保留其
    ``probe_*`` / ``failure_threshold`` 等自定义字段，只补齐/覆盖
    ``enabled`` 与 ``groups``（用户既有调优不被清掉）。
    """
    mirrors = dict((config_raw or {}).get("mirrors") or {})
    mirrors["enabled"] = True
    groups = dict(mirrors.get("groups") or {})
    groups[CANONICAL_PYPI] = preset_mirror_group()[CANONICAL_PYPI]
    mirrors["groups"] = groups
    return {"mirrors": mirrors}


def mirror_endpoints_from_patch(
    config_raw: dict[str, Any],
) -> list[tuple[str, str]]:
    """把"启用镜像"补丁折成 ``ordered_endpoints`` 形状的 ``(canonical, host)`` 列表。

    供安装器**在同一轮内**用启用后的镜像立即重试（用户点"是"之后不必先落盘再重跑），
    也让 GUI 无需自己拼 host 顺序（顺序口径只留一处：预置清单）。
    官方源仍在首位，其余按预置权重降序。
    """
    patch = enable_mirrors_patch(config_raw)
    group = patch["mirrors"]["groups"][CANONICAL_PYPI]
    return [(CANONICAL_PYPI, str(item["host"])) for item in group if item.get("host")]


def should_offer_mirror_acceleration(
    result: Any,
    *,
    config_raw: dict[str, Any],
) -> bool:
    """判定"是否该弹一次『启用国内镜像加速』提示"（纯函数，可单测）。

    触发条件（**全部**满足才提示，避免误扰）：

    1. 安装**确实失败**（``result.ok`` 为假）；
    2. 当前配置**尚未启用**镜像（否则镜像已经用过了，再提示无意义）；
    3. 失败归因里有**官方源**的**网络类**失败（``kind == network`` 或超时归因）
       —— 只有"源连不上"才可能靠换镜像救；"缺该版本 / 约束冲突"换镜像没用，
       提示只会误导用户。

    参数 ``result`` 是 ``InstallResult``（鸭子类型：读 ``ok`` 与 ``attempts``，
    每个 attempt 读 ``host`` / ``canonical`` / ``kind``），因此本函数不 import
    安装器模块，保持 ``mirror_presets`` 的零重量依赖。
    """
    if bool(getattr(result, "ok", False)):
        return False
    if mirrors_enabled(config_raw):
        return False
    official_hosts = {CANONICAL_PYPI, "files.pythonhosted.org"}
    for attempt in getattr(result, "attempts", ()) or ():
        host = str(getattr(attempt, "host", "") or "").casefold()
        kind = str(getattr(attempt, "kind", "") or "").casefold()
        if host in official_hosts and kind in (_NETWORK_KINDS | _TIMEOUT_KINDS):
            return True
    return False
