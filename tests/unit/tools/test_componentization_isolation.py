"""第 3 批（最小切片）：组件化"装不上时宿主仍完全正常"。

**为什么先写这条，而不是先写打包脚本**

组件化的风险不在"打不出包"，而在**装了或没装时宿主的行为**。
用户的目标是"不要让用户看到一个空荡荡的 PDF 工作台"——所以真正要钉住的
是：**文档子系统缺失时，两大支柱的每条路径都必须正常**。

本文件用**进程级隔离**验证：把文档子系统从导入路径中屏蔽掉，
再检查宿主的入口是否仍然可用、以及 PDF 相关入口是否给出**可操作**提示
（而不是 ImportError 栈或静默失败）。

**判据**

1. 屏蔽文档子系统后，宿主 CLI 的**主命令**仍能列出与响应（不崩）；
2. 文档能力探测报 `available=False`，且**给出缺失清单与安装指引**；
3. 访问 PDF 命令时给的是**可操作提示**（提到"组件"/"安装"），不是裸栈；
4. 这份隔离下**没有静默失败**——不能出现"命令成功但什么都没做"。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"

#: 屏蔽脚本：用 meta_path 钩子让所有 omnicrawler 文档子模块导入失败。
#:
#: 比"删目录"更真实——目录还在，但模块不可用（等同于组件未安装时
#: 宿主拿到的是一个"存在但没有实现"的模块名）。
_BLOCKER = '''
import sys

_BLOCKED = ("omnicrawler.pdfx", "omnicrawler.convertx", "omnicrawler.apps",
            "omnicrawler.document_ir", "omnicrawler.doc_extractors",
            "omnicrawler.review", "omnicrawler.export")


class _Blocker:
    def find_module(self, name, path=None):
        return None

    def find_spec(self, name, path=None, target=None):
        for blocked in _BLOCKED:
            if name == blocked or name.startswith(blocked + "."):
                raise ImportError(f"blocked by isolation harness: {name}")
        return None


sys.meta_path.insert(0, _Blocker())
'''


def _run_isolated(tmp_path: Path, body: str) -> str:
    """在"文档子系统不可用"的子进程里跑 body，返回合并输出。"""
    import os

    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(_SRC)!r})\n"
        + _BLOCKER
        + body
    )
    out = tmp_path / "isolated.txt"
    # 数据根重定向到临时目录：本沙箱无权在 `~/.omnicrawler` 建目录
    # （实测 PermissionError，属环境限制而非本判据）。
    #
    # 用 `LOCALAPPDATA` 而不是 `OMNICRAWL_INSTALL_ROOT`：后者表示"安装根"，
    # 拼出来的数据根会变成 `<root>/.omnicrawler/...`，实测得到
    # `~/.omnicrawler/.omnicrawler/cache` 这种重复层级并失败。
    local_appdata = tmp_path / "localappdata"
    local_appdata.mkdir(parents=True, exist_ok=True)
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PATH": os.environ.get("PATH", ""),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "HOME": os.environ.get("HOME", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "LOCALAPPDATA": str(local_appdata),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }
    with out.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [sys.executable, "-c", script],
            stdout=handle, stderr=subprocess.STDOUT, timeout=180, check=False,
            env=env,
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, f"隔离进程异常退出：\n{text[-2000:]}"
    return text


def test_capability_probe_reports_unavailable(tmp_path: Path) -> None:
    """屏蔽文档子系统后，能力探测必须报不可用并给出缺失清单。"""
    text = _run_isolated(
        tmp_path,
        "from omnicrawler.services.document_facade import document_capability\n"
        "report = document_capability()\n"
        "print('AVAILABLE=' + repr(report['available']))\n"
        "print('MISSING=' + repr(report['missing']))\n"
        "print('REASON=' + report['reason'])\n",
    )
    assert "AVAILABLE=False" in text, (
        "文档子系统已被屏蔽，能力探测却报可用——那样「未装就不显示」无从判断。\n"
        + text[-800:]
    )
    assert "MISSING=[" in text and "pdfx" in text, (
        "缺失清单里没有 pdfx：用户不知道到底缺什么。\n" + text[-800:]
    )
    assert "组件" in text or "安装" in text, (
        "不可用原因没有给安装指引。\n" + text[-800:]
    )


def test_pdf_entrypoint_gives_actionable_error(tmp_path: Path) -> None:
    """请求文档能力时必须是**可操作提示**，不是裸 ImportError。"""
    text = _run_isolated(
        tmp_path,
        "from omnicrawler.services.document_facade import require_document\n"
        "try:\n"
        "    require_document('pdfx.service')\n"
        "except ImportError as exc:\n"
        "    print('MESSAGE_START')\n"
        "    print(str(exc))\n"
        "    print('MESSAGE_END')\n",
    )
    assert "MESSAGE_START" in text, "没有抛出 ImportError（可能静默成功了？）\n" + text[-800:]
    body = text.split("MESSAGE_START", 1)[1].split("MESSAGE_END", 1)[0]
    assert body.strip(), "错误消息为空"
    assert ("组件" in body) or ("安装" in body), (
        f"错误消息没有告诉用户该怎么办：{body.strip()[:300]!r}"
    )
    assert "Traceback" not in body, "把裸栈抛给用户了"


def test_host_cli_still_works_without_document_subsystem(tmp_path: Path) -> None:
    """两大支柱的主命令在文档子系统缺失时必须照常工作。

    这是"组件化"的核心卖点：**没装文档工作台，爬虫+AI 与插件市场不受影响**。
    """
    text = _run_isolated(
        tmp_path,
        "import json\n"
        "from omnicrawler.cli._main import SECTION_ORDER\n"
        "sections = list(SECTION_ORDER)\n"
        "print('SECTIONS=' + json.dumps(sections, ensure_ascii=False))\n"
        "print('HAS_TASK=' + repr('task' in sections))\n"
        "print('HAS_PLUGINS=' + repr('plugins' in sections))\n"
        "print('HAS_PDF=' + repr('pdf' in sections))\n",
    )
    assert "HAS_TASK=True" in text, "支柱①命令 task 不见了\n" + text[-1200:]
    assert "HAS_PLUGINS=True" in text, "支柱②命令 plugins 不见了\n" + text[-1200:]
    # pdf 命令**仍应存在**（只是调用时才提示装组件），否则用户以为功能被删了
    assert "HAS_PDF=True" in text, (
        "pdf 命令消失了——应该保留命令、调用时提示安装组件，"
        "而不是让用户以为功能被删了\n" + text[-1200:]
    )


def test_no_silent_success_without_document_subsystem(tmp_path: Path) -> None:
    """缺组件时**不得**出现"命令成功但什么都没做"。"""
    text = _run_isolated(
        tmp_path,
        "from omnicrawler.services import document_facade\n"
        "outcomes = []\n"
        "for name in ('pdf_main', 'convert_with_preset', 'run_pdf_extraction'):\n"
        "    fn = getattr(document_facade, name)\n"
        "    try:\n"
        "        fn([]) if name == 'pdf_main' else fn()\n"
        "        outcomes.append((name, 'SILENT_SUCCESS'))\n"
        "    except Exception as exc:\n"
        "        outcomes.append((name, type(exc).__name__))\n"
        "print('OUTCOMES=' + repr(outcomes))\n",
    )
    assert "SILENT_SUCCESS" not in text, (
        "缺文档子系统时某个入口静默成功了——用户会以为功能正常但什么都没发生。\n"
        + text[-800:]
    )
