#!/usr/bin/env python3
"""Confidence calibration and threshold recommendation (reproducible procedure).

Input: calibration-report.json written by
backend/tests/integration/validation/test_accuracy_benchmark.py — one
observation per (document, field) with the machine value's confidence and
whether it matched ground truth.

Output (stdout + optional --out markdown):
* document outcomes: straight-through (COMPLETED without review), review,
  failure, and *silent errors*: documents completed automatically with at least
  one wrong field — the number that matters most for go-live;
* field- and document-level accuracy;
* a reliability table (accuracy per confidence band) and the expected
  calibration error;
* the current thresholds' behaviour: wrong values at/above threshold (would
  skip review) and correct values below it (needless review);
* recommended bands, each with a Wilson 95 % interval and its sample size:
    auto-accept  ≥ the lowest confidence c where accuracy of {conf ≥ c} has a
                 Wilson lower bound ≥ --accept-lb (default 0.98)
    escalate     < the highest confidence e where accuracy of {conf < e} is
                 below --escalate-acc (default 0.5): more likely wrong than right
    review       in between.
  With too few observations or errors the bounds are wide and the procedure
  says so: thresholds are only as good as the data they were fitted on.

Usage:
    python3 scripts/validation/calibrate.py calibration-report.json [--out docs/validation/CALIBRATION.md]
"""

from __future__ import annotations

import argparse
import json
import math
from itertools import pairwise
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MIN_ERRORS = 30  # below this, an auto-accept threshold cannot be validated
BANDS = [0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0001]


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(x: float) -> str:
    return f"{100 * x:.1f} %"


def observations(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Header fields where the machine produced a value (the only ones confidence can gate)."""
    out = []
    for doc in report["documents"]:
        for f in doc.get("fields", []):
            if f["outcome"] in ("correct", "wrong", "spurious") and f["confidence"] is not None:
                out.append(
                    {
                        **f,
                        "doc": doc["name"],
                        "kind": doc["kind"],
                        "ok": f["outcome"] == "correct",
                    }
                )
    return out


def recommend(obs: list[dict[str, Any]], accept_lb: float, escalate_acc: float) -> dict[str, Any]:
    confs = sorted({round(o["confidence"], 3) for o in obs})
    accept = None
    for c in confs:
        above = [o for o in obs if o["confidence"] >= c]
        lo, _ = wilson(sum(o["ok"] for o in above), len(above))
        if lo >= accept_lb:
            accept = {"threshold": c, "n": len(above), "accuracy_wilson_low": round(lo, 4)}
            break
    escalate = None
    for c in reversed(confs):
        below = [o for o in obs if o["confidence"] < c]
        if below and sum(o["ok"] for o in below) / len(below) < escalate_acc:
            escalate = {
                "threshold": c,
                "n": len(below),
                "accuracy": round(sum(o["ok"] for o in below) / len(below), 4),
            }
            break
    return {"auto_accept": accept, "escalate_below": escalate}


def analyse(report: dict[str, Any], accept_lb: float, escalate_acc: float) -> dict[str, Any]:
    docs = report["documents"]
    invoices = [d for d in docs if d["kind"] in ("native_invoice", "scanned_invoice")]
    statuses = Counter(d.get("document_status", f"upload {d.get('upload_status')}") for d in docs)

    def doc_correct(d: dict[str, Any]) -> bool:
        header = all(f["outcome"] in ("correct", "true_absent") for f in d.get("fields", []))
        lines = d.get("lines", {})
        return header and lines.get("cells_correct") == lines.get("cells_expected")

    def has(d: dict[str, Any], *outcomes: str) -> bool:
        return any(f["outcome"] in outcomes for f in d.get("fields", []))

    completed = [d for d in invoices if d.get("document_status") == "COMPLETED"]
    # A wrong or invented value accepted without a human: the go-live critical number.
    silent = [d["name"] for d in completed if has(d, "wrong", "spurious")]
    # Completed although an (optional) value present on the page was not extracted.
    incomplete = [
        d["name"] for d in completed if not has(d, "wrong", "spurious") and has(d, "missed")
    ]
    reviewed_errors = [
        d["name"]
        for d in invoices
        if d.get("document_status") == "WAITING_FOR_HUMAN" and has(d, "wrong", "spurious")
    ]
    field_conf: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"ok": [], "bad": []})
    for d in invoices:
        for f in d.get("fields", []):
            if f["outcome"] in ("correct", "wrong", "spurious") and f["confidence"] is not None:
                field_conf[f["path"]]["ok" if f["outcome"] == "correct" else "bad"].append(
                    f["confidence"]
                )
    per_field: dict[str, Counter[str]] = defaultdict(Counter)
    for d in invoices:
        for f in d.get("fields", []):
            per_field[f["path"]][f["outcome"]] += 1
    obs = observations(report)
    bands = []
    ece = 0.0
    for lo, hi in pairwise(BANDS):
        b = [o for o in obs if lo <= o["confidence"] < hi]
        if b:
            acc = sum(o["ok"] for o in b) / len(b)
            mean_conf = sum(o["confidence"] for o in b) / len(b)
            ece += len(b) / len(obs) * abs(acc - mean_conf)
            bands.append(
                {
                    "band": f"[{lo:.2f}, {min(hi, 1.0):.2f}{']' if hi > 1 else ')'}",
                    "n": len(b),
                    "accuracy": round(acc, 4),
                    "wilson_95": [round(x, 3) for x in wilson(sum(o["ok"] for o in b), len(b))],
                    "mean_confidence": round(mean_conf, 3),
                }
            )
    at_current = {
        "wrong_at_or_above_threshold": sum(
            1 for o in obs if not o["ok"] and o["confidence"] >= (o["threshold"] or 0.8)
        ),
        "correct_below_threshold": sum(
            1 for o in obs if o["ok"] and o["confidence"] < (o["threshold"] or 0.8)
        ),
    }
    errors = sum(1 for o in obs if not o["ok"])
    by_kind = {}
    for kind in ("native_invoice", "scanned_invoice"):
        sel = [d for d in invoices if d["kind"] == kind]
        fields = [f for d in sel for f in d.get("fields", [])]
        scored = [f for f in fields if f["outcome"] != "true_absent"]
        by_kind[kind] = {
            "documents": len(sel),
            "field_accuracy": round(sum(f["outcome"] == "correct" for f in scored) / len(scored), 4)
            if scored
            else None,
            "document_accuracy": round(sum(doc_correct(d) for d in sel) / len(sel), 4)
            if sel
            else None,
            "line_cell_accuracy": round(
                sum(d["lines"]["cells_correct"] for d in sel)
                / max(1, sum(d["lines"]["cells_expected"] for d in sel)),
                4,
            )
            if sel
            else None,
        }
    seconds = sorted(d["seconds"] for d in docs if "seconds" in d)

    def q(p: float) -> float | None:
        return round(seconds[min(len(seconds) - 1, int(p * len(seconds)))], 3) if seconds else None

    return {
        "dataset": report["dataset"],
        "ocr_engine": report["ocr_engine"],
        "llm": report["llm"],
        "documents": len(docs),
        "statuses": dict(statuses),
        "straight_through_rate": round(statuses.get("COMPLETED", 0) / len(docs), 4),
        "review_rate": round(statuses.get("WAITING_FOR_HUMAN", 0) / len(docs), 4),
        "failure_rate": round(statuses.get("FAILED", 0) / len(docs), 4),
        "silent_errors": silent,
        "completed_with_missing_values": incomplete,
        "wrong_values_caught_by_review": len(reviewed_errors),
        "field_confidence": {
            p: {
                "correct_n": len(v["ok"]),
                "correct_conf_range": [min(v["ok"]), max(v["ok"])] if v["ok"] else None,
                "wrong_n": len(v["bad"]),
                "wrong_conf_range": [min(v["bad"]), max(v["bad"])] if v["bad"] else None,
            }
            for p, v in sorted(field_conf.items())
        },
        "accuracy_by_kind": by_kind,
        "per_field": {p: dict(c) for p, c in sorted(per_field.items())},
        "observations": len(obs),
        "errors_observed": errors,
        "reliability": bands,
        "ece": round(ece, 4),
        "current_thresholds": at_current,
        "recommendation": recommend(obs, accept_lb, escalate_acc),
        "sufficient": errors >= MIN_ERRORS,
        "latency_s": {
            "mean": round(sum(seconds) / len(seconds), 3),
            "p50": q(0.5),
            "p95": q(0.95),
            "p99": q(0.99),
        }
        if seconds
        else None,
    }


def markdown(a: dict[str, Any]) -> str:
    lines = [
        "# Confidence calibration (generated)",
        "",
        f"Dataset: {a['dataset']} · OCR: {a['ocr_engine']} · LLM: {a['llm']}",
        "",
        f"Documents {a['documents']}: {a['statuses']}. Straight-through {pct(a['straight_through_rate'])}, "
        f"review {pct(a['review_rate'])}, failed {pct(a['failure_rate'])}. "
        f"**Silent errors** (completed automatically with a wrong or invented value): "
        f"{len(a['silent_errors'])}"
        + (f" ({', '.join(a['silent_errors'])})" if a["silent_errors"] else "")
        + f". Completed with a missed value: {len(a['completed_with_missing_values'])}. "
        f"Documents with a wrong value that went to review: {a['wrong_values_caught_by_review']}.",
        "",
        "| Kind | Docs | Field accuracy | Document accuracy | Line-cell accuracy |",
        "|---|---|---|---|---|",
    ]
    for kind, k in a["accuracy_by_kind"].items():
        lines.append(
            f"| {kind} | {k['documents']} | {k['field_accuracy']} | {k['document_accuracy']} | {k['line_cell_accuracy']} |"
        )
    lines += ["", "| Field | Outcomes |", "|---|---|"]
    lines += [f"| {p} | {c} |" for p, c in a["per_field"].items()]
    lines += [
        "",
        "| Field | Correct (n, confidence range) | Wrong (n, confidence range) |",
        "|---|---|---|",
    ]
    lines += [
        f"| {p} | {v['correct_n']} {v['correct_conf_range']} | {v['wrong_n']} {v['wrong_conf_range']} |"
        for p, v in a["field_confidence"].items()
    ]
    lines += [
        "",
        (
            f"Field observations with a machine value: {a['observations']}, of which wrong: "
            f"{a['errors_observed']}. Expected calibration error: {a['ece']}."
        ),
        "",
        "| Confidence band | n | Accuracy | Wilson 95 % | Mean confidence |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {b['band']} | {b['n']} | {b['accuracy']} | {b['wilson_95']} | {b['mean_confidence']} |"
        for b in a["reliability"]
    ]
    lines += [
        "",
        (
            "Current thresholds: wrong values at/above threshold (skip review): "
            f"{a['current_thresholds']['wrong_at_or_above_threshold']}; correct values below "
            f"threshold (needless review): {a['current_thresholds']['correct_below_threshold']}."
        ),
        "",
        f"Recommendation: {json.dumps(a['recommendation'])}",
        "",
        "Sufficient to validate an auto-accept threshold: "
        + ("yes" if a["sufficient"] else f"**no** (fewer than {MIN_ERRORS} errors observed)"),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--accept-lb", type=float, default=0.98)
    parser.add_argument("--escalate-acc", type=float, default=0.5)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    analysis = analyse(json.loads(args.report.read_text()), args.accept_lb, args.escalate_acc)
    text = markdown(analysis)
    print(text)
    if args.json:
        args.json.write_text(json.dumps(analysis, indent=1))
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
