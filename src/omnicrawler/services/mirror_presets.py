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
]

CANONICAL_PYPI = "pypi.org"

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
