from datetime import date
from decimal import Decimal

import pydantic
import pytest

from idp.domain.taxonomy import RuleSpec, SchemaDefinition
from idp.domain.templates import TEMPLATES
from idp.domain.validation import FieldState, Outcome, ValidationContext, evaluate, needs_human

TODAY = date(2026, 10, 3)


def ctx(
    schema: dict, values: dict[str, object], conf: float = 0.95, rows: dict | None = None
) -> ValidationContext:
    definition = SchemaDefinition.model_validate(schema)
    states = {
        k: FieldState(v, conf, "missing" if v is None else "extracted") for k, v in values.items()
    }
    row_states = {
        k: [{c: FieldState(v, conf, "extracted") for c, v in r.items()} for r in rs]
        for k, rs in (rows or {}).items()
    }
    return ValidationContext(schema=definition, fields=states, rows=row_states, today=TODAY)


def outcome(issues, rule_type: str) -> Outcome:  # type: ignore[no-untyped-def]
    return next(i.outcome for i in issues if i.rule_type == rule_type)


def field(name: str, type_: str, *rules: dict, **kw: object) -> dict:
    return {"name": name, "type": type_, "validation_rules": list(rules), **kw}


@pytest.mark.parametrize(
    ("rule", "type_", "value", "expected"),
    [
        ({"type": "min", "params": {"value": 0}}, "decimal", "-1.00", Outcome.FAIL),
        ({"type": "min", "params": {"value": 0}}, "decimal", "0", Outcome.PASS),
        (
            {"type": "max", "params": {"value": "100"}, "severity": "WARNING"},
            "decimal",
            "100.01",
            Outcome.WARNING,
        ),
        (
            {"type": "regex", "params": {"pattern": r"INV-\d{4}-\d+"}},
            "string",
            "INV-2026-1",
            Outcome.PASS,
        ),
        ({"type": "regex", "params": {"pattern": r"INV-\d{4}-\d+"}}, "string", "X-1", Outcome.FAIL),
        ({"type": "length", "params": {"min": 3, "max": 5}}, "string", "ab", Outcome.FAIL),
        ({"type": "one_of", "params": {"values": ["EUR", "USD"]}}, "currency", "eur", Outcome.PASS),
        ({"type": "iban"}, "iban", "DE89370400440532013000", Outcome.PASS),
        (
            {"type": "iban", "severity": "REQUIRES_HUMAN"},
            "iban",
            "DE89370400440532013001",
            Outcome.REQUIRES_HUMAN,
        ),
        ({"type": "tax_number"}, "tax_number", "DE123456789", Outcome.PASS),
        ({"type": "tax_number"}, "tax_number", "DE12345", Outcome.FAIL),
        (
            {"type": "tax_number", "params": {"country": "TR"}},
            "tax_number",
            "1234567890",
            Outcome.PASS,
        ),
        ({"type": "currency"}, "currency", "XYZ", Outcome.FAIL),
        (
            {"type": "date_range", "params": {"min": "today-365d", "max": "today+30d"}},
            "date",
            "2026-11-30",
            Outcome.FAIL,
        ),
        (
            {"type": "date_range", "params": {"min": "today-365d", "max": "today+30d"}},
            "date",
            "2026-09-01",
            Outcome.PASS,
        ),
        (
            {"type": "date_range", "params": {"min": "2020-01-01"}},
            "date",
            "2019-12-31",
            Outcome.FAIL,
        ),
    ],
)
def test_field_rules(rule: dict, type_: str, value: object, expected: Outcome) -> None:
    issues = evaluate(ctx({"fields": [field("x", type_, rule)]}, {"x": value}))
    assert outcome(issues, rule["type"]) is expected


def test_rules_skip_absent_values_but_required_catches_them() -> None:
    schema = {
        "fields": [field("x", "decimal", {"type": "min", "params": {"value": 0}}, required=True)]
    }
    issues = evaluate(ctx(schema, {"x": None}))
    assert [i.rule_type for i in issues] == ["required"]
    assert issues[0].outcome is Outcome.REQUIRES_HUMAN


def test_confidence_below_threshold_requires_human() -> None:
    schema = {"fields": [field("x", "string", confidence_threshold=0.9)]}
    issues = evaluate(ctx(schema, {"x": "abc"}, conf=0.7))
    assert outcome(issues, "confidence") is Outcome.REQUIRES_HUMAN
    assert "70%" in issues[0].message
    assert not [
        i for i in evaluate(ctx(schema, {"x": "abc"}, conf=0.95)) if i.rule_type == "confidence"
    ]


INVOICE = TEMPLATES["invoice"].definition.model_dump(mode="json")


@pytest.mark.parametrize("path", ["tax", "subtotal", "total"])
def test_invoice_amount_thresholds_follow_calibration(path: str) -> None:
    """Phase 13 calibration (docs/validation/CALIBRATION.md): wrong scanned amounts were
    extracted at confidence up to 0.841, correct ones at 0.865+. Amounts need 0.85."""
    thresholds = {p: d.confidence_threshold for p, d in TEMPLATES["invoice"].definition.flatten()}
    assert thresholds[path] == 0.85

    def confidence_issue(conf: float) -> bool:
        issues = evaluate(ctx(INVOICE, {path: "199.50"}, conf=conf))
        return any(i.rule_type == "confidence" and i.fields == (path,) for i in issues)

    assert confidence_issue(0.841)  # highest wrong value observed: must go to review
    assert not confidence_issue(0.865)  # lowest correct value observed: may pass


def test_invoice_arithmetic_and_dates() -> None:
    values = {
        "subtotal": "1050.00",
        "tax": "199.50",
        "total": "1249.50",
        "invoice_date": "2026-10-03",
        "due_date": "2026-11-02",
    }
    rows = {"lines[]": [{"total": "900.00"}, {"total": "125.00"}, {"total": "25.00"}]}
    issues = evaluate(ctx(INVOICE, values, rows=rows))
    assert outcome(issues, "sum") is Outcome.PASS
    assert outcome(issues, "compare") is Outcome.PASS
    assert outcome(issues, "line_items_sum") is Outcome.PASS

    bad = evaluate(
        ctx(INVOICE, {**values, "total": "1300.00", "due_date": "2026-09-01"}, rows=rows)
    )
    sum_issue = next(i for i in bad if i.rule_type == "sum")
    assert sum_issue.outcome is Outcome.REQUIRES_HUMAN
    assert Decimal(sum_issue.details["difference"]) == Decimal("50.50")
    assert set(sum_issue.fields) == {"total", "subtotal", "tax"}
    assert outcome(bad, "compare") is Outcome.WARNING
    assert needs_human(bad)


def test_line_item_math() -> None:
    schema = {
        "fields": [
            field(
                "lines",
                "array",
                item={
                    "name": "l",
                    "type": "object",
                    "children": [
                        {"name": "q", "type": "decimal"},
                        {"name": "p", "type": "decimal"},
                        {"name": "t", "type": "decimal"},
                    ],
                },
            )
        ],
        "rules": [
            {
                "type": "line_item_math",
                "params": {"lines": "lines", "quantity": "q", "unit_price": "p", "total": "t"},
            }
        ],
    }
    good = evaluate(ctx(schema, {}, rows={"lines[]": [{"q": "2", "p": "450.00", "t": "900.00"}]}))
    assert outcome(good, "line_item_math") is Outcome.PASS
    bad = evaluate(ctx(schema, {}, rows={"lines[]": [{"q": "2", "p": "450.00", "t": "950.00"}]}))
    assert outcome(bad, "line_item_math") is Outcome.FAIL


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({"fields": [field("x", "string", {"type": "nope"})]}, "unknown rule type"),
        ({"fields": [field("x", "string", {"type": "min"})]}, "missing params"),
        (
            {
                "fields": [
                    field("x", "string", {"type": "sum", "params": {"target": "x", "terms": []}})
                ]
            },
            "belongs in schema-level",
        ),
        (
            {
                "fields": [field("x", "decimal")],
                "rules": [{"type": "sum", "params": {"target": "x", "terms": ["y"]}}],
            },
            "unknown field 'y'",
        ),
        (
            {
                "fields": [field("x", "date")],
                "rules": [{"type": "compare", "params": {"left": "x", "op": "~", "right": "x"}}],
            },
            "compare op",
        ),
        (
            {
                "fields": [
                    field("x", "date", {"type": "date_range", "params": {"min": "yesterday"}})
                ]
            },
            "Invalid isoformat",
        ),
    ],
)
def test_misconfigured_rules_are_rejected_at_save(definition: dict, message: str) -> None:
    with pytest.raises(pydantic.ValidationError, match=message):
        SchemaDefinition.model_validate(definition)


def test_rule_spec_severity_is_constrained() -> None:
    with pytest.raises(pydantic.ValidationError):
        RuleSpec.model_validate({"type": "min", "severity": "IGNORE"})
