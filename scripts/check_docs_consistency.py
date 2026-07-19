#!/usr/bin/env python3
"""Mechanical CLI ↔ docs ↔ skill consistency checker.

Checks:
 1. Every CLI command appears in README.md.
 2. `## Commands (N` count in CLAUDE.md equals the number of CLI commands.
 3. __version__ appears in README.md and CHANGELOG.md.
 4. No phantom command references (command-shaped tokens that don't exist)
    in README / CLAUDE.md / docs / bundled skill.

Parser/dispatch parity is guaranteed by construction: each command is
registered by exactly one _cmd() call in metaads/cli.py that also binds the
handler, so it is not separately checked.

Exit 0 = OK, exit 1 = findings (printed to stderr).
"""

from __future__ import annotations

import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOC_FILES = ["README.md", "CLAUDE.md", "docs/api-notes.md", "skill/INSTALL.md"] + [
    os.path.relpath(p, BASE) for p in glob.glob(os.path.join(BASE, "skill", "meta-ads", "*.md"))
]

# Tokens that look like commands; used for phantom detection in docs.
COMMAND_PREFIXES = (
    "campaign-", "adset-", "ad-", "creative-", "image-", "video-",
    "insights-", "token-", "budget-", "interest-", "geo-", "locale-",
    "custom-", "api-", "ig-",
)

# Command-shaped tokens docs mention deliberately as NOT existing (or that are
# ordinary hyphenated words, not commands).
ALLOWED_NON_COMMANDS = {
    "creative-update", "creative-edit", "ad-account", "api-call",
    "creative-editing", "ad-creator",
}

SINGLE_WORD_COMMANDS_HINT = {
    "account", "pages", "campaigns", "adsets", "ads", "creatives",
    "insights", "pulse", "activities", "pixels", "preview",
}


def read(path: str) -> str:
    with open(os.path.join(BASE, path), encoding="utf-8") as f:
        return f.read()


def cli_commands() -> list[str]:
    src = read("metaads/cli.py")
    return re.findall(r'_cmd\(sub, "([a-z0-9-]+)"', src)


def main() -> int:
    findings: list[str] = []
    commands = cli_commands()
    if not commands:
        print("FATAL: no commands discovered in metaads/cli.py", file=sys.stderr)
        return 1

    dupes = {c for c in commands if commands.count(c) > 1}
    if dupes:
        findings.append(f"duplicate command registrations: {sorted(dupes)}")

    # 1. every command in README
    readme = read("README.md")
    for cmd in commands:
        if f"`{cmd}`" not in readme and f"`{cmd} " not in readme:
            findings.append(f"README.md: command `{cmd}` missing")

    # 2. CLAUDE.md count
    claude = read("CLAUDE.md")
    m = re.search(r"## Commands \((\d+)", claude)
    if not m:
        findings.append("CLAUDE.md: '## Commands (N' heading not found")
    elif int(m.group(1)) != len(commands):
        findings.append(f"CLAUDE.md: command count {m.group(1)} != actual {len(commands)}")

    # 3. version consistency
    vm = re.search(r'__version__ = "([^"]+)"', read("metaads/__init__.py"))
    if not vm:
        findings.append("metaads/__init__.py: __version__ not found")
    else:
        version = vm.group(1)
        if version not in readme:
            findings.append(f"README.md: version {version} not mentioned")
        if f"[{version}]" not in read("CHANGELOG.md"):
            findings.append(f"CHANGELOG.md: no [{version}] entry")

    # 4. phantom commands in docs/skill
    known = set(commands) | ALLOWED_NON_COMMANDS
    for doc in DOC_FILES:
        if not os.path.exists(os.path.join(BASE, doc)):
            findings.append(f"missing doc file: {doc}")
            continue
        text = read(doc)
        for token in set(re.findall(r"`([a-z][a-z0-9-]+)`", text)):
            if token.endswith("-"):
                continue
            if token.startswith(COMMAND_PREFIXES) and "-" in token and token not in known:
                findings.append(f"{doc}: phantom command-like token `{token}`")

    if findings:
        print(f"{len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"OK: {len(commands)} commands consistent across CLI, README, CLAUDE.md, docs and skill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
