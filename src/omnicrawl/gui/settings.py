"""QSettings 封装模块。

管理应用持久化设置：最近文件、主题、快捷键、项目根目录、隐私选项等。
"""

from typing import Optional

from PyQt6.QtCore import QSettings

SETTINGS_ORG = "OmniCrawler"
SETTINGS_APP = "OmniCrawlerGUI"


def make_qsettings(org: str = SETTINGS_ORG, app: str = SETTINGS_APP) -> QSettings:
    """构造便携安全的 QSettings 实例。

    源码模式回退到 Qt 默认（注册表 / INI 由平台决定）；冻结便携模式下
    一律落到应用数据根内的 INI——与 F53 同语义（脱离注册表，同机多份
    便携包互不串扰）。注意：``QSettings.setPath``/``setDefaultFormat``
    对 ``QSettings(org, app)`` 构造在 Windows 上不生效（仍走注册表），
    必须用带路径的构造才能真正落盘。
    """
    from ..core.runtime_paths import is_frozen, portable_data_root

    if is_frozen():
        root = portable_data_root()
        settings_dir = root / ".omnicrawler" / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        return QSettings(str(settings_dir / f"{org}-{app}.ini"), QSettings.Format.IniFormat)
    return QSettings(org, app)


class AppSettings:
    """应用设置管理器，封装 QSettings 读写。"""

    _instance: Optional["AppSettings"] = None

    def __init__(self) -> None:
        from ..core.runtime_paths import is_frozen, portable_data_root

        if is_frozen():
            # F53：便携版设置落在程序数据目录的 INI，脱离 HKCU 注册表，
            # 同机解压多份便携包互不串扰配置/历史/结果。
            ini_path = portable_data_root() / "settings.ini"
            self._settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
        else:
            self._settings = make_qsettings(SETTINGS_ORG, SETTINGS_APP)
        # Sandboxed Windows sessions can deny registry writes.  Keep settings
        # functional for the active session rather than silently ignoring user
        # actions such as theme and accessibility changes.
        self._session_values: dict[str, object] = {}

    def _value(self, key: str, default: object, value_type: type):
        if key in self._session_values:
            return self._session_values[key]
        return self._settings.value(key, default, type=value_type)

    def _set_value(self, key: str, value: object) -> None:
        self._session_values[key] = value
        try:
            if self._settings.isWritable():
                self._settings.setValue(key, value)
        except RuntimeError:
            # The session fallback remains valid if Qt has disposed QSettings.
            pass

    # ---- S3.1.16：公共读写接口（消费方不再直调私有方法） ----

    def value(self, key: str, default: object, value_type: type):
        return self._value(key, default, value_type)

    def set_value(self, key: str, value: object) -> None:
        self._set_value(key, value)

    @classmethod
    def reset(cls) -> None:
        """丢弃当前单例（数据模式变更后重建，使 settings.ini 落新数据根）。"""
        cls._instance = None

    @classmethod
    def instance(cls) -> "AppSettings":
        if cls._instance is None:
            cls._instance = cls()
        else:
            # Qt 会在 QApplication 销毁时清理其 C++ 侧对象。测试、插件重载和
            # “退出到启动器”场景可能在同一 Python 进程内重新创建应用，此时
            # Python 单例仍在，但内部 QSettings 已失效。
            try:
                cls._instance._settings.status()
            except RuntimeError:
                cls._instance = cls()
        return cls._instance

    # ---- 项目根目录 ----
    @property
    def project_root(self) -> str:
        return self._value("project/root", "", str)

    @project_root.setter
    def project_root(self, value: str) -> None:
        self._set_value("project/root", value)

    # ---- omnicrawl 命令路径 ----
    @property
    def omnicrawl_path(self) -> str:
        return self._value("omnicrawl/path", "omnicrawl", str)

    @omnicrawl_path.setter
    def omnicrawl_path(self, value: str) -> None:
        self._set_value("omnicrawl/path", value)

    @property
    def omnicrawl_version(self) -> str:
        return self._value("omnicrawl/version", "", str)

    @omnicrawl_version.setter
    def omnicrawl_version(self, value: str) -> None:
        self._set_value("omnicrawl/version", value)

    # ---- 最近文件 ----
    @property
    def recent_files(self) -> list[str]:
        value = self._value("recent/files", [], list)
        return [str(p) for p in value if isinstance(p, str)]

    def add_recent_file(self, filepath: str) -> None:
        files = self.recent_files
        files = [f for f in files if f != filepath]
        files.insert(0, filepath)
        self._set_value("recent/files", files[:5])

    def clear_recent(self) -> None:
        """清除最近文件列表（公开接口，封装底层存储细节）。"""
        self._session_values.pop("recent/files", None)
        try:
            self._settings.remove("recent/files")
        except RuntimeError:
            pass

    # ---- 主题 ----
    @property
    def theme(self) -> str:
        return self._value("appearance/theme", "light", str)

    @theme.setter
    def theme(self, value: str) -> None:
        self._set_value("appearance/theme", value)

    # ---- 快捷键 ----
    @property
    def shortcuts(self) -> dict[str, str]:
        defaults = {
            "save": "Ctrl+S",
            "run": "Ctrl+R",
            "stop": "Ctrl+Shift+S",
            "toggle_editor": "Ctrl+E",
            "open_templates": "Ctrl+T",
            "refresh": "F5",
            "format_yaml": "Ctrl+Shift+F",
            "toggle_dnd": "Ctrl+Shift+M",
        }
        stored = self._value("shortcuts", defaults, dict)
        return {**defaults, **{k: v for k, v in stored.items() if isinstance(v, str)}}

    # ---- 隐私选项 ----
    @property
    def privacy_redact_enabled(self) -> bool:
        return self._value("privacy/redact_enabled", True, bool)

    @property
    def crash_report_enabled(self) -> bool:
        return self._value("privacy/crash_report", False, bool)

    @crash_report_enabled.setter
    def crash_report_enabled(self, value: bool) -> None:
        self._set_value("privacy/crash_report", value)

    # ---- 体验选项 ----
    @property
    def auto_open_result(self) -> bool:
        return self._value("behavior/auto_open_result", False, bool)

    @auto_open_result.setter
    def auto_open_result(self, value: bool) -> None:
        self._set_value("behavior/auto_open_result", value)

    @property
    def ui_mode(self) -> str:
        value = self._value("appearance/ui_mode", "simple", str)
        return value if value in {"simple", "professional", "developer"} else "simple"

    @ui_mode.setter
    def ui_mode(self, value: str) -> None:
        self._set_value("appearance/ui_mode", value)

    @property
    def sound_enabled(self) -> bool:
        return self._value("behavior/sound_enabled", True, bool)

    @sound_enabled.setter
    def sound_enabled(self, value: bool) -> None:
        self._set_value("behavior/sound_enabled", value)

    @property
    def dnd_enabled(self) -> bool:
        return self._value("behavior/dnd_enabled", False, bool)

    @dnd_enabled.setter
    def dnd_enabled(self, value: bool) -> None:
        self._set_value("behavior/dnd_enabled", value)

    @property
    def markdown_export_enabled(self) -> bool:
        """任务完成后是否自动导出 Markdown。"""
        return self._value("export/markdown_enabled", False, bool)

    @markdown_export_enabled.setter
    def markdown_export_enabled(self, value: bool) -> None:
        self._set_value("export/markdown_enabled", value)

    # ---- 反检测/隐身 ----
    @property
    def stealth_level(self) -> str:
        return self._value("stealth/level", "medium", str)

    @stealth_level.setter
    def stealth_level(self, value: str) -> None:
        self._set_value("stealth/level", value)

    @property
    def stealth_webdriver_override(self) -> bool:
        return self._value("stealth/webdriver_override", True, bool)

    @stealth_webdriver_override.setter
    def stealth_webdriver_override(self, value: bool) -> None:
        self._set_value("stealth/webdriver_override", value)

    @property
    def stealth_canvas_noise(self) -> bool:
        return self._value("stealth/canvas_noise", True, bool)

    @property
    def stealth_webgl_noise(self) -> bool:
        return self._value("stealth/webgl_noise", True, bool)

    @property
    def stealth_plugin_spoof(self) -> bool:
        return self._value("stealth/plugin_spoof", True, bool)

    @property
    def stealth_audio_noise(self) -> bool:
        return self._value("stealth/audio_noise", True, bool)

    @property
    def stealth_timezone_spoof(self) -> bool:
        return self._value("stealth/timezone_spoof", True, bool)

    @property
    def stealth_locale_spoof(self) -> bool:
        return self._value("stealth/locale_spoof", True, bool)

    @property
    def proxy_list(self) -> str:
        value = self._value("proxy/list", "", str)
        if value and value.startswith("secret://"):
            # S2.2.2 出口解引用：INI 只存引用，此处还原明文；
            # 解引用失败（store 被删/密钥丢失）返回空串，不泄漏引用串给 UI。
            from ..core.credentials import get_secret

            try:
                return get_secret(value[len("secret://") :])
            except Exception:
                return ""
        return value

    @property
    def proxy_strategy(self) -> str:
        return self._value("proxy/strategy", "weighted", str)

    @property
    def proxy_rotate_every_n(self) -> int:
        return self._value("proxy/rotate_every_n", 20, int)

    # ---- 变更监控 ----
    @property
    def monitor_rules(self) -> list:
        return self._value("monitor/rules", [], list)

    @property
    def monitor_enabled(self) -> bool:
        return self._value("monitor/enabled", True, bool)

    @property
    def monitor_desktop_notify(self) -> bool:
        return self._value("monitor/desktop_notify", True, bool)

    @property
    def monitor_sound_notify(self) -> bool:
        return self._value("monitor/sound_notify", False, bool)

    @property
    def is_first_launch(self) -> bool:
        return self._value("app/first_launch", True, bool)

    @is_first_launch.setter
    def is_first_launch(self, value: bool) -> None:
        self._set_value("app/first_launch", value)

    @property
    def has_run_history(self) -> bool:
        return self._value("app/has_run_history", False, bool)

    @has_run_history.setter
    def has_run_history(self, value: bool) -> None:
        self._set_value("app/has_run_history", value)

    @property
    def env_checked(self) -> bool:
        return self._value("env/checked", False, bool)

    @env_checked.setter
    def env_checked(self, value: bool) -> None:
        self._set_value("env/checked", value)

    @property
    def interface_scale(self) -> int:
        return max(80, min(160, self._value("accessibility/scale", 100, int)))

    @interface_scale.setter
    def interface_scale(self, value: int) -> None:
        self._set_value("accessibility/scale", max(80, min(160, value)))

    @property
    def high_contrast(self) -> bool:
        return self._value("accessibility/high_contrast", False, bool)

    @high_contrast.setter
    def high_contrast(self, value: bool) -> None:
        self._set_value("accessibility/high_contrast", value)

    @property
    def color_blind_friendly(self) -> bool:
        return self._value("accessibility/color_blind_friendly", False, bool)

    @color_blind_friendly.setter
    def color_blind_friendly(self, value: bool) -> None:
        self._set_value("accessibility/color_blind_friendly", value)

    @property
    def reduced_motion(self) -> bool:
        return self._value("accessibility/reduced_motion", False, bool)

    @reduced_motion.setter
    def reduced_motion(self, value: bool) -> None:
        self._set_value("accessibility/reduced_motion", value)

    # ---- 任务历史清理 ----
    @property
    def history_max_entries(self) -> int:
        return self._value("history/max_entries", 100, int)

    @property
    def history_max_days(self) -> int:
        return self._value("history/max_days", 30, int)

    def sync(self) -> None:
        self._settings.sync()
