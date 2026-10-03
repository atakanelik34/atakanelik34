"""Development double for posting to an ERP. Clearly labelled as mock.

Returns a deterministic, obviously fake document number derived from the
idempotency key (so retries return the same number). Nothing is posted.
"""

from __future__ import annotations

import hashlib

from idp.infrastructure.db.models import Connection
from idp.providers.actions.base import ActionOutcome, ActionRequest


class MockERPActionProvider:
    kind = "mock_erp"
    is_mock = True

    def configured(self) -> bool:
        return True

    async def execute(self, connection: Connection, request: ActionRequest) -> ActionOutcome:
        del connection
        digest = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:10].upper()
        return ActionOutcome(
            external_reference=f"MOCK-ERP-{digest}",
            response={"mock": True, "note": "nothing was posted to a real ERP"},
        )
