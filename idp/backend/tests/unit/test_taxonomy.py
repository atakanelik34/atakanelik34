import uuid

import pydantic
import pytest

from idp.domain.classification import TypeCandidate, classify_pages, split
from idp.domain.taxonomy import ClassificationRules, SchemaDefinition
from idp.domain.templates import TEMPLATES
from tests.fixtures.files import MIXED_PACKET_PAGES


def test_templates_are_valid_and_flatten_with_array_paths() -> None:
    invoice = TEMPLATES["invoice"].definition
    paths = [p for p, _ in invoice.flatten()]
    assert "invoice_number" in paths
    assert "lines[]" in paths
    assert "lines[].unit_price" in paths
    assert invoice.field("lines[].total") is not None
    assert all(t.definition.is_classifiable for t in TEMPLATES.values())


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({"fields": [{"name": "a", "type": "string"}, {"name": "a", "type": "date"}]}, "unique"),
        ({"fields": [{"name": "lines", "type": "array"}]}, "item definition"),
        ({"fields": [{"name": "addr", "type": "object"}]}, "needs children"),
        ({"fields": [{"name": "Bad Name", "type": "string"}]}, "pattern"),
        (
            {
                "fields": [
                    {"name": "x", "type": "string", "extraction_hints": {"patterns": ["(unclosed"]}}
                ]
            },
            "invalid regular expression",
        ),
        (
            {
                "fields": [
                    {"name": "x", "type": "string", "extraction_hints": {"patterns": ["no group"]}}
                ]
            },
            "capture group",
        ),
        (
            {
                "fields": [
                    {"name": "x", "type": "string", "extraction_hints": {"patterns": ["(a)" * 200]}}
                ]
            },
            "longer than",
        ),
        ({"fields": [{"name": "x", "type": "unknown"}]}, "type"),
        ({"fields": [{"name": "x", "type": "string", "surprise": 1}]}, "Extra inputs"),
        (
            {"fields": [{"name": "x", "type": "string", "confidence_threshold": 1.5}]},
            "less than or equal",
        ),
    ],
)
def test_invalid_definitions_are_rejected(definition: dict[str, object], message: str) -> None:
    with pytest.raises(pydantic.ValidationError, match=message):
        SchemaDefinition.model_validate(definition)


def _candidate(key: str) -> TypeCandidate:
    rules = TEMPLATES[key].definition.classification
    return TypeCandidate(uuid.uuid4(), key, uuid.uuid4(), rules)


CANDIDATES = [_candidate(k) for k in ("invoice", "delivery_note", "contract", "purchase_order")]


def test_ten_page_packet_splits_into_three_documents() -> None:
    pages = classify_pages(list(enumerate(MIXED_PACKET_PAGES, start=1)), CANDIDATES)
    parts = split(pages)
    assert [
        (p.page_start, p.page_end, p.candidate.key if p.candidate else None) for p in parts
    ] == [
        (1, 3, "invoice"),
        (4, 5, "delivery_note"),
        (6, 10, "contract"),
    ]
    assert all(p.reasons for p in parts)
    # Page 3 ("terms and conditions") carried no invoice keywords: kept as continuation.
    assert any("continuation" in r for r in parts[0].reasons)


def test_unknown_pages_and_new_document_markers() -> None:
    texts = [
        (1, "Some random letter about the weather"),
        (2, "Another unrelated page"),
        (3, "INVOICE number INV-1 amount due Page 1 of 1"),
    ]
    parts = split(classify_pages(texts, CANDIDATES))
    assert (parts[0].page_start, parts[0].page_end, parts[0].candidate) == (1, 2, None)
    assert parts[1].candidate is not None
    assert parts[1].candidate.key == "invoice"


def test_negative_keywords_and_threshold() -> None:
    rules = ClassificationRules(
        keywords=["invoice", "amount due", "invoice number"],
        negative_keywords=["credit note"],
        min_score=0.6,
    )
    candidate = TypeCandidate(uuid.uuid4(), "invoice", uuid.uuid4(), rules)
    [credit] = classify_pages([(1, "Credit note for invoice number 7, amount due 0")], [candidate])
    assert credit.candidate is None
    [weak] = classify_pages([(1, "invoice")], [candidate])
    assert weak.candidate is None  # 1 of 3 keywords < 0.6
    [strong] = classify_pages([(1, "INVOICE  number 7; Amount Due: 10")], [candidate])
    assert strong.candidate is candidate
    assert strong.confidence == 1.0


def test_no_published_types_means_unclassified_not_guessed() -> None:
    [page] = classify_pages([(1, "Invoice number 1 amount due")], [])
    assert page.candidate is None
    assert page.reasons == ("no document type matched",)
