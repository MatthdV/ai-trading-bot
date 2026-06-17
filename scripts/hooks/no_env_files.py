#!/usr/bin/env python3
"""Pre-commit hook that refuses any .env file (except .env.example).

Backstop in case gitleaks or .gitignore is bypassed. See CURRENT_TASK.md
session 1 and the 47a18ec leak incident for context.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allowlist — files whose basename starts with .env but are safe to commit
ALLOWED_NAMES = {".env.example", ".env.sample", ".env.template"}


def is_blocked(path: str) -> bool:
    name = Path(path).name
    if name in ALLOWED_NAMES:
        return False
    # Block .env, .env.local, .env.production, .env.vercel, etc.
    return name == ".env" or name.startswith(".env.")


def main(argv: list[str]) -> int:
    blocked = [p for p in argv if is_blocked(p)]
    if not blocked:
        return 0

    print("ERROR: pre-commit blocked the following .env file(s):", file=sys.stderr)
    for p in blocked:
        print(f"  - {p}", file=sys.stderr)
    print(
        "\nThese files often contain secrets. If this is intentional (e.g. .env.example),\n"
        "rename the file or add it to ALLOWED_NAMES in scripts/hooks/no_env_files.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
