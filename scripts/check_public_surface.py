"""Reject private workstation or coordination language from the public source tree."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".mts",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_NAMES = {"pnpm-lock.yaml", "uv.lock"}

_LITERAL_RULES = {
    "private host alias": "h" + "k" + "3",
    "private coordination role": "sub" + "agent",
    "private model name": "GPT" + "-5.6",
    "private model vendor name": "Deep" + "Seek",
    "private model example": "Claude" + " Opus",
    "private Chinese coordination term": "总指" + "挥",
    "private Chinese task-brief term": "任务" + "书",
    "private Chinese approval narrative": "用户" + "审批",
    "private secret directory": "server" + "-secrets",
}
_REGEX_RULES = {
    "Windows absolute path": re.compile(r"\b[A-Za-z]:[\\/]"),
    "standalone private user name": re.compile(r"(?i)(?<![A-Za-z])" + "Di" + r"ng(?![A-Za-z])"),
    "credential-shaped token": re.compile(
        r"(?i)(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,}"
    ),
    "specific tenant service": re.compile(r"arena-hero-" + r"t[1-4]\b", re.IGNORECASE),
}


def candidate_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in completed.stdout.splitlines() if line]


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        if (
            not path.is_file()
            or path.name in SKIP_NAMES
            or path.suffix.lower() not in TEXT_SUFFIXES
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for label, value in _LITERAL_RULES.items():
            if value.casefold() in text.casefold():
                findings.append(f"{relative}: contains {label}")
        for label, pattern in _REGEX_RULES.items():
            if pattern.search(text):
                findings.append(f"{relative}: contains {label}")

    if findings:
        print("Public-surface scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public-surface scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
