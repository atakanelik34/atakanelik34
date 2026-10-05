#!/usr/bin/env python3
"""Production validation against a running Compose stack (black-box, through nginx).

Covers the scenarios that need real containers: concurrent upload flood (2), large
PDF and the edge upload limit (3), high page counts (4), CPU/RAM of the API and
worker containers under concurrent documents (13), tenant isolation under load at
the API and at the database for the runtime role idp_app (14).

Run from idp/ with the stack up (needs httpx; use the backend venv):
    backend/.venv/bin/python scripts/validation/stack_validation.py [--uploads 100]
Writes stack-validation-report.json (and prints a summary). Exit code 1 if any
check fails. Creates a second tenant ("validation-beta") via the CLI on first run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from tests.fixtures import files  # noqa: E402
from tests.integration.validation.conftest import noise_jpeg_pdf  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "WAITING_FOR_HUMAN", "READY_FOR_ACTION"}
BETA = ("validation-beta", "beta-owner@validation.test")


def env() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def compose(
    *args: str, input_text: str | None = None, extra_env: dict[str, str] | None = None
) -> str:
    return subprocess.run(  # noqa: S603 — fixed argv
        ["docker", "compose", *args],  # noqa: S607
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **(extra_env or {})},
    ).stdout


class DockerStats(threading.Thread):
    """Samples `docker stats` for the given services until stopped."""

    def __init__(self, services: list[str]) -> None:
        super().__init__(daemon=True)
        self.services = services
        self.samples: dict[str, list[tuple[float, float, float]]] = {s: [] for s in services}
        self.stop = threading.Event()
        ids = compose("ps", "-q", *services).split()
        names = compose("ps", "--format", "{{.Service}}", *services).split()
        self.ids = dict(zip(names, ids, strict=False))

    @staticmethod
    def _mib(value: str) -> float:
        number = float("".join(c for c in value if c.isdigit() or c == "."))
        unit = value.lstrip("0123456789.").strip().lower()
        return number * {
            "b": 1 / 1024**2,
            "kib": 1 / 1024,
            "kb": 1 / 1024,
            "mib": 1,
            "mb": 1,
            "gib": 1024,
            "gb": 1024,
        }.get(unit, 1)

    def run(self) -> None:
        while not self.stop.is_set():
            out = subprocess.run(  # noqa: S603
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.ID}} {{.CPUPerc}} {{.MemUsage}}",
                    *self.ids.values(),
                ],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for line in out.splitlines():
                cid, cpu, usage = line.split(" ", 2)
                used, limit = (part.strip() for part in usage.split("/"))
                service = next(
                    s for s, i in self.ids.items() if i.startswith(cid) or cid.startswith(i[:12])
                )
                self.samples[service].append(
                    (float(cpu.rstrip("%")), self._mib(used), self._mib(limit))
                )
            time.sleep(1)

    def summary(self) -> dict[str, Any]:
        out = {}
        for service, rows in self.samples.items():
            if rows:
                out[service] = {
                    "peak_cpu_pct": round(max(r[0] for r in rows), 1),
                    "mean_cpu_pct": round(statistics.mean(r[0] for r in rows), 1),
                    "peak_mem_mib": round(max(r[1] for r in rows)),
                    "mem_limit_mib": round(rows[0][2]),
                    "peak_mem_pct_of_limit": round(100 * max(r[1] for r in rows) / rows[0][2], 1),
                    "samples": len(rows),
                }
        return out


class Stack:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"
        self.client = httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=200))

    async def login(self, email: str, password: str) -> dict[str, str]:
        r = await self.client.post(
            f"{self.base}/auth/login", json={"email": email, "password": password}
        )
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def upload(
        self, headers: dict[str, str], data: bytes, name: str = "doc.pdf"
    ) -> tuple[int | str, float, dict]:
        started = time.perf_counter()
        try:
            r = await self.client.post(
                f"{self.base}/documents",
                headers=headers,
                files={"file": (name, data, "application/pdf")},
            )
            body = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            return r.status_code, time.perf_counter() - started, body
        except httpx.HTTPError as exc:
            return type(exc).__name__, time.perf_counter() - started, {}

    async def wait_terminal(
        self, headers: dict[str, str], ids: list[str], timeout: float
    ) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        statuses: dict[str, str] = {}
        while time.monotonic() < deadline:
            pending = [i for i in ids if statuses.get(i) not in TERMINAL]
            if not pending:
                break
            for doc_id in pending:
                r = await self.client.get(f"{self.base}/documents/{doc_id}", headers=headers)
                statuses[doc_id] = r.json().get("status", str(r.status_code))
            await asyncio.sleep(2)
        return statuses


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3) if ordered else 0.0


def ensure_beta(values: dict[str, str]) -> str:
    password = values["IDP_BOOTSTRAP_PASSWORD"]
    try:
        compose(
            "exec",
            "-T",
            "-e",
            f"IDP_BOOTSTRAP_PASSWORD={password}",
            "api",
            "idp",
            "bootstrap",
            "--tenant-slug",
            BETA[0],
            "--tenant-name",
            "Validation Beta",
            "--email",
            BETA[1],
            "--name",
            "Beta Owner",
            "--if-missing",
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"could not create the beta tenant: {exc.stderr}") from exc
    return password


def db_rls_check(values: dict[str, str], alpha_slug: str) -> dict[str, Any]:
    """As idp_app (the runtime role): no context → nothing; tenant context → own rows only."""
    user = values.get("POSTGRES_APP_USER", "idp_app")
    # tenants is itself under RLS: read the id in system context ("*"), then switch.
    sql = f"""
SELECT 'no_context', count(*) FROM documents;
SELECT set_config('app.tenant_id', '*', false) \\gset
SELECT id AS alpha FROM tenants WHERE slug = '{alpha_slug}' \\gset
SELECT set_config('app.tenant_id', :'alpha', false) \\gset
SELECT 'alpha_context_foreign_rows', count(*) FROM documents WHERE tenant_id <> :'alpha';
SELECT 'alpha_context_rows', count(*) FROM documents;
"""
    out = compose(
        "exec",
        "-T",
        "-e",
        f"PGPASSWORD={values['POSTGRES_APP_PASSWORD']}",
        "postgres",
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        user,
        "-d",
        values.get("POSTGRES_DB", "idp"),
        "-tA",
        "-F",
        "=",
        input_text=sql,
    )
    result: dict[str, Any] = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in ("no_context", "alpha_context_foreign_rows", "alpha_context_rows"):
                result[key] = int(value)
    try:
        compose(
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={values['POSTGRES_APP_PASSWORD']}",
            "postgres",
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            user,
            "-d",
            values.get("POSTGRES_DB", "idp"),
            "-c",
            "UPDATE audit_logs SET action = 'tampered'",
        )
        result["audit_update_refused"] = False
    except subprocess.CalledProcessError:
        result["audit_update_refused"] = True
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--uploads", type=int, default=100)
    parser.add_argument("--pages", type=int, default=500)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    values = env()
    stack = Stack(args.base_url)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **data: Any) -> None:
        checks.append({"check": name, "ok": ok, **data})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {json.dumps(data, default=str)[:300]}")

    alpha = await stack.login(values["IDP_BOOTSTRAP_EMAIL"], values["IDP_BOOTSTRAP_PASSWORD"])
    beta = await stack.login(BETA[1], ensure_beta(values))
    run_id = datetime.now(UTC).strftime("%H%M%S")
    stats = DockerStats(["api", "worker", "postgres", "frontend"])
    stats.start()

    # 2 — concurrent upload flood (alpha) while beta uploads too (14)
    started = time.perf_counter()
    results = await asyncio.gather(
        *(
            stack.upload(alpha, files.make_pdf([f"Invoice {run_id}-{i}\nTotal due {i}.00 EUR"]))
            for i in range(args.uploads)
        ),
        *(
            stack.upload(beta, files.make_pdf([f"Beta {run_id}-{i}"]))
            for i in range(args.uploads // 4)
        ),
    )
    wall = time.perf_counter() - started
    alpha_results, beta_results = results[: args.uploads], results[args.uploads :]
    latencies = [r[1] for r in results]
    statuses = [r[0] for r in results]
    check(
        "2 concurrent upload flood",
        all(s == 201 for s in statuses),
        uploads=len(results),
        non_201=sorted({str(s) for s in statuses if s != 201}),
        uploads_per_s=round(len(results) / wall, 1),
        p50_s=pct(latencies, 0.5),
        p95_s=pct(latencies, 0.95),
        max_s=round(max(latencies), 2),
    )
    alpha_ids = [r[2]["document"]["id"] for r in alpha_results if r[0] == 201]
    beta_ids = [r[2]["document"]["id"] for r in beta_results if r[0] == 201]

    # 14 — isolation under load: cross-tenant reads and listings interleaved
    cross = await asyncio.gather(
        *(stack.client.get(f"{stack.base}/documents/{d}", headers=beta) for d in alpha_ids[:50]),
        *(stack.client.get(f"{stack.base}/documents/{d}", headers=alpha) for d in beta_ids[:25]),
    )
    listings = await asyncio.gather(
        *(stack.client.get(f"{stack.base}/documents?limit=100", headers=beta) for _ in range(20))
    )
    leaked = [d["id"] for r in listings for d in r.json()["items"] if d["id"] in set(alpha_ids)]
    check(
        "14 tenant isolation under load (API)",
        all(r.status_code == 404 for r in cross) and not leaked,
        cross_reads=len(cross),
        non_404=sorted({r.status_code for r in cross if r.status_code != 404}),
        list_leaks=len(leaked),
    )
    db = db_rls_check(values, "demo")
    check(
        "14 tenant isolation (database, runtime role idp_app)",
        db.get("no_context") == 0
        and db.get("alpha_context_foreign_rows") == 0
        and db.get("audit_update_refused") is True,
        **db,
    )

    # 3 — large PDF through nginx, and over the edge limit
    large = noise_jpeg_pdf(pages=7)
    status, seconds, body = await stack.upload(alpha, large, "large.pdf")
    check(
        "3 large PDF accepted",
        status == 201,
        size_mb=round(len(large) / 1e6, 1),
        upload_s=round(seconds, 2),
    )
    if status == 201:
        alpha_ids.append(body["document"]["id"])
    over = noise_jpeg_pdf(pages=13)
    status, _, _ = await stack.upload(alpha, over, "over.pdf")
    check(
        "3 over-limit PDF refused at the edge",
        status == 413,
        size_mb=round(len(over) / 1e6, 1),
        status=status,
    )

    # 4 — high page count
    high = files.make_pdf(
        [f"Statement {run_id} page {n}\nBalance {n}.00" for n in range(args.pages)]
    )
    status, _, body = await stack.upload(alpha, high, "statement.pdf")
    high_id = body.get("document", {}).get("id")

    # 13 — everything above is now being processed concurrently by the worker
    processing_started = time.perf_counter()
    final = await stack.wait_terminal(
        alpha, [*alpha_ids, *(x for x in [high_id] if x)], args.timeout
    )
    processing_wall = time.perf_counter() - processing_started
    stats.stop.set()
    stats.join(timeout=5)
    counts: dict[str, int] = {}
    for s in final.values():
        counts[s] = counts.get(s, 0) + 1
    unfinished = [d for d, s in final.items() if s not in TERMINAL]
    check(
        "4 high page count processed",
        bool(high_id) and final.get(high_id) in TERMINAL - {"FAILED"},
        pages=args.pages,
        status=final.get(high_id),
    )
    resources = stats.summary()
    worker = resources.get("worker", {})
    check(
        "13 CPU/RAM under concurrent documents",
        not unfinished and worker.get("peak_mem_pct_of_limit", 100) < 90,
        documents=len(final),
        statuses=counts,
        unfinished=len(unfinished),
        processing_wall_s=round(processing_wall, 1),
        docs_per_min=round(len(final) / processing_wall * 60, 1),
        containers=resources,
    )
    jobs = (
        await stack.client.get(
            f"{stack.base}/jobs?status=DEAD_LETTERED&status=FAILED&limit=200", headers=alpha
        )
    ).json()
    check("no failed or dead-lettered jobs", not jobs, failed_or_dead=len(jobs))

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "checks": checks,
    }
    (ROOT / "stack-validation-report.json").write_text(json.dumps(report, indent=2, default=str))
    await stack.client.aclose()
    failed = [c["check"] for c in checks if not c["ok"]]
    print(
        f"\n{len(checks) - len(failed)}/{len(checks)} checks passed"
        + (f"; failed: {failed}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
