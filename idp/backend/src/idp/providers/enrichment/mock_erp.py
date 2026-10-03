"""Development double for an ERP vendor master. Clearly labelled as mock.

It serves a fixed, obviously fictitious demo vendor list so routing, matching
and the UI can be exercised without an ERP. Results carry `is_mock`, and it is
refused unless mock providers are allowed by the deployment and the policy.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.matching import Match, Record, score_record
from idp.infrastructure.db.models import Connection

DEMO_VENDORS = (
    Record(
        "MOCK-V-1001",
        "ACME Industrial Supplies GmbH",
        {
            "tax_id": "DE123456789",
            "iban": "DE89370400440532013000",
            "payment_terms": "NET30",
            "source": "mock-erp",
        },
    ),
    Record(
        "MOCK-V-1002",
        "Globex Office Solutions Ltd",
        {"tax_id": "GB987654321", "payment_terms": "NET45", "source": "mock-erp"},
    ),
    Record(
        "MOCK-V-1003",
        "Initech Services AG",
        {"tax_id": "CHE123456789", "payment_terms": "NET14", "source": "mock-erp"},
    ),
)


class MockERPProvider:
    kind = "mock_erp"
    is_mock = True

    async def lookup(
        self,
        session: AsyncSession,
        connection: Connection,
        entity: str,
        criteria: Mapping[str, str],
    ) -> list[Match]:
        del session, connection
        if entity != "vendor":
            return []
        matches = (score_record(r, criteria) for r in DEMO_VENDORS)
        return [m for m in matches if m is not None]
