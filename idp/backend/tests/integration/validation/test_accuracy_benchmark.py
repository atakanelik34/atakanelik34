"""Phase 13: extraction accuracy, outcomes and confidence calibration data.

Runs the synthetic ground-truth fixture set (tests/fixtures/benchmark.py)
through the real pipeline in-process: probe → digitize (real Tesseract for the
scanned cases when installed) → classify → extract → enrich → validate → review
gate. It scores every machine value (`original_value`, never a human
correction) against ground truth, and writes one observation per field with its
confidence. `scripts/validation/calibrate.py` turns that file into reliability
tables and threshold recommendations.

Writes $CALIBRATION_REPORT (default: <repo>/idp/calibration-report.json).
Asserts only what must hold on any dataset: every document reaches a terminal
state and corrupted files fail cleanly. The accuracy numbers are measurements,
not pass/fail criteria.
"""

import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from idp.application.jobs import RunOutcome
from idp.domain.evaluation import Prediction, align_rows, normalized_match
from idp.providers.ocr.tesseract import TesseractOCREngine
from tests.fixtures.benchmark import fixture_set
from tests.integration.conftest import login, upload
from tests.integration.validation.conftest import job_row, make_due

HEADER = [
    "vendor_name",
    "vendor_tax_number",
    "invoice_number",
    "invoice_date",
    "due_date",
    "purchase_order_number",
    "currency",
    "subtotal",
    "tax",
    "total",
    "iban",
]
LINE_CELLS = ["description", "quantity", "unit_price", "total"]
REPORT = Path(
    os.environ.get(
        "CALIBRATION_REPORT", Path(__file__).resolve().parents[4] / "calibration-report.json"
    )
)


def _write(report: dict[str, Any]) -> None:
    REPORT.write_text(json.dumps(report, indent=1, default=str))


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _machine(view: dict[str, Any] | None) -> Any:
    if view is None or view.get("status") == "missing":
        return None
    return view.get("original_value", view.get("value"))


def _score_header(truth: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path in HEADER:
        view = fields.get(path)
        expected, predicted = truth.get(path), _machine(view)
        has_e, has_p = _present(expected), _present(predicted)
        if not has_e and not has_p:
            outcome = "true_absent"
        elif has_e and has_p:
            outcome = "correct" if normalized_match(expected, predicted) else "wrong"
        elif has_p:
            outcome = "spurious"
        else:
            outcome = "missed"
        out.append(
            {
                "path": path,
                "outcome": outcome,
                "expected": expected,  # synthetic data: safe to record
                "predicted": predicted,
                "confidence": view.get("confidence") if view else None,
                "threshold": view.get("threshold") if view else None,
                "required": bool(view and view.get("required")),
                "method": view.get("method") if view else None,
            }
        )
    return out


def _score_lines(truth_lines: list[dict[str, str]], tables: dict[str, Any]) -> dict[str, Any]:
    rows = tables.get("lines[]", [])
    predicted = [
        {
            cell: Prediction(_machine(view), float(view.get("confidence") or 0))
            for cell, view in row["cells"].items()
            if cell in LINE_CELLS and _present(_machine(view))
        }
        for row in rows
    ]
    correct = total = 0
    cells = []
    for expected, got in align_rows(truth_lines, predicted):
        for cell in LINE_CELLS:
            want = (expected or {}).get(cell)
            pred = (got or {}).get(cell)
            if want is None:
                if pred is not None:
                    cells.append(
                        {"cell": cell, "outcome": "spurious", "confidence": pred.confidence}
                    )
                continue
            total += 1
            ok = pred is not None and normalized_match(want, pred.value)
            correct += ok
            cells.append(
                {
                    "cell": cell,
                    "outcome": "correct" if ok else ("wrong" if pred else "missed"),
                    "confidence": pred.confidence if pred else None,
                }
            )
    return {
        "expected_rows": len(truth_lines),
        "predicted_rows": len(rows),
        "cells_correct": correct,
        "cells_expected": total,
        "cells": cells,
    }


async def _run(container, runner, job_id: uuid.UUID) -> list[str]:  # type: ignore[no-untyped-def]
    outcomes = []
    for _ in range(5):
        outcome = await runner.run(job_id)
        outcomes.append(outcome.value)
        if outcome is not RunOutcome.RETRY_SCHEDULED:
            break
        await make_due(container, job_id)
    return outcomes


async def test_accuracy_and_calibration_dataset(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    created = await client.post(
        "/api/v1/document-types/from-template",
        headers=headers,
        json={"template_key": "invoice", "publish": True},
    )
    assert created.status_code == 201, created.text
    have_tesseract = shutil.which("tesseract") is not None
    engine = None
    if have_tesseract:
        engine = TesseractOCREngine(binary="tesseract", languages="eng+deu", timeout_seconds=120)
        await engine.detect_version()
    runner = runner_factory(ocr_handlers(engine))

    documents = []
    for case in fixture_set():
        if case.kind == "scanned_invoice" and not have_tesseract:
            continue
        started = time.perf_counter()
        response = await upload(client, headers, case.data, filename=f"{case.name}.pdf")
        if response.status_code != 201:
            documents.append(
                {"name": case.name, "kind": case.kind, "upload_status": response.status_code}
            )
            continue
        body = response.json()
        job_id = uuid.UUID(body["job_id"])
        outcomes = await _run(container, runner, job_id)
        seconds = time.perf_counter() - started
        doc_id = body["document"]["id"]
        result = (
            await client.get(f"/api/v1/documents/{doc_id}/extraction", headers=headers)
        ).json()
        row = await job_row(container, job_id)
        entry: dict[str, Any] = {
            "name": case.name,
            "kind": case.kind,
            "variant": case.variant,
            "document_status": result["status"],
            "job_status": str(row["status"]),
            "attempts": row["attempts"],
            "error": row["last_error_code"],
            "outcomes": outcomes,
            "seconds": round(seconds, 3),
            "step_ms": result.get("metrics", {}).get("steps", {}),
            "document_type": None,
            "classification_confidence": None,
        }
        part = result["parts"][0] if result.get("parts") else None
        if part:
            entry["document_type"] = part["classification"]["document_type"]
            entry["classification_confidence"] = part["classification"]["confidence"]
            entry["review_reasons"] = sorted(
                {v["rule"] for v in part.get("validation", []) if v["outcome"] != "PASS"}
            )
        if case.truth is not None:
            fields = part["fields"] if part else {}
            tables = part["tables"] if part else {}
            entry["fields"] = _score_header(case.truth, fields)
            entry["lines"] = _score_lines(case.lines, tables)
        documents.append(entry)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "synthetic (tests/fixtures/benchmark.py fixture_set, seed 2026)",
        "ocr_engine": f"tesseract {engine.version}" if engine else "not installed: scans skipped",
        "llm": "none configured (deterministic extractors only)",
        "documents": documents,
    }
    _write(report)
    statuses = [d.get("document_status") for d in documents]
    record(
        "P13 accuracy dataset",
        documents=len(documents),
        statuses={s: statuses.count(s) for s in set(statuses)},
        report=REPORT.name,
    )
    terminal = {"COMPLETED", "WAITING_FOR_HUMAN", "FAILED", "REJECTED", "READY_FOR_ACTION"}
    assert all(d.get("document_status") in terminal for d in documents if "upload_status" not in d)
    corrupt = [d for d in documents if d["kind"] == "corrupted"]
    assert all(
        d.get("upload_status") in (415, 422) or d.get("document_status") == "FAILED"
        for d in corrupt
    ), corrupt
