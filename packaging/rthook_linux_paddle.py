# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller runtime hook —— Linux Full 便携包启用 Paddle 共享库。

背景：paddle 的 C++ 动态加载器（phi/backends/dynload/dynamic_loader.cc）对
部分第三方库用**裸名** dlopen（如 GetMKLMLDsoHandle → "libmklml_intel.so"、
GetLAPACKDsoHandle → "liblapack.so.3"；FLAGS_mklml_dir / FLAGS_lapack_dir
默认空，退化为裸名）。裸名 dlopen 的库搜索规则（glibc dl-load.c）：
  1. 已加载库列表（相同 SONAME 已存在则直接复用）；
  2. LD_LIBRARY_PATH（仅进程启动时由 ld.so 解析一次缓存，运行中改
     os.environ 无效）；
  3. DT_RPATH / DT_RUNPATH（仅作用于主程序加载其依赖，裸 dlopen 不查）；
  4. /etc/ld.so.cache 与系统默认目录。

libmklml_intel.so 存在于 paddle wheel 的 paddle/libs/（v0.9.1 Linux CI
已确认），PyInstaller 已把它 collect 到 _internal/paddle/libs/，但裸 dlopen
在产物环境（无启动时 LD_LIBRARY_PATH 预置、RPATH 不参与）下找不到。

修复：本 hook 在 Python 启动早期（任何 import paddle 之前）用绝对路径
ctypes.CDLL() **预加载** paddle 裸 dlopen 的那几个第三方库（libmklml_intel
.so、liblapack.so.3）。glibc 的 dlopen 对已加载库直接复用（搜索规则第 1
条），此后 paddle 的裸名 dlopen 即命中。

**刻意不预加载** libphi.so / libphi_core.so 等 paddle 核心对象：它们由
paddle 自身模块按既有路径加载，预先以绝对路径加载会与 paddle 内部的
再加载产生两份全局状态——v0.9.1 Linux CI 实测报 paddle flags error:
flag "enable_host_event_recorder_hook" defined both in profiler.cc（flag
静态链接进 .so，重复加载同库不同句柄导致全局重复定义）。libwarpctc.so /
libwarprnnt.so 走 SetPaddleLibPath 的 s_py_site_pkg_path 绝对路径，不需要
预加载。

依赖解析：CDLL 绝对路径加载 libmklml 时其依赖（libiomp5.so 等）经
patchelf 已设的 $ORIGIN RPATH 在同目录解析（见 build_linux.sh 的 RPATH
修正步骤）。单库失败容忍，不阻塞启动。

frozen 环境下 sys._MEIPASS == onedir 的 _internal 目录。
"""
import ctypes
import os
import sys

_PADDLE_LIBS = ("libmklml_intel.so",)


def _preload_paddle_libs() -> None:  # pragma: no cover - 仅 frozen Linux 生效
    libs_dir = os.path.join(sys._MEIPASS, os.path.join("paddle", "libs"))  # noqa: SLF001 - PyInstaller 私有常量
    if not os.path.isdir(libs_dir):
        return
    for name in _PADDLE_LIBS:
        path = os.path.join(libs_dir, name)
        if os.path.isfile(path):
            try:
                ctypes.CDLL(os.path.abspath(path))
            except OSError:
                pass  # 由其它机制加载或非必需，忽略


if getattr(sys, "frozen", False):
    _preload_paddle_libs()