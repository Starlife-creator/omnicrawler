from __future__ import annotations

from datetime import datetime
from typing import Any


def evaluate_conditions(conditions: dict[str, Any]) -> tuple[bool, str]:
    if not conditions:
        return True, ""
    hour = datetime.now().hour
    allowed_hours = conditions.get("allowed_hours")
    if isinstance(allowed_hours, list) and allowed_hours:
        hours = {int(value) % 24 for value in allowed_hours}
        if hour not in hours:
            return False, f"当前小时 {hour} 不在允许运行时段"
    try:
        import psutil
    except ImportError:
        psutil = None
    if conditions.get("require_ac") or conditions.get("minimum_battery_percent") is not None:
        battery = psutil.sensors_battery() if psutil else None
        if battery is not None:
            if conditions.get("require_ac") and not battery.power_plugged:
                return False, "电脑未接通电源"
            minimum = float(conditions.get("minimum_battery_percent", 0))
            if not battery.power_plugged and battery.percent < minimum:
                return False, f"电量 {battery.percent:.0f}% 低于 {minimum:.0f}%"
    if conditions.get("require_network") and psutil:
        active = any(name.casefold() not in {"loopback", "lo"} and stats.isup for name, stats in psutil.net_if_stats().items())
        if not active:
            return False, "没有检测到可用网络接口"
    return True, ""
