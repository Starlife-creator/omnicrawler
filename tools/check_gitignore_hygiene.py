"""Guard the public .gitignore against publishing machine-specific facts.

Why this gate exists
--------------------
The repository is public. ``.gitignore`` is tracked, so every comment in it is
readable by anyone. A comment such as "pytest-of-<local user name>/" or
"the real evidence library lives outside the repo (see <in-repo plan>.md)"
leaks the maintainer's machine layout without leaking a single rule.

Dropping the ignore rules is not an option either: the file also protects
``.env``-style secrets and autosave files, and an untracked ``.gitignore``
would simply not reach a fresh clone (silent security regression).

So the rules stay, and this gate keeps the *comments* neutral:

1. No absolute-path shapes (``X:\\...`` or ``X:/...``) - they name real disks
   and directories on somebody's machine.
2. No local user name (taken from ``getpass.getuser()``, never hard-coded).
   If the user name cannot be determined the gate fails closed: not knowing
   the name is not an excuse to skip the check.
3. Any ``*.md`` a comment refers to must exist in the repository, and a
   section pointer (``§14.8``) must come with such a reference. Otherwise the
   comment points at a document that only exists in one person's workspace,
   which is a layout leak of its own kind - and an unfollowable pointer for
   every other reader.

Plus one structural check: ``.gitignore`` itself must be tracked and must not
be matched by any ignore rule. If it ever became ignored, the whole ignore
strategy - including the secret patterns - would stop shipping.

Machine-specific ignores belong in ``.git/info/exclude`` (never tracked), not
here.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ".gitignore"

#: ``X:\`` or ``X:/`` - a Windows drive letter followed by a separator.
_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/]")
#: Bare POSIX absolute paths look too much like patterns (``/build``), but a
#: home directory reference is unambiguous.
_HOME_PATH = re.compile(r"(?:/home/|/Users/|[Cc]:/Users/)")
#: ``something.md`` inside a comment.
_MD_REFERENCE = re.compile(r"[\w.\-/\u4e00-\u9fff]+\.md")
#: A section pointer such as ``§14.8`` - only meaningful with a document name.
_SECTION_POINTER = re.compile(r"[§#]{1,2}\s?\d+(?:\.\d+)+")


@dataclass(frozen=True, slots=True)
class Finding:
    line: int
    rule: str
    detail: str

    def describe(self) -> str:
        return f"{GITIGNORE}:{self.line}: [{self.rule}] {self.detail}"


def _comments(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, comment_text)`` for every comment line."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            found.append((number, stripped.lstrip("#").strip()))
    return found


def _user_names() -> list[str]:
    """Local account names that must never appear in a public comment."""
    names: set[str] = set()
    try:
        name = getpass.getuser().strip()
    except Exception:  # noqa: BLE001 - any failure must fail closed, not crash
        name = ""
    if name:
        names.add(name)
    return sorted(names, key=len, reverse=True)


def _looks_like_absolute(detail: str) -> bool:
    without_urls = re.sub(r"https?://\S+", "", detail)
    return bool(_ABSOLUTE_PATH.search(without_urls) or _HOME_PATH.search(without_urls))


def _check_comment(
    line: int,
    detail: str,
    *,
    user_names: list[str],
    root: Path,
) -> list[Finding]:
    findings: list[Finding] = []
    if _looks_like_absolute(detail):
        findings.append(Finding(line, "absolute-path", "comment names an absolute filesystem path"))
    folded = detail.casefold()
    for name in user_names:
        if name.casefold() in folded:
            findings.append(Finding(line, "local-user-name", "comment contains the local account name"))
            break
    resolvable = 0
    for reference in _MD_REFERENCE.findall(detail):
        candidate = reference.strip("./")
        if not candidate:
            continue
        if (root / candidate).is_file():
            resolvable += 1
            continue
        findings.append(
            Finding(line, "dangling-doc-reference", f"comment references {candidate!r}, which is not in the repo")
        )
    if resolvable == 0 and _SECTION_POINTER.search(detail):
        findings.append(
            Finding(
                line,
                "unfollowable-section-pointer",
                "comment points at a document section but names no in-repo file",
            )
        )
    return findings


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return 127, f"git unavailable: {exc}"
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _check_tracked(root: Path) -> list[Finding]:
    code, output = _git(root, "ls-files", "--error-unmatch", GITIGNORE)
    if code != 0:
        return [Finding(0, "not-tracked", f"{GITIGNORE} is not tracked by git; a fresh clone would not get it")]
    if not output.strip():
        return [Finding(0, "not-tracked", f"{GITIGNORE} could not be located in the index")]
    code, _ = _git(root, "check-ignore", "-q", "--no-index", GITIGNORE)
    if code == 0:
        return [Finding(0, "self-ignored", f"{GITIGNORE} is matched by an ignore rule; the secret patterns stop shipping")]
    return []


def check(root: Path = REPO_ROOT) -> list[Finding]:
    path = root / GITIGNORE
    if not path.is_file():
        return [Finding(0, "missing", f"{GITIGNORE} is missing from {root}")]
    text = path.read_text(encoding="utf-8", errors="replace")
    comments = _comments(text)
    if not comments:
        return [Finding(0, "empty-selection", "no comment lines found; an empty selection is not a pass")]
    user_names = _user_names()
    if not user_names:
        return [Finding(0, "unknown-user", "cannot determine the local account name; failing closed")]
    findings: list[Finding] = []
    for line, detail in comments:
        findings.extend(_check_comment(line, detail, user_names=user_names, root=root))
    findings.extend(_check_tracked(root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that public .gitignore comments leak no local layout facts.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)
    findings = check(args.root)
    if args.json:
        payload = {"ok": not findings, "findings": [f.describe() for f in findings]}
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    elif findings:
        for finding in findings:
            print(f"- {finding.describe()}", file=sys.stderr)
    else:
        print("gitignore hygiene: comments carry no local paths, user names or dangling doc references")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
