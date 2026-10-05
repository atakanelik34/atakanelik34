#!/usr/bin/env python3
"""Production-like benchmark against the running Compose stack.

Uploads the synthetic ground-truth fixture set (backend/tests/fixtures/benchmark.py)
concurrently through nginx into a dedicated tenant (`bench`, invoice template
published, no actions), waits for every document, then measures:

* outcome rates: straight-through (COMPLETED), human review (WAITING_FOR_HUMAN),
  failure (FAILED / refused at upload);
* field-level, document-level and line-item accuracy against ground truth
  (machine values only), per kind (native / scanned);
* end-to-end latency per document (upload → terminal status, includes queueing)
  and pipeline processing time (sum of step durations), mean/P50/P95/P99;
* throughput (documents per minute over the processing window);
* container CPU/RAM (docker stats), queue depth (QUEUED/RUNNING jobs in
  Postgres, sampled each second);
* LLM calls and estimated cost (provider_calls).

All numbers are measured; nothing is extrapolated. Writes stack-benchmark-report.json.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_validation as sv  # noqa: E402
from tests.fixtures.benchmark import fixture_set  # noqa: E402
from tests.integration.validation.test_accuracy_benchmark import (  # noqa: E402
    _score_header,
    _score_lines,
)

API = "http://127.0.0.1:8080/api/v1"
BENCH = ("bench", "bench-owner@validation.test")
TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "WAITING_FOR_HUMAN", "READY_FOR_ACTION"}


def q(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 3)


class QueueSampler(threading.Thread):
    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(daemon=True)
        self.values = values
        self.samples: list[tuple[int, int]] = []
        self.stop = threading.Event()

    def run(self) -> None:
        sql = (
            "SET app.tenant_id='*'; SELECT count(*) FILTER (WHERE status='QUEUED' OR "
            "status='RETRY_SCHEDULED'), count(*) FILTER (WHERE status='RUNNING') FROM processing_jobs;"
        )
        while not self.stop.is_set():
            try:
                out = sv.compose(
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    self.values.get("POSTGRES_USER", "idp"),
                    "-d",
                    self.values.get("POSTGRES_DB", "idp"),
                    "-tA",
                    "-F",
                    "|",
                    input_text=sql,
                )
                line = [x for x in out.splitlines() if "|" in x][-1]
                queued, running = (int(v) for v in line.split("|"))
                self.samples.append((queued, running))
            except (subprocess.CalledProcessError, IndexError, ValueError):
                pass
            time.sleep(1)


async def main() -> int:
    values = sv.env()
    sv.compose(
        "exec",
        "-T",
        "-e",
        f"IDP_BOOTSTRAP_PASSWORD={values['IDP_BOOTSTRAP_PASSWORD']}",
        "api",
        "idp",
        "bootstrap",
        "--tenant-slug",
        BENCH[0],
        "--tenant-name",
        "Benchmark",
        "--email",
        BENCH[1],
        "--name",
        "Bench Owner",
        "--if-missing",
    )
    client = httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=100))
    r = await client.post(
        f"{API}/auth/login", json={"email": BENCH[1], "password": values["IDP_BOOTSTRAP_PASSWORD"]}
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    types = (await client.get(f"{API}/document-types", headers=h)).json()
    if not any(t["key"] == "invoice" for t in types):
        created = await client.post(
            f"{API}/document-types/from-template",
            headers=h,
            json={"template_key": "invoice", "publish": True},
        )
        created.raise_for_status()

    cases = fixture_set()
    run = datetime.now(UTC).strftime("%H%M%S")
    stats = sv.DockerStats(["api", "worker", "postgres", "redis", "frontend"])
    queue = QueueSampler(values)
    stats.start()
    queue.start()
    sent: dict[str, float] = {}

    async def send(case) -> tuple[Any, httpx.Response]:  # type: ignore[no-untyped-def]
        # Same bytes would be refused as duplicates on a re-run: append a PDF comment.
        data = case.data + f"\n%run {run}\n".encode()
        sent[case.name] = time.perf_counter()
        resp = await client.post(
            f"{API}/documents",
            headers=h,
            files={"file": (f"{case.name}.pdf", data, "application/pdf")},
        )
        return case, resp

    started = time.perf_counter()
    uploads = await asyncio.gather(*(send(c) for c in cases))
    upload_wall = time.perf_counter() - started
    pending = {
        resp.json()["document"]["id"]: case for case, resp in uploads if resp.status_code == 201
    }
    finished: dict[str, float] = {}
    statuses: dict[str, str] = {}
    deadline = time.monotonic() + 1800
    while pending.keys() - finished.keys() and time.monotonic() < deadline:
        for doc_id in list(pending.keys() - finished.keys()):
            status = (await client.get(f"{API}/documents/{doc_id}", headers=h)).json()["status"]
            if status in TERMINAL:
                finished[doc_id] = time.perf_counter()
                statuses[doc_id] = status
        await asyncio.sleep(1)
    wall = time.perf_counter() - started
    stats.stop.set()
    queue.stop.set()
    stats.join(5)
    queue.join(5)

    documents = []
    for case, resp in uploads:
        if resp.status_code != 201:
            documents.append(
                {
                    "name": case.name,
                    "kind": case.kind,
                    "document_status": f"upload {resp.status_code}",
                }
            )
            continue
        doc_id = resp.json()["document"]["id"]
        result = (await client.get(f"{API}/documents/{doc_id}/extraction", headers=h)).json()
        part = result["parts"][0] if result.get("parts") else None
        entry: dict[str, Any] = {
            "name": case.name,
            "kind": case.kind,
            "document_status": statuses.get(doc_id, "UNFINISHED"),
            "latency_s": round(finished[doc_id] - sent[case.name], 3)
            if doc_id in finished
            else None,
            "processing_ms": result.get("metrics", {}).get("duration_ms"),
            "llm_cost": result.get("metrics", {}).get("estimated_cost"),
        }
        if case.truth is not None:
            entry["fields"] = _score_header(case.truth, part["fields"] if part else {})
            entry["lines"] = _score_lines(case.lines, part["tables"] if part else {})
        documents.append(entry)

    llm = (
        sv.compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            values.get("POSTGRES_USER", "idp"),
            "-d",
            values.get("POSTGRES_DB", "idp"),
            "-tA",
            input_text="SET app.tenant_id='*'; SELECT count(*) || ' calls, cost ' || coalesce(sum(cost),0) FROM provider_calls;",
        )
        .strip()
        .splitlines()[-1]
    )
    latencies = [d["latency_s"] for d in documents if d.get("latency_s") is not None]
    processing = [d["processing_ms"] / 1000 for d in documents if d.get("processing_ms")]
    counts: dict[str, int] = {}
    for d in documents:
        counts[d["document_status"]] = counts.get(d["document_status"], 0) + 1
    n = len(documents)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": "synthetic fixture_set(seed 2026)",
        "documents": documents,
        "summary": {
            "documents": n,
            "statuses": counts,
            "straight_through_rate": round(counts.get("COMPLETED", 0) / n, 4),
            "review_rate": round(counts.get("WAITING_FOR_HUMAN", 0) / n, 4),
            "failure_rate": round(
                sum(v for k, v in counts.items() if k == "FAILED" or k.startswith("upload")) / n, 4
            ),
            "unfinished": counts.get("UNFINISHED", 0),
            "upload_wall_s": round(upload_wall, 2),
            "total_wall_s": round(wall, 2),
            "documents_per_minute": round(len(finished) / wall * 60, 1),
            "latency_s": {
                "mean": round(statistics.mean(latencies), 2) if latencies else None,
                "p50": q(latencies, 0.5),
                "p95": q(latencies, 0.95),
                "p99": q(latencies, 0.99),
            },
            "processing_s": {
                "mean": round(statistics.mean(processing), 2) if processing else None,
                "p50": q(processing, 0.5),
                "p95": q(processing, 0.95),
                "p99": q(processing, 0.99),
            },
            "queue_depth": {
                "max_queued": max((s[0] for s in queue.samples), default=None),
                "max_running": max((s[1] for s in queue.samples), default=None),
                "samples": len(queue.samples),
            },
            "containers": stats.summary(),
            "llm_calls_and_cost": llm,
        },
    }
    (ROOT / "stack-benchmark-report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps(report["summary"], indent=1, default=str))
    await client.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
