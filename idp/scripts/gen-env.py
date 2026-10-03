#!/usr/bin/env python3
"""Create .env from .env.example with freshly generated secrets. Never overwrites."""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ("JWT_SECRET", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "IDP_BOOTSTRAP_PASSWORD")


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; not overwriting")
        return 0
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={secrets.token_urlsafe(36)}" if key in GENERATED else line)
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    print("wrote .env (mode 600)")
    print(f"first login: {values['IDP_BOOTSTRAP_EMAIL']} / {values['IDP_BOOTSTRAP_PASSWORD']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
