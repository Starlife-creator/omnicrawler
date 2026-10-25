"""计划条件判定（时段 / 电源 / 网络）。

A-P2-5：psutil 缺失时，电源 / 网络条件此前被**静默跳过**（fail-open）——
未安装 psutil 的精简环境里，「仅接通电源时运行」「要求联网」等用户配置被
无声忽略。现在改为 fail-closed：配置了对应条件而 psutil 不可用时直接拒绝本次
运行，并在返回原因里显式标注「条件依赖不可用」；时段条件不依赖 psutil，不受影响。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: psutil 缺失且条件被配置时返回的统一拒绝原因（状态 / 日志 / 测试共用口径）
PSUTIL_UNAVAILABLE_REASON = (
    "条件依赖 psutil 不可用：电源 / 网络检查无法执行，按安全策略拒绝本次运行"
    "（安装 psutil 后该条件才会生效；不要在未声明条件时静默放行）"
)


def evaluate_conditions(conditions: dict[str, Any]) -> tuple[bool, str]:
    if not conditions:
        return True, ""
    # S2.5.44：allowed_hours 与 next_run_at 统一 UTC 基准（调度时间戳为 UTC epoch）
    hour = datetime.now(UTC).hour
    allowed_hours = conditions.get("allowed_hours")
    if isinstance(allowed_hours, list) and allowed_hours:
        hours = {int(value) % 24 for value in allowed_hours}
        if hour not in hours:
            return False, f"当前小时 {hour}（UTC）不在允许运行时段"

    power_condition = bool(conditions.get("require_ac")) or conditions.get("minimum_battery_percent") is not None
    network_condition = bool(conditions.get("require_network"))

    if power_condition or network_condition:
        try:
            import psutil
        except ImportError:
            # A-P2-5：fail-closed——条件被配置而依赖缺失时不再静默放行。
            logger.warning("psutil 不可用：电源 / 网络条件按 fail-closed 拒绝本次运行")
            return False, PSUTIL_UNAVAILABLE_REASON

        if power_condition:
            battery = psutil.sensors_battery()
            if battery is not None:
                if conditions.get("require_ac") and not battery.power_plugged:
                    return False, "电脑未接通电源"
                minimum = float(conditions.get("minimum_battery_percent", 0))
                if not battery.power_plugged and battery.percent < minimum:
                    return False, f"电量 {battery.percent:.0f}% 低于 {minimum:.0f}%"
            # psutil 存在但平台无电池传感器（桌面 / 服务器）：维持既有跳过语义，不阻断。
        if network_condition:
            active = any(
                name.casefold() not in {"loopback", "lo"} and stats.isup
                for name, stats in psutil.net_if_stats().items()
            )
            if not active:
                return False, "没有检测到可用网络接口"
    return True, ""
