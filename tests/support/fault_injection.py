"""受控故障注入：把「故障可注入」变成可复用的验收手段。

`优化方案.md` §1.2 把可靠性的验收方式升级为「**故障可注入 / 可定位 / 可恢复**的闭环，
而不是仅依赖零散用例」。本模块提供该闭环的「注入」一端。

设计要点：

* **单一收口**：只替换 :meth:`Pipeline._fetch_checked`（每个请求恰好经过一次），
  因此注入的失败与真实失败走**完全相同**的异常处理路径——被测对象是真实的失败
  处理逻辑，而不是某个测试替身的行为。
* **替换用的必须是普通函数，不能用带 `__call__` 的实例**。`Pipeline._fetch_checked`
  在源码里以 `self._fetch_checked(run_id, request)` 与
  `executor.submit(self._fetch_checked, run_id, request)` 两种方式取用；
  只有函数才实现描述符协议、才会被绑定并拿到实例。若挂一个带 `__call__` 的实例，
  它不会被绑定，于是既拿不到 Pipeline 实例、也无法调用原实现
  （实测报 `TypeError: ... missing 1 required positional argument: 'request'`）。
  因此这里用 `_patched(pipeline, run_id, request)` 转发，记录器显式接收 `pipeline`。
* **按需编排**：可按调用序、按 URL、按间隔注入；可指定异常类型以覆盖
  「可重试的网络失败」「不可重试的策略拦截」等不同分支（见
  :func:`omnicrawler.core.errors.describe_error`）。
* **可复现**：:attr:`InjectedFetchFaults.calls` / :attr:`InjectedFetchFaults.injected`
  记录每次调用与每次注入，便于断言「注入确实发生了」而不是测试空跑。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.error import URLError

from omnicrawler.core.models import CrawlRequest

#: 一次调用的判定函数：``(第几次调用, 请求) -> 是否注入失败``。调用序号从 0 开始。
ShouldFail = Callable[[int, CrawlRequest], bool]
#: 异常工厂：``(第几次调用, 请求) -> 要抛出的异常``。
ExceptionFactory = Callable[[int, CrawlRequest], BaseException]


def default_exception(_index: int, request: CrawlRequest) -> BaseException:
    """默认注入「网络瞬时失败」——`describe_error` 会判为可重试。

    选用可重试异常是有意的：这样注入的失败既能触发轮内重试，
    也能在重试次数耗尽后进入 dead-letter，从而同时覆盖
    「可恢复」与「终态失败」两条路径。
    """
    return URLError(f"注入的抓取故障: {request.url}")


def policy_exception(_index: int, request: CrawlRequest) -> BaseException:
    """注入「策略拦截」——`describe_error` 判为不可重试，走 policy 分支。"""
    return PermissionError(f"注入的策略拦截: {request.url}")


def fail_all() -> ShouldFail:
    """每一个请求都失败。"""
    return lambda _index, _request: True


def fail_first(count: int) -> ShouldFail:
    """仅前 *count* 个请求失败（其余走真实抓取）。"""

    def predicate(index: int, _request: CrawlRequest) -> bool:
        return index < count

    return predicate


def fail_every(nth: int) -> ShouldFail:
    """每隔 *nth* 个请求注入一次失败（从第 1 个开始，即第 1、nth+1、… 个）。"""
    if nth < 1:
        raise ValueError("nth 必须 >= 1")

    def predicate(index: int, _request: CrawlRequest) -> bool:
        return index % nth == 0

    return predicate


def fail_urls(*suffixes: str) -> ShouldFail:
    """URL 以任一 *suffix* 结尾的请求失败。"""
    if not suffixes:
        raise ValueError("至少需要一个后缀")

    def predicate(_index: int, request: CrawlRequest) -> bool:
        return any(request.url.endswith(suffix) for suffix in suffixes)

    return predicate


@dataclass
class InjectedFetchFaults:
    """故障记录器：判定该失败就抛异常，否则转交原本的实现。

    ``original`` 是**未绑定**的 ``Pipeline._fetch_checked``，因此调用时要显式传入
    ``pipeline`` 实例——由 :func:`inject_fetch_faults` 里那个普通函数完成绑定。
    """

    original: Callable[..., object]
    should_fail: ShouldFail
    exc_factory: ExceptionFactory = default_exception
    calls: list[CrawlRequest] = field(default_factory=list)
    injected: list[str] = field(default_factory=list)

    def intercept(self, pipeline: object, run_id: str, request: CrawlRequest) -> object:
        index = len(self.calls)
        self.calls.append(request)
        if self.should_fail(index, request):
            self.injected.append(request.url)
            raise self.exc_factory(index, request)
        return self.original(pipeline, run_id, request)


def inject_fetch_faults(
    monkeypatch,
    *,
    should_fail: ShouldFail,
    exc_factory: ExceptionFactory = default_exception,
) -> InjectedFetchFaults:
    """把故障注入挂到 :meth:`Pipeline._fetch_checked` 上并返回记录器。

    Args:
        monkeypatch: pytest 的 ``monkeypatch`` fixture（测试结束自动还原）。
        should_fail: 判定哪些请求该失败。
        exc_factory: 要抛出的异常；默认是「网络瞬时失败」（可重试）。

    Returns:
        :class:`InjectedFetchFaults`，含 ``calls`` / ``injected`` 供断言。
    """
    from omnicrawler.pipeline import Pipeline

    faults = InjectedFetchFaults(
        original=Pipeline._fetch_checked, should_fail=should_fail, exc_factory=exc_factory
    )

    def _patched(pipeline: object, run_id: str, request: CrawlRequest) -> object:
        # 普通函数 → 实现描述符协议 → 自动拿到 Pipeline 实例；
        # 用带 __call__ 的实例替换拿不到实例，也调不动原实现。
        return faults.intercept(pipeline, run_id, request)

    monkeypatch.setattr(Pipeline, "_fetch_checked", _patched, raising=True)
    return faults
