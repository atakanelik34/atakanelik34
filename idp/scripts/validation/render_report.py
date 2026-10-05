#!/usr/bin/env python3
"""Render validation-report.json (pytest -m validation) and stack-validation-report.json
(stack_validation.py) into one Markdown results page.

    python3 scripts/validation/render_report.py [--out docs/validation/RESULTS.md]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _cell(value: Any) -> str:
    text = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    return text.replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default=str(ROOT / "validation-report.json"))
    parser.add_argument("--stack", default=str(ROOT / "stack-validation-report.json"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "validation" / "RESULTS.md"))
    args = parser.parse_args()
    lines = ["# Validation results", ""]
    suite_path, stack_path = Path(args.suite), Path(args.stack)
    if suite_path.exists():
        suite = json.loads(suite_path.read_text())
        lines += [f"## Fault-injection suite (`pytest -m validation`) — {suite['generated_at']}", ""]
        lines += ["| Scenario | Measurements |", "|---|---|"]
        for r in suite["results"]:
            values = {k: v for k, v in r.items() if k not in ("test", "scenario")}
            lines.append(f"| {_cell(r['scenario'])} | {_cell(values)} |")
        lines.append("")
    if stack_path.exists():
        stack = json.loads(stack_path.read_text())
        lines += [f"## Live Compose stack (`stack_validation.py`) — {stack['generated_at']}", ""]
        lines += ["| Check | Result | Measurements |", "|---|---|---|"]
        for c in stack["checks"]:
            values = {k: v for k, v in c.items() if k not in ("check", "ok")}
            lines.append(f"| {_cell(c['check'])} | {'PASS' if c['ok'] else 'FAIL'} | {_cell(values)} |")
        lines.append("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
