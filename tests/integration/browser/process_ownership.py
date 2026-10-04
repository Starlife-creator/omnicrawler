"""Snapshot browser identities; pre-existing processes belong to other runs."""
from __future__ import annotations


def browser_snapshot(psutil):
    result = {}
    for process in psutil.process_iter(["pid", "create_time", "name", "cmdline"]):
        try:
            command = " ".join(process.info.get("cmdline") or [])
            lowered = command.lower()
            name = (process.info.get("name") or "").lower()
            if name in {"chrome", "chrome.exe", "chromium", "chromium.exe", "chrome-headless-shell", "chrome-headless-shell.exe"} and ("ms-playwright" in lowered or ".runtime" in lowered):
                result[(process.info["pid"], process.info["create_time"])] = command
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result
