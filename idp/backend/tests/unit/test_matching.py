import httpx
import pytest

from idp.domain.matching import Record, decide, name_similarity, normalize_name, score_record
from idp.domain.taxonomy import RuleSpec, SchemaDefinition
from idp.domain.validation import FieldState, Outcome, ValidationContext, evaluate
from idp.infrastructure.db.models import Connection
from idp.providers.enrichment.base import EnrichmentError, RestConfig
from idp.providers.enrichment.rest import RestLookupProvider

ACME = Record(
    "V-1",
    "ACME Industrial Supplies GmbH",
    {"tax_id": "DE123456789", "iban": "DE89370400440532013000"},
)
ACNE = Record("V-2", "ACNE Industrial Supplies GmbH", {"tax_id": "DE999999999"})


def test_names_ignore_case_punctuation_and_legal_forms() -> None:
    assert normalize_name("ACME Industrial Supplies, GmbH.") == "acme industrial supplies"
    assert name_similarity("Acme Industrial Supplies", "ACME INDUSTRIAL SUPPLIES GMBH") == 1.0
    assert name_similarity("", "x") == 0.0


def test_identifiers_dominate_and_contradictions_exclude() -> None:
    by_tax = score_record(ACME, {"tax_id": "de 123 456 789", "name": "something else"})
    assert by_tax is not None and by_tax.score == 1.0 and "tax_id" in by_tax.matched_on
    assert score_record(ACNE, {"tax_id": "DE123456789", "name": "ACME Industrial"}) is None
    by_name = score_record(ACME, {"name": "Acme Industrial Supplies"})
    assert by_name is not None and by_name.score == 0.95


def test_decisions() -> None:
    a = score_record(ACME, {"name": "ACME Industrial Supplies"})
    b = score_record(ACNE, {"name": "ACME Industrial Supplies"})
    assert a and b
    assert decide([a, b], 0.85).status == "ambiguous"  # near-identical names
    assert decide([a], 0.85).status == "matched"
    assert decide([], 0.85).status == "not_found"
    assert decide([b], 0.99).status == "not_found"


def test_schema_checks_enrichment_and_lookup_references() -> None:
    base = {"fields": [{"name": "vendor_name", "type": "string"}]}
    with pytest.raises(ValueError, match="unknown field"):
        SchemaDefinition.model_validate(
            {
                **base,
                "enrichment": [
                    {"name": "vendor", "connection": "vendors", "match": {"name": "nope"}}
                ],
            }
        )
    with pytest.raises(ValueError, match="unknown enrichment"):
        SchemaDefinition.model_validate(
            {**base, "rules": [{"type": "lookup", "params": {"enrichment": "vendor"}}]}
        )
    schema = SchemaDefinition.model_validate(
        {
            **base,
            "enrichment": [
                {"name": "vendor", "connection": "vendors", "match": {"name": "vendor_name"}}
            ],
            "rules": [
                {"type": "lookup", "params": {"enrichment": "vendor"}, "severity": "REQUIRES_HUMAN"}
            ],
        }
    )
    state = {"vendor_name": FieldState(value="ACME", confidence=0.99, status="extracted")}
    [issue] = [
        i
        for i in evaluate(
            ValidationContext(schema=schema, fields=state, enrichment={"vendor": "not_found"})
        )
        if i.rule_type == "lookup"
    ]
    assert issue.outcome is Outcome.REQUIRES_HUMAN
    passed = evaluate(
        ValidationContext(schema=schema, fields=state, enrichment={"vendor": "matched"})
    )
    assert [i.outcome for i in passed if i.rule_type == "lookup"] == [Outcome.PASS]
    assert not [
        i
        for i in evaluate(ValidationContext(schema=schema, fields=state))
        if i.rule_type == "lookup"
    ]
    assert RuleSpec(type="lookup", params={"enrichment": "vendor"}).severity == "FAIL"


def test_rest_config_rejects_credentials_in_url() -> None:
    with pytest.raises(ValueError, match="credentials"):
        RestConfig.model_validate(
            {"base_url": "https://user:pw@erp.example", "query": {"name": "q"}}
        )


async def test_rest_lookup_maps_and_scores_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "id": 7,
                            "displayName": "ACME Industrial Supplies GmbH",
                            "vat": "DE123456789",
                        },
                        {"id": 8, "displayName": "Other Corp", "vat": "DE000"},
                    ]
                }
            },
        )

    monkeypatch.setenv("IDP_SECRET_ERP_TOKEN", "s3cret")
    provider = RestLookupProvider(
        allowed_hosts=frozenset({"erp.example"}),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    connection = Connection(
        key="erp",
        name="ERP",
        kind="rest",
        config={
            "base_url": "https://erp.example/api",
            "path": "/vendors",
            "query": {"tax_id": "vatId"},
            "results_path": "data.items",
            "name_field": "displayName",
            "attribute_fields": {"tax_id": "vat"},
            "auth_header": "Authorization",
            "secret_env": "IDP_SECRET_ERP_TOKEN",
        },
    )
    matches = await provider.lookup(None, connection, "vendor", {"tax_id": "DE123456789"})  # type: ignore[arg-type]
    assert [m.record.key for m in matches] == ["7"]  # the other record contradicts the tax id
    assert seen[0].url.params["vatId"] == "DE123456789"
    assert seen[0].headers["Authorization"] == "Bearer s3cret"

    blocked = RestLookupProvider(
        allowed_hosts=frozenset(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(EnrichmentError, match="host_not_allowed"):
        await blocked.lookup(None, connection, "vendor", {"tax_id": "x"})  # type: ignore[arg-type]
    assert len(seen) == 1
