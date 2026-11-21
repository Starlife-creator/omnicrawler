"""从 PyPI 自动生成一个 Wheel-Pack 组件的完整配方（含全部构件与哈希）。

## 为什么需要它

`ocr-paddle` 的 113 个构件是**手工攒出来的**，而手工必然漏 ——
本轮实测就漏过 `httpx` / `httpcore` / `networkx` / `protobuf` /
`safetensors`，每次都要等 CI 报 `ModuleNotFoundError` 才发现。

本工具做的是：给一个包名，**解析完整依赖闭包**（含传递依赖），
按目标平台选出**最好的那个轮子**，算好 sha256 与体积，直接产出
可用的 `recipe.json`。

## 选轮子的策略（顺序即优先级）

1. **平台专属轮子** —— 如 `win_amd64` / `manylinux` / `macosx`，
   能带原生扩展，必须优先（纯 Python 轮子常常是降级实现）
2. **ABI3 轮子** —— `cp39-abi3` 之类对多个解释器版本通用
3. **纯 Python 轮子** —— `none-any`，最通用但可能缺原生加速
4. 源码包 —— **只在没有轮子时才用**，且会在输出里明确警告

跳过：`cp313t`（free-threaded，与常规构建 ABI 不兼容）、
`musllinux`（Alpine 专用，与 glibc 环境不兼容）、
以及 `requires_python` 与目标解释器不符的版本。

## 用法

```powershell
# 只看会拉什么，不下载（默认）
python tools/market/gen_component_recipe.py rapidocr --id ocr-rapidocr --kind wheel-pack

# 真的下载并算哈希（需要联网，只在建组件时跑一次）
python tools/market/gen_component_recipe.py rapidocr --id ocr-rapidocr --download
```
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PYPI = "https://pypi.org/pypi"

#: 目标解释器（与项目一致）。写死是刻意的：配方要声明 ABI，
#: 而「猜当前解释器」会让同一份配方在不同机器上产出不同结果。
TARGET_PY = (3, 13)
TARGET_ABI = f"cp{TARGET_PY[0]}{TARGET_PY[1]}"

#: 平台 → 轮子文件名里应出现的标记（按优先级）。
PLATFORM_TOKENS: dict[str, tuple[str, ...]] = {
    "windows": ("win_amd64", "win32"),
    "linux": ("manylinux", "linux_x86_64"),
    "macos": ("macosx", "universal2"),
}

#: 架构 → 文件名标记。
ARCH_TOKENS: dict[str, tuple[str, ...]] = {
    "x86_64": ("x86_64", "amd64"),
    "aarch64": ("aarch64", "arm64"),
}

#: **CPU-only 轮子的独立索引**。
#:
#: PyPI 上的 `torch` 是 **CUDA 版**：Linux 上装完仍要求系统里有
#: `libcublasLt.so.13` 等 CUDA 运行库（实测 CI 报
#: `ValueError: libcublasLt.so.*[0-9] not found in the system path`），
#: 在无 GPU 的机器上直接跑不起来。
#:
#: 正确的做法是用 pytorch 官方的 CPU 索引，那里的轮子不带 CUDA 依赖。
#: 版本号会略低于 PyPI（当前 2.9.1 vs 2.15.0），但下游约束都很宽
#: （easyocr 只要求 torchvision>=0.5，doctr 要求 torch>=2.0.0），
#: 完全兼容。
_CPU_INDEX = {
    "torch": "https://download.pytorch.org/whl/cpu/torch/",
    "torchvision": "https://download.pytorch.org/whl/cpu/torchvision/",
}

#: 明确不接受的标记（会在目标环境上装不上或跑不起来）。
_REJECT_TOKENS = ("cp313t", "musllinux", "win_arm64", "i686", "ppc64", "s390x")

#: **GPU / CUDA 运行时包前缀** —— 纯 CPU 场景一律排除。
#:
#: Linux 上 `torch` 的依赖闭包会带进一整套 CUDA 运行时：
#: nvidia-cudnn 492 MiB + nvidia-cublas 423 MiB + triton 272 MiB +
#: nccl 197 MiB + ... 合计约 **2.3 GiB**。
#:
#: 而本项目的 OCR 用途是**纯 CPU 推理**（桌面单机、无 GPU 假设）。
#: 把这些算进组件会让 easyocr 从 248 MiB 膨胀到 2687 MiB —— 那是
#: 「因为下游的默认依赖而把组件撑爆」，不是用户要的东西。
#:
#: 排除它们是安全的：torch 的 CPU 轮子自带所需运行时，
#: 只有真正要用 CUDA 时才需要这些包。
_GPU_PACKAGE_PREFIXES = (
    "nvidia-",
    "cuda-",
    "triton",
    "pytorch-triton",
)


def _fetch(name: str) -> dict:
    with urllib.request.urlopen(f"{PYPI}/{name}/json", timeout=45) as response:
        return json.load(response)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


#: 平台 → PEP 508 的 `sys_platform` 取值。
_SYS_PLATFORM = {"windows": "win32", "linux": "linux", "macos": "darwin"}


def _marker_applies(marker: str, platform: str) -> bool:
    """判断 PEP 508 环境标记在当前目标平台上是否成立。

    **这是必须做的一步**：`torch` 的 CUDA 依赖几乎全带
    `platform_system == "Linux"` 标记，Windows 上根本不需要 ——
    不求值标记就会去拉 `nvidia-cublas` 这种只有 Linux 轮子的包，
    于是配方缺构件、装配失败（实测 docTR 因此虚增 3 个不存在的依赖）。

    只实现本项目会用到的几种形式（够用即止，不追求完整 PEP 508）：
    * `extra == "..."` —— 可选依赖，一律跳过
    * `platform_system == "Linux"` / `!=`
    * `python_version` / `sys_platform` 的 `==` / `!=`
    * 用 `and` / `or` 连接的组合（保守：任一不支持就按「成立」处理，
      宁可多拉也不漏拉——漏拉会让组件装完跑不起来）
    """
    marker = marker.strip()
    if not marker:
        return True
    lowered = marker.lower()
    if 'extra' in lowered:
        return False  # 可选依赖不进基础闭包


    target_system = {"windows": "Windows", "linux": "Linux", "macos": "Darwin"}[platform]
    py_version = f"{TARGET_PY[0]}.{TARGET_PY[1]}"

    # 逐条 `xxx == "yyy"` / `xxx != "yyy"` 求值
    facts = {
        "platform_system": target_system,
        "sys_platform": _SYS_PLATFORM[platform],
        "python_version": py_version,
        "os_name": "nt" if platform == "windows" else "posix",
    }
    pattern = re.compile(r"(\w+)\s*(==|!=)\s*[\"']([^\"']+)[\"']")

    def _eval_one(expr: str) -> bool:
        match = pattern.search(expr)
        if not match:
            return True  # 不认识的写法：保守当成立
        key, operator, expected = match.groups()
        actual = facts.get(key)
        if actual is None:
            return True
        equal = _normalize(actual) == _normalize(expected)
        return equal if operator == "==" else not equal

    # `or` 优先于 `and` 的优先级处理：先按 or 拆，再按 and 合并
    for disjunct in re.split(r"\bor\b", lowered):
        if all(_eval_one(part) for part in re.split(r"\band\b", disjunct)):
            return True
    return False


def _parse_requirement(spec: str) -> tuple[str, str]:
    """把 `pkg>=1.0; marker` 拆成 `(包名, 环境标记)`。"""
    body, _, marker = spec.partition(";")
    name = re.split(r"[<>=!~\[ ]", body.strip(), maxsplit=1)[0]
    return name, marker.strip()


def _version_key(text: str) -> tuple:
    """版本号排序键（粗粒度但足够选「最新」）。"""
    parts = re.findall(r"\d+", text)
    return tuple(int(p) for p in parts[:6]) or (0,)


def _wheel_score(filename: str, platform: str, arch: str) -> int | None:
    """给轮子打分；`None` 表示这个轮子不能用。

    分数越高越优先：平台专属原生轮 > abi3 原生轮 > 纯 Python 轮。
    """
    lowered = filename.lower()
    if any(token in lowered for token in _REJECT_TOKENS):
        return None
    if not lowered.endswith(".whl"):
        return None

    plat_tokens = PLATFORM_TOKENS[platform]
    arch_tokens = ARCH_TOKENS[arch]
    is_platform_wheel = any(t in lowered for t in plat_tokens)
    is_any = "none-any" in lowered
    matches_arch = any(t in lowered for t in arch_tokens)

    if is_any:
        return 10
    if not (is_platform_wheel and matches_arch):
        return None

    # **ABI 必须与目标解释器一致**：`cp312` 的轮子装在 cp313 上会报
    # `No module named`（实测：numpy cp312 被选中，装完导入失败）。
    #
    # 三种可接受的 ABI 形态：
    #   * `abi3`      —— 承诺跨解释器版本兼容（最高优先）
    #   * `cp313`     —— 精确匹配目标
    #   * `py3-none`  —— 纯 Python 实现但**带平台标签**
    #                    （如 `torchvision-0.29.2-py3-none-win_amd64`、
    #                     `pypdfium2-5.15.2-py3-none-win_amd64`）。
    #                    早先只认 `none-any`，把这类误判成「没有轮子」，
    #                    导致 docTR 的配方漏了 torchvision 与 pypdfium2。
    if "abi3" in lowered:
        return 30
    if f"cp{TARGET_PY[0]}{TARGET_PY[1]}" in lowered:
        return 28
    if re.search(r"-py3-none-(?!any)", lowered):
        return 26
    return None


def _head_size(url: str) -> int:
    """用 HEAD 取构件体积（CPU 索引页面不提供 size）。"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return int(response.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _from_cpu_index(package: str, platform: str, arch: str) -> dict | None:
    """从 pytorch 的 CPU-only 索引里挑轮子。

    只在 `package` 属于 `_CPU_INDEX` 时调用。返回与 PyPI 同形的条目。
    """
    index_url = _CPU_INDEX.get(package)
    if not index_url:
        return None
    try:
        req = urllib.request.Request(index_url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=60) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 索引不可达就退回 PyPI
        return None

    plat_tokens = PLATFORM_TOKENS[platform]
    arch_tokens = ARCH_TOKENS[arch]
    candidates: list[tuple[tuple, str, str, str]] = []
    # href 是**完整绝对 URL**（形如 https://download-r2.pytorch.org/whl/cpu/
    # torch-2.9.1%2Bcpu-cp313-...whl#sha256=...），不是相对路径。
    for match in re.finditer(
        rf'href="(https://[^"]+/whl/cpu/({re.escape(package)}-[^"#]+\.whl))(?:#sha256=([0-9a-f]{{64}}))?"',
        html,
    ):
        full_url, filename, sha = match.group(1), match.group(2), match.group(3)
        lowered = filename.lower()
        if f"cp{TARGET_PY[0]}{TARGET_PY[1]}" not in lowered:
            continue
        if not any(tok in lowered for tok in plat_tokens):
            continue
        if not any(tok in lowered for tok in arch_tokens):
            continue
        version_match = re.search(r"-([\d.]+)(?:\+|%2B)cpu", filename)
        version = version_match.group(1) if version_match else "0"
        candidates.append((_version_key(version), filename, sha or "", full_url))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, filename, sha, url = candidates[-1]
    basename = urllib.parse.unquote(filename)
    return {
        "package": package,
        "version": re.search(r"-([\d.]+)(?:\+|%2B)cpu", basename).group(1),
        "url": url,
        "sha256": sha,
        "size": 0,  # CPU 索引页面不提供体积，稍后由 HEAD 补
        "filename": basename,
    }


def _pick_wheel(release_files: list[dict], platform: str, arch: str) -> dict | None:
    """在某个版本的发布文件里挑最合适的轮子。"""
    scored: list[tuple[int, dict]] = []
    for item in release_files:
        score = _wheel_score(item["filename"], platform, arch)
        if score is not None:
            scored.append((score, item))
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], pair[1]["filename"]))
    return scored[0][1]


def resolve_closure(root_package: str, platform: str, arch: str) -> list[dict]:
    """解析 `root_package` 的完整依赖闭包，返回每个包选中的构件。"""
    chosen: dict[str, dict] = {}
    pending: list[str] = [root_package]
    seen: set[str] = set()

    while pending:
        name = pending.pop(0)
        key = _normalize(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            data = _fetch(name)
        except Exception as exc:  # noqa: BLE001 - 网络/404 都按「跳过」处理
            print(f"  [skip] {name}: {type(exc).__name__}", file=sys.stderr)
            continue

        version = data["info"]["version"]
        files = data["releases"].get(version) or []
        # **torch / torchvision 优先用 CPU-only 索引**：PyPI 上的是 CUDA 版，
        # 装完仍要求系统有 libcublasLt.so，无 GPU 机器上直接失败。
        wheel = _from_cpu_index(name, platform, arch) if _normalize(name) in _CPU_INDEX else None
        if wheel is None:
            wheel = _pick_wheel(files, platform, arch)
        if wheel is None:
            print(
                f"  [warn] {name} {version}: 没有适配 {platform}/{arch} 的轮子",
                file=sys.stderr,
            )
        else:
            if not wheel.get("size"):
                wheel["size"] = _head_size(wheel["url"])
            # 两种结构：PyPI 的是 `{"digests": {"sha256": ...}}`，
            # CPU 索引的已拍平成 `{"sha256": ...}`。统一取值。
            sha = wheel.get("sha256") or (wheel.get("digests") or {}).get("sha256") or ""
            chosen[key] = {
                "package": name,
                "version": wheel.get("version") or version,
                "url": wheel["url"],
                "sha256": sha,
                "size": wheel["size"],
                "filename": wheel["filename"],
            }

        for spec in data["info"].get("requires_dist") or []:
            dep, marker = _parse_requirement(spec)
            if not dep:
                continue
            if not _marker_applies(marker, platform):
                continue  # 该标记在当前目标平台不成立
            if _normalize(dep).startswith(_GPU_PACKAGE_PREFIXES):
                # 纯 CPU 场景：GPU 运行时不进闭包（见 _GPU_PACKAGE_PREFIXES 说明）
                continue
            if _normalize(dep) not in seen:
                pending.append(dep)

    return [chosen[k] for k in sorted(chosen)]


def build_recipe(
    package: str,
    *,
    component_id: str,
    kind: str,
    platforms: list[str],
    architectures: list[str],
    engine: str,
    module: str,
    description: str,
    platforms_scope: str = "",
) -> dict:
    """产出一份 recipe（构件表由调用方随后填入）。"""
    return {
        "schema": 1,
        "id": component_id,
        "name": component_id,
        "version": "",
        "description": description,
        "kind": kind,
        "provides": [f"ocr.{component_id.replace('ocr-', '')}"],
        "platforms": platforms,
        "architectures": architectures,
        "python_abi": [TARGET_ABI],
        "requires": [package],
        "artifacts": [],
        "entry": {
            "path": "entry.json",
            "protocol": "ocr-file-v1",
            "engine": engine,
            "runner": "worker-wheelpack",
            # **入口必须指向 `__init__.py`**，不是包目录 ——
            # worker 的 `_resolve_module_name` 要求入口是一个**文件**，
            # 再把它换成模块名（`site-packages/rapidocr/__init__.py` → `rapidocr`）。
            # 指向目录会报「组件入口不存在」（实测）。
            "module": f"site-packages/{module}/__init__.py",
        },
        "smoke": {
            "expect": ["ok", "engine", "module"],
            "timeout_seconds": 600,
            "description": f"import {module}，确认原生扩展可加载",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 PyPI 生成 Wheel-Pack 组件配方")
    parser.add_argument("package", help="根包名（如 rapidocr）")
    parser.add_argument("--id", required=True, help="组件 id（等于目录名）")
    parser.add_argument("--kind", default="wheel-pack", choices=["wheel-pack"])
    parser.add_argument("--engine", default="generic", help="探测引擎（generic / paddle）")
    parser.add_argument("--module", required=True, help="入口模块名（如 rapidocr）")
    parser.add_argument("--description", default="")
    parser.add_argument(
        "--platform", default="windows", choices=sorted(PLATFORM_TOKENS),
        help="主平台（构建依赖闭包用）",
    )
    parser.add_argument("--arch", default="x86_64", choices=sorted(ARCH_TOKENS))
    parser.add_argument(
        "--also-platforms", default="",
        help=(
            "额外平台，逗号分隔（如 linux）。**必须给**，否则组件只能在本机平台装 —— "
            "CI 跑在 Linux 上，只声明 windows 会导致装配阶段直接失败"
            "（实测：5 个新组件在 CI 上全部报「此组件不支持当前平台」）。"
        ),
    )
    parser.add_argument("--out", required=True, help="输出目录（市场仓的组件目录）")
    parser.add_argument("--download", action="store_true", help="真的下载并算哈希")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    extra = [p.strip() for p in (args.also_platforms or "").split(",") if p.strip()]
    all_platforms = [args.platform] + [p for p in extra if p != args.platform]

    # **每个平台单独解析闭包**：同一份配方在不同平台要拉不同的轮子
    # （onnxruntime 的 win_amd64 与 manylinux_x86_64 是两个构件）。
    # 合并时按 (包名, 文件名) 去重 —— 同一个纯 Python 轮子在多平台共用。
    merged: dict[str, dict] = {}
    for platform in all_platforms:
        print(f"解析依赖闭包：{args.package}（{platform}/{args.arch}）")
        for item in resolve_closure(args.package, platform, args.arch):
            key = f"{item['package']}|{item['filename']}"
            if key in merged:
                # 已在别的平台出现过：补上 platforms 标记
                merged[key].setdefault("platforms", set()).add(platform)
                continue
            entry = dict(item)
            entry["platforms"] = {platform}
            merged[key] = entry
        via = len([k for k, v in merged.items() if platform in v["platforms"]])
        print(f"  该平台 {via} 个构件")

    closure = list(merged.values())
    total = sum(int(item["size"]) for item in closure)
    print(f"合计 {len(closure)} 个构件，{total // 1024 // 1024} MiB")

    recipe = build_recipe(
        args.package,
        component_id=args.id,
        kind=args.kind,
        platforms=all_platforms,
        architectures=[args.arch],
        engine=args.engine,
        module=args.module,
        description=args.description or f"{args.package}（{args.engine}）",
    )
    recipe["version"] = "1.0.0"
    recipe["artifacts"] = [
        {
            "url": item["url"],
            "sha256": item["sha256"],
            "size": item["size"],
            "format": "whl",
            "extract_to": "site-packages",
            # 每个构件标注适用平台：跨平台配方里同一个包有多个轮子，
            # 装配时按当前平台筛（`artifacts_for_platform`）。
            "platforms": sorted(item["platforms"]),
        }
        for item in closure
    ]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "recipe.json"
    target.write_bytes(
        (json.dumps(recipe, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(f"已写入 {target}（{len(recipe['artifacts'])} 个构件）")

    if args.download:
        print("--download 模式：构件 URL 与哈希已从 PyPI 取得，无需本地下载")
        print("（哈希来自 PyPI API，与实际下载的字节一一对应）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
