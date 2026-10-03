from __future__ import annotations

from idp.config import Settings
from idp.providers.enrichment.base import EnrichmentProvider
from idp.providers.enrichment.master_data import MasterDataProvider
from idp.providers.enrichment.mock_erp import MockERPProvider
from idp.providers.enrichment.rest import RestLookupProvider


def create_enrichment_providers(settings: Settings) -> dict[str, EnrichmentProvider]:
    """One provider per connection kind. Close the REST provider's client when done."""
    return {
        "master_data": MasterDataProvider(),
        "rest": RestLookupProvider(
            allowed_hosts=settings.enrichment_allowed_host_set,
            timeout_seconds=settings.enrichment_timeout_seconds,
        ),
        "mock_erp": MockERPProvider(),
    }


async def close_enrichment_providers(providers: dict[str, EnrichmentProvider]) -> None:
    rest = providers.get("rest")
    if isinstance(rest, RestLookupProvider):
        await rest.aclose()
