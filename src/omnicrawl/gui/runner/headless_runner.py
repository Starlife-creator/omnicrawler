"""无 GUI 模式执行器模块。

支持命令行直接执行爬虫任务，不初始化任何 GUI 组件。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..core.config_serializer import load_yaml
from ..runner.env_checker import check_omnicrawl


class HeadlessRunner:
    """无 GUI 模式执行器。

    在没有图形界面环境（如 CI/CD、SSH 会话）中执行爬虫任务。
    """

    def __init__(self, omnicrawl_path: str = "omnicrawl") -> None:
        """初始化无 GUI 执行器。

        Args:
            omnicrawl_path: omnicrawl 命令路径。
        """
        self._omnicrawl_path = omnicrawl_path

    def run(self, config_path: Path, log_level: str = "INFO") -> int:
        """执行爬虫任务。

        Args:
            config_path: YAML 配置文件路径。
            log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)。

        Returns:
            退出码：0 成功，1 失败。
        """
        # 检查配置文件
        if not config_path.is_file():
            print(f"\033[31m[ERROR]\033[0m 配置文件不存在: {config_path}", file=sys.stderr)
            return 1

        # 验证配置
        try:
            config = load_yaml(config_path)
            errors = config.validate()
            if errors:
                for err in errors:
                    print(f"\033[31m[ERROR]\033[0m 配置校验失败: {err}", file=sys.stderr)
                return 1
        except Exception as e:
            print(f"\033[31m[ERROR]\033[0m 配置加载失败: {e}", file=sys.stderr)
            return 1

        # 检查 omnicrawl
        available, version = check_omnicrawl(self._omnicrawl_path)
        if not available:
            print(f"\033[31m[ERROR]\033[0m omnicrawl 命令不可用 (路径: {self._omnicrawl_path})",
                  file=sys.stderr)
            return 1

        print(f"\033[36m[INFO]\033[0m OmniCrawler {version}")
        print(f"\033[36m[INFO]\033[0m 配置: {config_path}")
        print(f"\033[36m[INFO]\033[0m 项目: {config.project_name} (task_id: {config.task_id})")
        print("\033[36m[INFO]\033[0m 正在启动爬虫...")

        # 启动子进程
        process: subprocess.Popen | None = None
        try:
            env = dict(os.environ)
            env["PYTHONIOCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            process = subprocess.Popen(
                [self._omnicrawl_path, "run", "-c", str(config_path), "--log-level", log_level],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(config_path.parent),
            )

            # 实时输出日志
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                # 着色输出
                lower = line.lower()
                if "error" in lower or "exception" in lower:
                    print(f"\033[31m{line}\033[0m")
                elif "warn" in lower:
                    print(f"\033[33m{line}\033[0m")
                elif "progress" in lower:
                    print(f"\033[32m{line}\033[0m")
                else:
                    print(line)

            exit_code = process.wait()
            if exit_code == 0:
                print("\033[32m[SUCCESS]\033[0m 任务成功完成")
            else:
                print(f"\033[31m[FAILED]\033[0m 任务失败，退出码: {exit_code}")
            return exit_code

        except KeyboardInterrupt:
            print("\033[33m[WARN]\033[0m 用户中断")
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except Exception:
                    process.kill()
            return 1
        except Exception as e:
            print(f"\033[31m[ERROR]\033[0m 执行异常: {e}", file=sys.stderr)
            return 1
        finally:
            if process is not None and process.stdout:
                process.stdout.close()


def run_headless(config_path: str, log_level: str = "INFO",
                 omnicrawl_path: str = "omnicrawl") -> int:
    """无 GUI 模式的便捷入口函数。

    Args:
        config_path: YAML 配置文件路径。
        log_level: 日志级别。
        omnicrawl_path: omnicrawl 命令路径。

    Returns:
        退出码：0 成功，1 失败。
    """
    runner = HeadlessRunner(omnicrawl_path=omnicrawl_path)
    return runner.run(Path(config_path), log_level)
