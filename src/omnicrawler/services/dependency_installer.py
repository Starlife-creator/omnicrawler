"""依赖自动安装器：顺序多源回退 + 严格版本 + 自适应超时 + 装完复检。

本模块补齐「检测到依赖缺失 → 用户确认安装 → 多源自动回退装好」链路中
**唯一缺失的那一环**。它与既有组件的关系（不重复发明轮子）：

* 检测来源：``pipeline_ops.preflight`` 产出的 ``action == "install"`` 检查项；
* 源顺序：``sources.mirror_registry.MirrorRegistry.ordered_endpoints``
  （官方源健康时恒为第 0 位，失败移末兜底）；
* 出口策略：复用 ``fetching.http_client.build_safe_opener`` 同一条 egress 边界
  （镜像 host 仍须在 egress 白名单内）。

设计要点（对应已冻结的决策）：

1. **单源循环**：每个源单独调用一次 pip，只传一个 ``--index-url``；
   绝不使用 ``--extra-index-url``（会跨源混合解析、版本来源不可控）。
2. **严格版本**：``spec`` 直接拼进 ``name+spec``（如 ``pdfplumber>=0.10,<0.12``），
   交给 pip 的解析器；区间约束时 pip 默认取区间内最高版本。
3. **超时自适应**（四层）：重包表快速命中 → dry-run 精算体积 → 默认保守值 →
   超时归因（大包判定"估短了"则加时重试同源，普通包才换源）。
4. **失败分类**：只有**网络类**失败才回写 ``record_failure`` 摘流；"源可达但缺该
   版本"与"约束冲突"**不摘流** —— 摘流会误杀健康源。
5. **装完复检**：用 ``importlib.metadata.version`` 取真实版本，确认落在约束区间内，
   否则判失败（绝不把"装完了"当成"装对了"）。
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

__all__ = [
    "InstallResult",
    "InstallAttempt",
    "HeavyPackage",
    "classify_pip_error",
    "estimate_timeout_seconds",
    "is_satisfied",
    "parse_requirement",
    "install_dependency",
    "HEAVY_PACKAGES",
    "DEFAULT_TIMEOUT_SECONDS",
    "HEAVY_TIMEOUT_SECONDS",
    "OFFICIAL_PYPI_INDEX",
    "PRESET_PYPI_MIRRORS",
]

# ── 超时参数（决策八）─────────────────────────────────────
DEFAULT_TIMEOUT_SECONDS = 180.0
"""普通包的基础超时。"""

HEAVY_TIMEOUT_SECONDS = 900.0
"""重包（paddle/torch 等）的基础超时。"""

_DRY_RUN_BASE_SECONDS = 60.0
"""dry-run 精算时的固定底：连接握手 + 依赖解析开销。"""

_DRY_RUN_BANDWIDTH_BYTES_PER_SECOND = 200 * 1024.0
"""保守期望带宽（200 KB/s），据此把体积折算成下载时间。"""

_TIMEOUT_RETRY_MULTIPLIER = 2.0
"""大包超时归因后的加时重试倍数（最多重试一次）。"""


#: 已知重包：体积大 / 编译久，用固定大超时兜底。命中即免去 dry-run 探测。
HEAVY_PACKAGES: frozenset[str] = frozenset({
    "paddlepaddle",
    "paddlepaddle-gpu",
    "paddleocr",
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "tensorflow-cpu",
    "opencv-python",
    "opencv-python-headless",
    "playwright",
    "scipy",
    "numpy",
    "pandas",
    "scikit-learn",
    "mkl",
})


@dataclass(frozen=True, slots=True)
class HeavyPackage:
    """重包判定的规范化结果（同时给出归一后的分发包名）。"""

    name: str
    normalized: str
    is_heavy: bool
    base_timeout: float


#: 官方 PyPI 索引（顺序回退链的首位 / 兜底位）。
OFFICIAL_PYPI_INDEX = "https://pypi.org/simple"

#: 预置国内镜像清单（决策七：预置候选，**启用决定权交给用户**）。
#: 均为标准 PyPI simple 协议；域名仍须在 egress 白名单内才会被实际使用。
PRESET_PYPI_MIRRORS: tuple[tuple[str, str], ...] = (
    ("mirrors.tuna.tsinghua.edu.cn", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"),
    ("mirrors.aliyun.com", "https://mirrors.aliyun.com/pypi/simple"),
    ("mirrors.cloud.tencent.com", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("pypi.mirrors.ustc.edu.cn", "https://pypi.mirrors.ustc.edu.cn/simple"),
)


# ── 失败分类（决策五）─────────────────────────────────────
# pip 在「源可达但缺该版本 / 约束冲突」时给出的特征串：**不应**摘流。
_VERSION_ERROR_MARKERS: tuple[str, ...] = (
    "no matching distribution found",
    "could not find a version",
    "from versions:",
    "resolutionimpossible",
    "conflicting dependencies",
    "cannot install",
    "no versions found",
)

# 明确的网络 / 传输故障特征串：**应**摘流。
_NETWORK_ERROR_MARKERS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "connection",
    "connectionerror",
    "connectionrefused",
    "connectionreset",
    "sslerror",
    "ssl:",
    "temporary failure in name resolution",
    "name or service not known",
    "getaddrinfo",
    "nodename nor servname",
    "read timed out",
    "network is unreachable",
    "proxyerror",
    "max retries exceeded",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)


class PipFailureKind:
    """pip 失败的归因类别（三分类，决定是否摘流）。"""

    NETWORK = "network"      # 网络/传输故障 ⇒ 摘流（record_failure）
    VERSION = "version"      # 源可达但无满足版本 ⇒ 不摘流，仅换源
    CONFLICT = "conflict"    # 约束自相冲突 ⇒ 不摘流，报用户（换源无用）


def classify_pip_error(text: str) -> str:
    """把 pip 的 stderr/stdout 归类为 NETWORK / VERSION / CONFLICT。

    判定顺序：**先看版本类特征**（``no matching distribution`` 里也含
    ``could not find`` 之类的中性词），再看网络类。两者都不命中时保守判
    ``NETWORK``（宁可摘流一个可疑源，也不要反复卡在它身上）。
    """
    lowered = (text or "").casefold()
    if "resolutionimpossible" in lowered or "conflicting dependencies" in lowered:
        return PipFailureKind.CONFLICT
    if any(marker in lowered for marker in _VERSION_ERROR_MARKERS):
        return PipFailureKind.VERSION
    if any(marker in lowered for marker in _NETWORK_ERROR_MARKERS):
        return PipFailureKind.NETWORK
    return PipFailureKind.NETWORK


# ── 需求解析与版本校验 ────────────────────────────────────
_REQ_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]*)\])?"
    r"\s*(?P<spec>.*?)\s*$"
)
_CLAUSE_RE = re.compile(r"(===|==|~=|!=|<=|>=|<|>)\s*([^\s,]+)")


def parse_requirement(requirement: str) -> tuple[str, str, str]:
    """拆分需求字符串为 ``(分发包名, extras, 版本约束串)``。

    ``pdfplumber>=0.10,<0.12`` → ``("pdfplumber", "", ">=0.10,<0.12")``
    ``omnicrawler-platform[pdf]`` → ``("omnicrawler-platform", "pdf", "")``
    """
    match = _REQ_RE.match(requirement or "")
    if match is None:
        return (requirement.strip(), "", "")
    return (
        match.group("name"),
        (match.group("extras") or "").strip(),
        (match.group("spec") or "").strip(),
    )


def _normalize_name(name: str) -> str:
    """PEP 503 归一：``Foo_Bar.Baz`` → ``foo-bar-baz``（重包表命中的关键）。"""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).casefold()


def resolve_heavy_package(name: str) -> HeavyPackage:
    """判断分发包是否属于重包表，并给出其基础超时。"""
    normalized = _normalize_name(name)
    heavy = normalized in {_normalize_name(item) for item in HEAVY_PACKAGES}
    return HeavyPackage(
        name=name,
        normalized=normalized,
        is_heavy=heavy,
        base_timeout=HEAVY_TIMEOUT_SECONDS if heavy else DEFAULT_TIMEOUT_SECONDS,
    )


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """把版本号解析成可比较元组；含非数字段（如 rc1）时按段尽力取数字前缀。

    解析不出任何数字 ⇒ 返回 None（调用方保守放行，不因无法比较而误判失败）。
    """
    text = (raw or "").strip().split("+", 1)[0]
    parts: list[int] = []
    for segment in re.split(r"[._-]", text):
        match = re.match(r"^(\d+)", segment)
        if match is None:
            # 遇到 dev/rc/post 等前缀段尝试继续；纯字母段直接停
            if segment and segment[0].isdigit() is False and segment[:1].isalpha():
                continue
            continue
        parts.append(int(match.group(1)))
    return tuple(parts) if parts else None


def is_satisfied(installed_version: str, spec: str) -> bool:
    """校验已安装版本是否满足约束串（支持 ``,`` 分隔的多子句）。

    无法解析的约束 / 无法比较的版本一律**保守返回 True** —— 本函数的用途是
    "抓出明显不满足"，不能因为解析器局限把成功安装误判成失败。
    """
    spec = (spec or "").strip()
    if not spec:
        return True
    installed = _parse_version(installed_version)
    if installed is None:
        return True
    clauses = _CLAUSE_RE.findall(spec)
    if not clauses:
        return True
    for _operator, raw_target in clauses:
        target = _parse_version(raw_target)
        if target is None:
            continue
        installed_padded = _pad(installed, len(target))
        target_padded = _pad(target, len(installed))
        if _operator in ("==", "==="):
            if installed_padded != target_padded:
                return False
        elif _operator == "!=":
            if installed_padded == target_padded:
                return False
        elif _operator == ">=":
            if installed_padded < target_padded:
                return False
        elif _operator == "<=":
            if installed_padded > target_padded:
                return False
        elif _operator == ">":
            if installed_padded <= target_padded:
                return False
        elif _operator == "<":
            if installed_padded >= target_padded:
                return False
        elif _operator == "~=":
            # 兼容版本：>=target 且 ==target 的前缀（去掉最后一段）
            if installed_padded < target_padded:
                return False
            prefix = target_padded[:-1] if len(target_padded) > 1 else target_padded
            if installed_padded[: len(prefix)] != prefix:
                return False
    return True


def _pad(value: tuple[int, ...], length: int) -> tuple[int, ...]:
    if len(value) >= length:
        return value
    return value + (0,) * (length - len(value))


# ── 超时估算（决策八：组合 + 保险）─────────────────────────
def _dry_run_download_bytes(
    requirement: str,
    index_url: str,
    *,
    python_executable: str,
    timeout: float,
) -> int | None:
    """用 ``pip install --dry-run --report`` 取本次安装的预估下载体积。

    仅用于**未命中重包表**时的精算；任何失败（老 pip / 索引不可达 / 解析异常）
    都返回 ``None`` 让调用方降级到默认值 —— 精算失败绝不能阻塞安装。
    """
    try:
        proc = subprocess.run(
            [
                python_executable, "-m", "pip", "install",
                requirement,
                "--index-url", index_url,
                "--dry-run",
                "--quiet",
                "--report", "-",
                "--disable-pip-version-check",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    total = 0
    for item in report.get("install", []) or []:
        archive = item.get("download_info", {}).get("archive_info", {}) or {}
        # pip 在不同版本里把大小放在 hash / size / hashes 下，逐一尝试
        size = archive.get("size") or archive.get("bytes")
        if isinstance(size, int):
            total += size
    return total or None


def estimate_timeout_seconds(
    requirement: str,
    index_url: str,
    *,
    python_executable: str = "",
    allow_dry_run: bool = True,
    probe_timeout: float = 30.0,
    override: float | None = None,
) -> float:
    """四层超时估算（决策八）。

    1. 重包表命中 ⇒ 直接用 ``HEAVY_TIMEOUT_SECONDS``；
    2. 未命中且有 ``allow_dry_run`` ⇒ dry-run 取体积精算；
    3. 精算失败/关闭 ⇒ 默认 ``DEFAULT_TIMEOUT_SECONDS``；
    4. 调用方可用 ``override`` 强制覆盖（对应 config 项）。
    """
    if override is not None and override > 0:
        return float(override)
    name, _extras, _spec = parse_requirement(requirement)
    heavy = resolve_heavy_package(name)
    if heavy.is_heavy:
        return heavy.base_timeout
    if allow_dry_run:
        executable = python_executable or sys.executable
        size = _dry_run_download_bytes(
            requirement, index_url, python_executable=executable, timeout=probe_timeout
        )
        if size:
            download_seconds = size / _DRY_RUN_BANDWIDTH_BYTES_PER_SECOND
            return max(heavy.base_timeout, _DRY_RUN_BASE_SECONDS + download_seconds)
    return heavy.base_timeout


# ── 安装结果 ──────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class InstallAttempt:
    """单个源的安装尝试记录。"""

    host: str
    canonical: str
    index_url: str
    ok: bool
    kind: str = ""
    detail: str = ""


@dataclass(slots=True)
class InstallResult:
    """安装终态。

    ``ok`` 为 True 时 ``actual_version`` 一定已通过复检；否则 ``attempts`` 里
    保留了逐源归因，供 UI 展示「原因链」。
    """

    ok: bool
    package: str
    spec: str
    actual_version: str = ""
    attempts: list[InstallAttempt] = field(default_factory=list)
    detail: str = ""

    @property
    def summary(self) -> str:
        """一句结论（给 Toast 用）。"""
        if self.ok:
            suffix = f" {self.actual_version}" if self.actual_version else ""
            return f"已安装 {self.package}{self.spec}{suffix}"
        return f"安装 {self.package}{self.spec} 失败：{self.detail or '所有来源均不可用'}"

    def reason_chain(self) -> str:
        """逐源原因链（给"详情"对话框用，可复制）。"""
        lines = [f"包: {self.package}{self.spec}", f"结果: {'成功' if self.ok else '失败'}"]
        for attempt in self.attempts:
            status = "成功" if attempt.ok else f"失败[{attempt.kind}]"
            lines.append(f"  - {attempt.host} ({attempt.index_url}): {status} {attempt.detail}".rstrip())
        if self.detail:
            lines.append(f"结论: {self.detail}")
        return "\n".join(lines)


def _run_pip(
    requirement: str,
    index_url: str,
    *,
    python_executable: str,
    timeout: float,
) -> tuple[int, str]:
    """执行一次单源 pip 安装。返回 ``(returncode, 合并输出)``。

    注意用**列表传参**（不经 shell）⇒ 版本约束里的 ``>=`` / ``<`` 无需加引号，
    也不会被 shell 解释。
    """
    proc = subprocess.run(
        [
            python_executable, "-m", "pip", "install",
            requirement,
            "--index-url", index_url,
            "--retries", "2",
            "--no-input",
            "--disable-pip-version-check",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return proc.returncode, combined


def _installed_version(distribution: str) -> str:
    """读取已安装分发包的真实版本；缺失时返回空串。"""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        # 分发包名与 import 名可能不同，尝试归一后再查一次
        try:
            for dist in importlib.metadata.distributions():
                if _normalize_name(dist.metadata["Name"] or "") == _normalize_name(distribution):
                    return dist.version
        except Exception:  # noqa: BLE001 - 探测失败按"未知"处理
            return ""
        return ""
    except Exception:  # noqa: BLE001
        return ""


def install_dependency(
    requirement: str,
    *,
    sources: Iterable[tuple[str, str]],
    registry: Any = None,
    python_executable: str = "",
    timeout_override: float | None = None,
    allow_dry_run: bool = True,
    max_attempts: int | None = None,
) -> InstallResult:
    """顺序多源回退安装一个依赖，并复检实际版本。

    Parameters
    ----------
    requirement:
        需求字符串，如 ``"pdfplumber>=0.10,<0.12"`` 或 ``"playwright"``。
    sources:
        ``(canonical, host)`` 有序列表 —— 通常来自
        ``MirrorRegistry.ordered_endpoints("pypi.org")``；为空时用官方源直连。
    registry:
        可选的 ``MirrorRegistry``；提供时按分类结果回写健康分
        （**只有网络类失败才摘流**）。
    timeout_override:
        覆盖自适应超时（对应 config 的 ``dependencies.install_timeout_seconds``）。
    allow_dry_run:
        是否允许 dry-run 精算体积（关闭则只用重包表 + 默认值）。
    max_attempts:
        最多尝试的源数（默认全部）。用于避免极端情况下长时间阻塞。

    Returns
    -------
    InstallResult
        成功时 ``actual_version`` 已复检；失败时 ``attempts`` 含逐源归因。
    """
    requirement = (requirement or "").strip()
    executable = python_executable or sys.executable
    package, _extras, spec = parse_requirement(requirement)
    result = InstallResult(ok=False, package=package, spec=spec)

    # 源列表：显式传入优先；为空则官方源直连（保持"无镜像也能装"）
    source_list = [(str(canonical), str(host)) for canonical, host in sources if host]
    if not source_list:
        source_list = [("pypi.org", "pypi.org")]

    # 判定是否"重包"——超时归因（第 4 层）要靠它区分"估短了"还是"源挂了"
    heavy = resolve_heavy_package(package)

    attempted = 0
    timeout_retried = False
    for canonical, host in source_list:
        if max_attempts is not None and attempted >= max_attempts:
            break
        attempted += 1
        index_url = _index_url_for(host, canonical)
        timeout = estimate_timeout_seconds(
            requirement,
            index_url,
            python_executable=executable,
            allow_dry_run=allow_dry_run,
            override=timeout_override,
        )
        try:
            returncode, output = _run_pip(
                requirement, index_url, python_executable=executable, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            # ── 第 4 层保险：超时归因 ──────────────────────
            # 判定"超时估短了"而不是"源挂了"：仅当该源尚未加时重试过，且
            # 该包属重包（或超时阈值本就低于重包档）时才加时重试**同一源**。
            # 否则四个健康源各超时一次、永远装不上。
            if not timeout_retried and (heavy.is_heavy or timeout < HEAVY_TIMEOUT_SECONDS):
                timeout_retried = True
                bigger = timeout * _TIMEOUT_RETRY_MULTIPLIER
                result.attempts.append(
                    InstallAttempt(
                        host=host, canonical=canonical, index_url=index_url,
                        ok=False, kind="timeout-retry",
                        detail=f"超时 {timeout:.0f}s，按大包加时重试 {bigger:.0f}s",
                    )
                )
                try:
                    returncode, output = _run_pip(
                        requirement, index_url, python_executable=executable, timeout=bigger
                    )
                except subprocess.TimeoutExpired:
                    _record_failure(registry, canonical, host)
                    result.attempts.append(
                        InstallAttempt(
                            host=host, canonical=canonical, index_url=index_url,
                            ok=False, kind=PipFailureKind.NETWORK,
                            detail=f"加时后仍超时（{bigger:.0f}s）",
                        )
                    )
                    continue
            else:
                _record_failure(registry, canonical, host)
                result.attempts.append(
                    InstallAttempt(
                        host=host, canonical=canonical, index_url=index_url,
                        ok=False, kind=PipFailureKind.NETWORK,
                        detail=f"超时（{timeout:.0f}s）",
                    )
                )
                continue
        except OSError as exc:
            _record_failure(registry, canonical, host)
            result.attempts.append(
                InstallAttempt(
                    host=host, canonical=canonical, index_url=index_url,
                    ok=False, kind=PipFailureKind.NETWORK,
                    detail=f"无法启动 pip：{exc}",
                )
            )
            continue

        if returncode == 0:
            # ── 复检：绝不把"装完了"当"装对了" ──────────────
            actual = _installed_version(package)
            if actual and not is_satisfied(actual, spec):
                result.attempts.append(
                    InstallAttempt(
                        host=host, canonical=canonical, index_url=index_url,
                        ok=False, kind=PipFailureKind.CONFLICT,
                        detail=f"装到的版本 {actual} 不满足 {spec or '约束'}",
                    )
                )
                _record_success(registry, canonical, host)
                result.detail = f"版本复检失败：实际 {actual}，要求 {spec}"
                return result
            _record_success(registry, canonical, host)
            result.attempts.append(
                InstallAttempt(
                    host=host, canonical=canonical, index_url=index_url,
                    ok=True, detail=f"已安装 {actual or '（版本未知）'}",
                )
            )
            result.ok = True
            result.actual_version = actual
            result.detail = ""
            return result

        # 失败：分类决定是否摘流（决策五）
        kind = classify_pip_error(output)
        if kind == PipFailureKind.NETWORK:
            _record_failure(registry, canonical, host)
        # VERSION / CONFLICT 不摘流 —— 源本身是好的，只是没有这个版本/约束冲突
        tail = _last_error_line(output)
        result.attempts.append(
            InstallAttempt(
                host=host, canonical=canonical, index_url=index_url,
                ok=False, kind=kind, detail=tail,
            )
        )
        if kind == PipFailureKind.CONFLICT:
            # 约束自相冲突：换源无用，直接收敛（避免无谓拖时间）
            result.detail = f"版本约束冲突：{tail}"
            return result

    if not result.detail:
        kinds = {attempt.kind for attempt in result.attempts}
        if kinds == {PipFailureKind.VERSION}:
            result.detail = f"所有来源均无满足 {spec or '该'} 约束的版本"
        else:
            result.detail = "所有来源均不可用"
    return result


def _index_url_for(host: str, canonical: str) -> str:
    """把镜像 host 拼成 pip 的 ``--index-url``。

    约定：canonical 为 ``pypi.org`` 的组使用各 host 的 ``/simple``；
    已知镜像使用预置清单里的完整 URL（部分镜像的路径不是根 ``/simple``）。
    """
    if host in ("pypi.org", "files.pythonhosted.org"):
        return OFFICIAL_PYPI_INDEX
    for known_host, known_url in PRESET_PYPI_MIRRORS:
        if _normalize_name(host) == _normalize_name(known_host):
            return known_url
    return f"https://{host}/simple"


def _record_success(registry: Any, canonical: str, host: str) -> None:
    if registry is None:
        return
    try:
        registry.record_success(canonical, host)
    except Exception as exc:  # noqa: BLE001 - 健康分回写失败不影响安装结论
        LOGGER.warning("镜像成功回写异常: %s", exc)


def _record_failure(registry: Any, canonical: str, host: str) -> None:
    if registry is None:
        return
    try:
        registry.record_failure(canonical, host)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("镜像失败回写异常: %s", exc)


def _last_error_line(output: str) -> str:
    """从 pip 输出里取最后一条非空、含 ``ERROR`` 的行（无则取最后一非空行）。"""
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if "error" in line.casefold():
            return line[:300]
    return lines[-1][:300] if lines else ""
