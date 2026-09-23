"""登录会话委托：把「登录会话」页装配进主窗口，并对外提供跳转入口。

页面本身的逻辑（状态机显示、桥接、列表、删除）都在
:class:`omnicrawler.gui.views.login_session.LoginSessionView`；委托只负责
"它在主窗口里的位置"、"配置怎么给它"，以及"怎么从别处跳过去"（U4 的联动）。
"""
from __future__ import annotations

from ...core.config import AppConfig
from ..navigation import NavIndex
from ..views.login_session import LoginSessionView
from ._base import _BaseDelegate


class LoginSessionDelegate(_BaseDelegate):
    """装配与导航联动（不放业务逻辑）。"""

    #: 配置缓存（见 :meth:`current_config`）。用类级默认值而不是重写 ``__init__``：
    #: 这样就不必在类型层再引一次 ``MainWindow`` —— 那条边会让本模块多一条进环的
    #: 依赖，而基类已经给了 ``self._mw`` 的完整类型。
    _cached_source: object | None = None
    _cached_config: AppConfig | None = None

    def setup(self) -> LoginSessionView:
        """创建页面（由 MainWindow 加入页面栈）。"""
        view = LoginSessionView(self.current_config, settings=self._mw._settings)
        self.view = view
        return view

    def current_config(self) -> AppConfig:
        """当前 GUI 项目状态 → 引擎侧 ``AppConfig``。

        ★ **同一份 GUI 配置下必须返回同一个对象**：视图靠对象身份判断"是否换了
        工作区"，只有换了才重建会话管理器。若每次调用都新建对象，进行中的登录
        会话会在下一拍被悄悄丢弃（窗口还开着，页面却不再认它）。

        缓存键是 GUI 配置对象的身份；保存/重载后由 :meth:`invalidate` 显式丢弃缓存
        （``MainWindow._bind_application_controllers`` 调用）。
        """
        source = self._mw._config
        if self._cached_config is None or source is not self._cached_source:
            self._cached_source = source
            self._cached_config = self._mw._login_app_config()
        return self._cached_config

    def invalidate(self) -> None:
        """丢弃配置缓存（配置保存/重载后调用）。"""
        self._cached_source = None
        self._cached_config = None

    def open_page(self, *, account: str = "", url: str = "") -> None:
        """跳到登录会话页并预填登录区（U4：任务命中 401 / 302→login 时调用）。

        未显式给出时按当前任务配置预填：账户取 ``session.name``，
        地址取第一个种子 URL（目标站点通常就是登录站点；用户可随手改）。
        """
        view = getattr(self, "view", None)
        if view is None:
            return
        if not account or not url:
            default_account, default_url = self._page_defaults()
            account = account or default_account
            url = url or default_url
        view.prefill(account=account, url=url)
        self._mw._nav.setCurrentRow(NavIndex.LOGIN_SESSION)

    def _page_defaults(self) -> tuple[str, str]:
        config = self.current_config()
        session = config.section("session")
        account = str(session.get("name", "") or "")
        seeds = config.section("source").get("seeds")
        url = str(seeds[0]) if isinstance(seeds, (list, tuple)) and seeds else ""
        return account, url

    def shutdown(self) -> None:
        """窗口关闭时停掉轮询定时器（幂等）。"""
        view = getattr(self, "view", None)
        if view is not None:
            view.stop_timer()
