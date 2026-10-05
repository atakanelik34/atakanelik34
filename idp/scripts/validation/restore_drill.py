#!/usr/bin/env python3
"""Backup/restore drill (F6): back up a seeded stack, destroy it, restore into a fresh one.

DESTRUCTIVE for the Compose project it runs against: it ends with
`docker compose down -v` (all volumes) and rebuilds from the backup. Run it only
on a disposable validation stack. It honours COMPOSE_FILE / COMPOSE_PROFILES.

    backend/.venv/bin/python scripts/validation/restore_drill.py --i-understand-this-destroys-the-stack

Steps, all with the repository's own scripts:
 1. seed through the API: a second tenant, processing policy, a connection, a
    document type with an approval-gated action, an API key; a document routed
    to human review (claimed, corrected, approved, action approved and
    executed); a straight-through document whose action is approved and
    executed; and, with the worker stopped, a document whose job is still
    QUEUED when the backup is taken;
 2. fingerprint: row count and an MD5 over the ordered row text of every table,
    plus a SHA-256 of every object in the bucket;
 3. `scripts/backup.sh`;
 4. disaster: `docker compose down -v` (database, object store, Redis gone);
 5. fresh environment: `docker compose up -d` (new volumes, migrations,
    bootstrap), stop api/worker, `scripts/restore.sh`, start everything;
 6. verify: identical fingerprints; logins of both tenants; documents, review
    tasks, actions and audit entries readable; page images served from the
    restored bucket; the seeded API key still authenticates; the job that was
    QUEUED at backup time completes (Redis was not backed up: the sweeper
    re-dispatches it from Postgres); a new upload processes end to end; the
    runtime role still cannot update audit_logs or read across tenants.
Writes restore-drill-report.json.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_validation as sv  # noqa: E402
from tests.fixtures import files  # noqa: E402

API = "http://127.0.0.1:8080/api/v1"
TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "WAITING_FOR_HUMAN", "READY_FOR_ACTION"}


def sh(*args: str, input_text: str | None = None) -> str:
    return subprocess.run(  # noqa: S603
        list(args), cwd=ROOT, input=input_text, capture_output=True, text=True, check=True
    ).stdout


def psql_owner(values: dict[str, str], sql: str) -> str:
    return sv.compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        values.get("POSTGRES_USER", "idp"),
        "-d",
        values.get("POSTGRES_DB", "idp"),
        "-tA",
        "-F",
        "|",
        input_text=sql,
    )


def db_fingerprint(values: dict[str, str]) -> dict[str, list[Any]]:
    tables = psql_owner(
        values,
        "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1;",
    ).split()
    sql = "SET app.tenant_id = '*';\n" + "\n".join(
        f"SELECT '{t}', count(*), coalesce(md5(string_agg(x::text, '|' ORDER BY x::text)), '')"
        f' FROM "{t}" x;'
        for t in tables
    )
    out = {}
    for line in psql_owner(values, sql).splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            out[parts[0]] = [int(parts[1]), parts[2]]
    return out


def object_fingerprint() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="idp-drill-") as tmp:
        sh(
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-u",
            "0:0",
            "-v",
            f"{tmp}:/snap",
            "--entrypoint",
            "/bin/sh",
            "minio-init",
            "-c",
            'mc alias set src http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null'
            ' && mc mirror --quiet "src/$BUCKET" /snap && chmod -R a+rX /snap',
        )
        root = Path(tmp)
        return {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }


class Api:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=120)

    async def login(self, email: str, password: str) -> dict[str, str]:
        r = await self.client.post(f"{API}/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def get(self, path: str, h: dict[str, str]) -> Any:
        r = await self.client.get(f"{API}{path}", headers=h)
        r.raise_for_status()
        return r.json()

    async def post(self, path: str, h: dict[str, str], body: Any = None) -> httpx.Response:
        return await self.client.post(f"{API}{path}", headers=h, json=body or {})

    async def upload(self, h: dict[str, str], data: bytes, name: str) -> dict[str, Any]:
        r = await self.client.post(
            f"{API}/documents", headers=h, files={"file": (name, data, "application/pdf")}
        )
        r.raise_for_status()
        return r.json()

    async def wait(
        self, h: dict[str, str], doc_id: str, want: set[str], timeout: float = 600
    ) -> str:
        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            status = (await self.get(f"/documents/{doc_id}", h))["status"]
            if status in want:
                return status
            await asyncio.sleep(2)
        raise SystemExit(f"document {doc_id} stuck in {status}, wanted {want}")


async def configure(api: Api, h: dict[str, str]) -> None:
    await api.client.put(
        f"{API}/processing-policy",
        headers=h,
        json={"mode": "LOCAL_ONLY", "allow_llm": True, "allow_mock_providers": True},
    )
    await api.post(
        "/connections", h, {"key": "erp", "name": "ERP (mock)", "kind": "mock_erp", "config": {}}
    )
    types = await api.get("/document-types", h)
    if any(t.get("key") == "invoice" for t in types):
        return
    created = (
        await api.post(
            "/document-types/from-template", h, {"template_key": "invoice", "publish": False}
        )
    ).json()
    type_id = created["id"]
    version = (await api.get(f"/document-types/{type_id}", h))["versions"][0]["version"]
    definition = (await api.get(f"/document-types/{type_id}/schemas/{version}", h))["definition"]
    definition["actions"] = [
        {
            "name": "post_invoice",
            "connection": "erp",
            "requires_approval": True,
            "payload": {"invoice_number": "field:invoice_number", "total": "field:total"},
        }
    ]
    r = await api.client.put(
        f"{API}/document-types/{type_id}/draft", headers=h, json={"definition": definition}
    )
    r.raise_for_status()
    (await api.post(f"/document-types/{type_id}/publish", h)).raise_for_status()


async def seed(api: Api, values: dict[str, str], tag: str) -> dict[str, Any]:
    admin = await api.login(values["IDP_BOOTSTRAP_EMAIL"], values["IDP_BOOTSTRAP_PASSWORD"])
    beta = await api.login(sv.BETA[1], sv.ensure_beta(values))
    await configure(api, admin)
    key = (await api.post("/api-keys", admin, {"name": f"drill-{tag}"})).json()

    # straight-through document: action approval, then executed by the mock ERP
    clean = [list(r) for r in files.INVOICE_ROWS]
    clean[0] = [(72, "Supplier: ACME Industrial Supplies GmbH")]
    clean[1] = [(72, f"Hauptstrasse {tag}, 10115 Berlin")]
    stp = await api.upload(admin, files.make_text_pdf(clean), f"stp-{tag}.pdf")
    # a document needing review (low-confidence supplier)
    rows = [list(r) for r in files.INVOICE_ROWS]
    rows[1] = [(72, f"Hauptstrasse {tag}, 10115 Berlin")]
    rev = await api.upload(admin, files.invoice_pdf(rows), f"review-{tag}.pdf")
    await api.upload(beta, files.make_pdf([f"Beta tenant document {tag}"]), f"beta-{tag}.pdf")

    stp_id, rev_id = stp["document"]["id"], rev["document"]["id"]
    await api.wait(admin, stp_id, {"READY_FOR_ACTION"})
    (
        await api.post(f"/documents/{stp_id}/actions/approve", admin, {"note": "drill"})
    ).raise_for_status()
    await api.wait(admin, rev_id, {"WAITING_FOR_HUMAN"})
    # The list is oldest-first without a document filter: look the task up directly.
    task_id = psql_owner(
        values,
        f"SET app.tenant_id = '*'; SELECT id FROM review_tasks WHERE document_id = '{rev_id}';",
    ).split()[-1]
    task = {"id": task_id}
    detail = await api.get(f"/reviews/{task['id']}", admin)
    (await api.post(f"/reviews/{task['id']}/claim", admin)).raise_for_status()
    vendor = detail["parts"][0]["fields"]["vendor_name"]
    (
        await api.post(
            f"/reviews/{task['id']}/fields/{vendor['id']}",
            admin,
            {"action": "edit", "value": "ACME Industrial Supplies GmbH", "reason": "drill"},
        )
    ).raise_for_status()
    (await api.post(f"/reviews/{task['id']}/approve", admin, {"note": "drill"})).raise_for_status()
    await api.wait(admin, rev_id, {"READY_FOR_ACTION", "COMPLETED"})
    if (await api.get(f"/documents/{rev_id}", admin))["status"] == "READY_FOR_ACTION":
        (await api.post(f"/documents/{rev_id}/actions/approve", admin, {})).raise_for_status()
    for doc in (stp_id, rev_id):
        await api.wait(admin, doc, {"COMPLETED"})

    # a job still QUEUED when the backup is taken (worker stopped)
    sv.compose("stop", "worker")
    queued = await api.upload(admin, files.native_pdf(3) + f"%{tag}".encode(), f"queued-{tag}.pdf")
    return {
        "admin": admin,
        "beta": beta,
        "api_key": key,
        "stp": stp_id,
        "review": rev_id,
        "review_task": task["id"],
        "queued": queued["document"]["id"],
    }


async def verify(api: Api, values: dict[str, str], seeded: dict[str, Any], check) -> None:  # type: ignore[no-untyped-def]
    admin = await api.login(values["IDP_BOOTSTRAP_EMAIL"], values["IDP_BOOTSTRAP_PASSWORD"])
    beta = await api.login(sv.BETA[1], values["IDP_BOOTSTRAP_PASSWORD"])
    check("logins of both tenants work (password hashes restored)", True)
    for doc, label in ((seeded["stp"], "straight-through"), (seeded["review"], "reviewed")):
        d = await api.get(f"/documents/{doc}", admin)
        runs = await api.get(f"/documents/{doc}/actions", admin)
        check(
            f"{label} document restored with its executed action",
            d["status"] == "COMPLETED"
            and runs
            and runs[0]["status"] == "succeeded"
            and bool(runs[0]["external_reference"]),
            status=d["status"],
            action=runs[0]["status"] if runs else None,
        )
        image = await api.client.get(f"{API}/documents/{doc}/pages/1/image/content", headers=admin)
        check(
            f"{label} page image served from restored bucket",
            image.status_code == 200 and len(image.content) > 1000,
            bytes=len(image.content),
        )
    task = await api.get(f"/reviews/{seeded['review_task']}", admin)
    vendor = task["parts"][0]["fields"]["vendor_name"]
    check(
        "review decision and correction restored",
        vendor["status"] == "corrected" and task["task"]["status"] == "approved",
        field=vendor["status"],
        task=task["task"]["status"],
    )
    audit = (await api.get("/audit-logs?limit=100", admin))["items"]
    check("audit trail restored", len(audit) > 10, entries_page=len(audit))
    machine = {"Authorization": f"Bearer {seeded['api_key']['token']}"}
    r = await api.client.get(f"{API}/documents?limit=5", headers=machine)
    check("seeded API key still authenticates", r.status_code == 200, status=r.status_code)
    beta_docs = (await api.get("/documents?limit=100", beta))["items"]
    check(
        "second tenant's data restored and isolated",
        len(beta_docs) >= 1
        and all(d["id"] not in (seeded["stp"], seeded["review"]) for d in beta_docs),
        beta_documents=len(beta_docs),
    )
    status = await api.wait(admin, seeded["queued"], TERMINAL, timeout=300)
    check(
        "job QUEUED at backup time completes after restore (sweeper)",
        status in TERMINAL - {"FAILED"},
        status=status,
    )
    fresh = await api.upload(admin, files.make_pdf([f"post-restore {time.time()}"]), "post.pdf")
    status = await api.wait(admin, fresh["document"]["id"], TERMINAL, timeout=300)
    check(
        "new upload processes end to end after restore",
        status in TERMINAL - {"FAILED"},
        status=status,
    )
    rls = sv.db_rls_check(values, "demo")
    check(
        "runtime role: RLS and append-only audit still enforced",
        rls.get("no_context") == 0
        and rls.get("alpha_context_foreign_rows") == 0
        and rls.get("audit_update_refused") is True,
        **rls,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--i-understand-this-destroys-the-stack", action="store_true", dest="ok")
    parser.add_argument("--backup-dir", default=None)
    args = parser.parse_args()
    if not args.ok:
        raise SystemExit("refusing: pass --i-understand-this-destroys-the-stack")
    values = sv.env()
    tag = datetime.now(UTC).strftime("%H%M%S")
    checks: list[dict[str, Any]] = []
    timings: dict[str, float] = {}

    def check(name: str, ok: bool, **data: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), **data})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {json.dumps(data, default=str)[:240]}")

    api = Api()
    seeded = await seed(api, values, tag)
    before_db = db_fingerprint(values)
    before_obj = object_fingerprint()
    print(
        f"seeded: {sum(v[0] for v in before_db.values())} rows in {len(before_db)} tables, {len(before_obj)} objects"
    )

    backup_dir = Path(args.backup_dir or ROOT / "backups" / f"drill-{tag}")
    started = time.perf_counter()
    sh("scripts/backup.sh", str(backup_dir))
    timings["backup_s"] = round(time.perf_counter() - started, 1)
    size = sum(p.stat().st_size for p in backup_dir.rglob("*") if p.is_file())
    backed_up = {
        str(p.relative_to(backup_dir / "objects")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (backup_dir / "objects").rglob("*")
        if p.is_file()
    }
    check(
        "backup contains every stored object (originals and page images)",
        bool(before_obj) and backed_up == before_obj,
        objects_in_bucket=len(before_obj),
        objects_in_backup=len(backed_up),
    )

    started = time.perf_counter()
    sv.compose("down", "-v")  # the disaster: every volume is gone
    sv.compose("up", "-d")
    sh(
        "sh",
        "-c",
        "timeout 600 sh -c 'until curl -fsS http://127.0.0.1:8080/api/v1/health/ready >/dev/null 2>&1; do sleep 3; done'",
    )
    sv.compose("stop", "api", "worker")
    timings["fresh_environment_s"] = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    restore_out = sh("scripts/restore.sh", str(backup_dir), input_text="restore\n")
    timings["restore_s"] = round(time.perf_counter() - started, 1)
    # Compare before the application touches the data (the worker would process the
    # QUEUED job and new audit rows would appear).
    after_db = db_fingerprint(values)
    after_obj = object_fingerprint()
    differing = sorted(
        t for t in set(before_db) | set(after_db) if before_db.get(t) != after_db.get(t)
    )
    check(
        "database identical after restore (row counts + content hash, every table)",
        not differing,
        tables=len(before_db),
        rows=sum(v[0] for v in before_db.values()),
        differing=differing,
        **{
            f"rows_{t}": before_db[t][0]
            for t in (
                "tenants",
                "users",
                "documents",
                "processing_jobs",
                "review_tasks",
                "review_actions",
                "action_runs",
                "audit_logs",
                "document_types",
                "schema_versions",
                "connections",
                "api_keys",
                "processing_policies",
            )
            if t in before_db
        },
    )
    missing = sorted(set(before_obj) - set(after_obj))
    changed = sorted(k for k in before_obj if k in after_obj and before_obj[k] != after_obj[k])
    check(
        "object store identical after restore (SHA-256 per object)",
        not missing and not changed,
        objects=len(before_obj),
        missing=missing[:5],
        changed=changed[:5],
    )
    started = time.perf_counter()
    sv.compose("start", "api", "worker")
    sh(
        "sh",
        "-c",
        "timeout 300 sh -c 'until curl -fsS http://127.0.0.1:8080/api/v1/health/ready >/dev/null 2>&1; do sleep 3; done'",
    )
    timings["restart_s"] = round(time.perf_counter() - started, 1)
    await verify(api, values, seeded, check)
    await api.client.aclose()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "backup_dir": backup_dir.name,
        "backup_bytes": size,
        "timings": timings,
        "restore_output_tail": restore_out.strip().splitlines()[-2:],
        "checks": checks,
    }
    (ROOT / "restore-drill-report.json").write_text(json.dumps(report, indent=2, default=str))
    failed = [c["check"] for c in checks if not c["ok"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed; timings {timings}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
