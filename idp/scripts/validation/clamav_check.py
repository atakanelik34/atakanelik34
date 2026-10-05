#!/usr/bin/env python3
"""Live ClamAV validation against a running Compose stack (MALWARE_SCANNER=clamav).

Start the stack with the scanner:
    MALWARE_SCANNER=clamav docker compose --profile av up -d
then:
    backend/.venv/bin/python scripts/validation/clamav_check.py

Checks, through nginx → API → real clamd:
1. a clean PDF is accepted and recorded `scan_status=clean`;
2. a PDF carrying the EICAR test file is rejected (422), audited with the
   signature, and not stored as a document;
3. the same through an API key (machine ingestion has no separate path);
4. a bare EICAR file is refused by the type allow-list (415) before scanning;
5. clamd stopped: uploads fail closed (503), nothing stored;
6. clamd back: uploads are accepted again.
Writes clamav-validation-report.json. Uses `docker compose` (honours COMPOSE_FILE).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tests.fixtures import files  # noqa: E402

API = "http://127.0.0.1:8080/api/v1"


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)  # noqa: S603, S607


async def upload(client: httpx.AsyncClient, headers: dict[str, str], data: bytes, name: str):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{API}/documents", headers=headers, files={"file": (name, data, "application/pdf")}
    )


async def main() -> int:
    values = env()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **data: Any) -> None:
        checks.append({"check": name, "ok": ok, **data})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {json.dumps(data, default=str)[:300]}")

    async with httpx.AsyncClient(timeout=120) as client:
        token = (
            await client.post(
                f"{API}/auth/login",
                json={
                    "email": values["IDP_BOOTSTRAP_EMAIL"],
                    "password": values["IDP_BOOTSTRAP_PASSWORD"],
                },
            )
        ).json()["access_token"]
        user = {"Authorization": f"Bearer {token}"}
        tag = datetime.now(UTC).strftime("%H%M%S%f")

        clean = await upload(client, user, files.make_pdf([f"clamav clean {tag}"]), "clean.pdf")
        detail = (
            (
                await client.get(f"{API}/documents/{clean.json()['document']['id']}", headers=user)
            ).json()
            if clean.status_code == 201
            else {}
        )
        check(
            "1 clean upload accepted and scanned",
            clean.status_code == 201 and detail.get("scan_status") == "clean",
            status=clean.status_code,
            scan_status=detail.get("scan_status"),
        )

        before = (await client.get(f"{API}/documents?limit=100", headers=user)).json()["items"]
        infected = await upload(client, user, files.eicar_pdf(), "invoice.pdf")
        after = (await client.get(f"{API}/documents?limit=100", headers=user)).json()["items"]
        audit = (
            await client.get(f"{API}/audit-logs?action=document.malware_rejected", headers=user)
        ).json()["items"]
        check(
            "2 EICAR-in-PDF rejected, audited, not stored",
            infected.status_code == 422
            and len(after) == len(before)
            and bool(audit)
            and "Eicar" in (audit[0]["after"].get("signature") or ""),
            status=infected.status_code,
            signature=audit[0]["after"].get("signature") if audit else None,
            documents_before=len(before),
            documents_after=len(after),
        )

        key = (
            await client.post(f"{API}/api-keys", headers=user, json={"name": f"av-{tag}"})
        ).json()
        machine = {"Authorization": f"Bearer {key['token']}"}
        via_key = await upload(client, machine, files.eicar_pdf(), "scan.pdf")
        check(
            "3 API-key upload of EICAR rejected",
            via_key.status_code == 422,
            status=via_key.status_code,
        )
        await client.delete(f"{API}/api-keys/{key['id']}", headers=user)

        bare = await upload(client, user, files.EICAR, "eicar.pdf")
        check(
            "4 bare EICAR refused by type allow-list",
            bare.status_code == 415,
            status=bare.status_code,
        )

        compose("stop", "clamav")
        try:
            down = await upload(client, user, files.make_pdf([f"clamav down {tag}"]), "down.pdf")
            check(
                "5 scanner unavailable: upload fails closed",
                down.status_code == 503,
                status=down.status_code,
                code=down.json().get("code")
                if down.headers.get("content-type", "").startswith("application")
                else None,
            )
        finally:
            compose("start", "clamav")
        deadline = time.monotonic() + 300
        back = None
        while time.monotonic() < deadline:
            back = await upload(client, user, files.make_pdf([f"clamav back {tag}"]), "back.pdf")
            if back.status_code != 503:
                break
            await asyncio.sleep(5)
        check(
            "6 scanner back: uploads accepted again",
            back is not None and back.status_code == 201,
            status=back.status_code if back else None,
        )

    report = {"generated_at": datetime.now(UTC).isoformat(), "checks": checks}
    (ROOT / "clamav-validation-report.json").write_text(json.dumps(report, indent=2))
    failed = [c["check"] for c in checks if not c["ok"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
