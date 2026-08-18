# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller runtime hook —— Linux Full 便携包启用 Paddle 共享库。

背景：paddle 的 C++ 动态加载器（phi/backends/dynload/dynamic_loader.cc）对
部分第三方库用**裸名** dlopen（如 GetMKLMLDsoHandle → "libmklml_intel.so"，
FLAGS_mklml_dir 默认空，退化为裸名）。裸名 dlopen 的库搜索规则（glibc
dl-load.c）：
  1. 已加载库列表（相同 SONAME 已存在则直接复用）；
  2. LD_LIBRARY_PATH（仅进程启动时由 ld.so 解析一次缓存，运行中改
     os.environ 无效）；
  3. DT_RPATH / DT_RUNPATH（仅作用于主程序加载其依赖，裸 dlopen 不查）；
  4. /etc/ld.so.cache 与系统默认目录。

libmklml_intel.so（及其 NEEDED 依赖 libiomp5.so 等）存在于 paddle wheel
的 paddle/libs/（v0.9.1 Linux CI 已确认），PyInstaller 已 collect 到
_internal/paddle/libs/，但裸 dlopen 在产物环境（无启动时 LD_LIBRARY_PATH
预置、RPATH 不参与）下找不到 libmklml。

修复：本 hook 在 Python 启动早期（任何 import paddle 之前）用绝对路径
ctypes.CDLL() **预加载** paddle/libs/ 下的共享库（libmklml_intel.so 及其
依赖链），glibc 的 dlopen 对已加载库直接复用（搜索规则第 1 条），此后
paddle 的裸名 dlopen 即命中。加载顺序：基础运行时库（libiomp5 等）先于
依赖它们的 libmklml。

**排除预加载** libphi.so / libphi_core.so / libwarpctc.so / libwarprnnt.so：
- libphi/libphi_core 由 paddle 自身模块按既有路径加载，若预先以绝对路径
  加载会产生两份全局状态——v0.9.1 Linux CI 实测报 paddle flags error:
  flag "enable_host_event_recorder_hook" defined both in profiler.cc（flag
  静态链接进 .so，重复加载同库不同句柄导致全局重复定义）。
- libwarpctc/libwarprnnt 走 SetPaddleLibPath 的 s_py_site_pkg_path 绝对
  路径，不需要预加载。

依赖解析：被排除的 libphi 由 paddle 单次加载；libmklml 的依赖经
build_linux.sh 已设的 \$ORIGIN RPATH 在同目录解析。单库失败容忍，不阻塞
启动。

frozen 环境下 sys._MEIPASS == onedir 的 _internal 目录。
"""
import ctypes
import glob
import os
import sys

# 这些库由 paddle 自身以绝对路径/专属机制加载，预加载会造成重复全局状态
_EXCLUDED = frozenset({"libphi.so", "libphi_core.so", "libwarpctc.so", "libwarprnnt.so"})

# 基础依赖先加载，libmklml 等最后（保证其 NEEDED 依赖已就绪）
_ORDERING = (
    "libiomp5*", "libgomp*", "libgfortran*", "libquadmath*",
    "libblas*", "liblapack*", "libtbb.so*", "libdnnl*",
    "libcommon*", "libopenvino*", "libmklml*",
)


def _preload_paddle_libs() -> None:  # pragma: no cover - 仅 frozen Linux 生效
    libs_dir = os.path.join(sys._MEIPASS, os.path.join("paddle", "libs"))  # noqa: SLF001 - PyInstaller 私有常量
    if not os.path.isdir(libs_dir):
        return
    for pattern in _ORDERING:
        for path in sorted(glob.glob(os.path.join(libs_dir, pattern))):
            name = os.path.basename(path)
            if name in _EXCLUDED:
                continue
            try:
                ctypes.CDLL(os.path.abspath(path))
            except OSError:
                pass  # 由其它机制加载或非必需，忽略


if getattr(sys, "frozen", False):
    _preload_paddle_libs()