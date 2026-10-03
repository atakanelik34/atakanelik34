from decimal import Decimal

import pytest

from idp.domain.normalization import iban_is_valid, normalize, parse_date, parse_decimal
from idp.domain.taxonomy import FieldDefinition, FieldType


def f(type_: str, **normalization: object) -> FieldDefinition:
    return FieldDefinition(name="x", type=FieldType(type_), normalization=normalization)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1234,56", "1234.56"),
        ("1,234", "1234"),
        ("12,5", "12.5"),
        ("€ 1 234,50", "1234.50"),
        ("CHF 1'234.50", "1234.50"),
        ("(12.00)", "-12.00"),
        ("12.00-", "-12.00"),
        ("−3.10", "-3.10"),
        ("1.234.567", "1234567"),
        ("1.000", "1000"),
        ("0.125", "0.125"),
        ("12.50", "12.50"),
        ("1,249.50 EUR", "1249.50"),
    ],
)
def test_decimals_with_auto_separator(raw: str, expected: str) -> None:
    assert parse_decimal(raw) == Decimal(expected)


def test_explicit_decimal_separator() -> None:
    assert parse_decimal("1.234", separator=",") == Decimal("1234")
    assert parse_decimal("1,234", separator=",") == Decimal("1.234")


@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("03.10.2026", "DMY", "2026-10-03"),
        ("2026-10-03", "DMY", "2026-10-03"),
        ("10/03/2026", "MDY", "2026-10-03"),
        ("13/02/2026", "MDY", "2026-02-13"),  # unambiguous despite MDY
        ("3. Oktober 2026", "DMY", "2026-10-03"),
        ("Oct 3, 2026", "DMY", "2026-10-03"),
        ("3 Ekim 2026", "DMY", "2026-10-03"),
        ("03.10.26", "DMY", "2026-10-03"),
    ],
)
def test_dates(raw: str, order: str, expected: str) -> None:
    parsed = parse_date(raw, order)
    assert parsed is not None
    assert parsed.isoformat() == expected


@pytest.mark.parametrize("raw", ["31.02.2026", "yesterday", "2026-13-01"])
def test_invalid_dates(raw: str) -> None:
    assert parse_date(raw) is None


@pytest.mark.parametrize(
    ("type_", "raw", "value", "ok"),
    [
        ("currency", "€", "EUR", True),
        ("currency", "usd", "USD", True),
        ("currency", "XYZ", "XYZ", False),
        ("iban", "de89 3704 0044 0532 0130 00", "DE89370400440532013000", True),
        ("boolean", "Ja", True, True),
        ("boolean", "maybe", "maybe", False),
        ("email", "Info@ACME.test", "info@acme.test", True),
        ("phone", "+49 (0)30 123 456-7", "+49301234567", True),
        ("integer", "1.000", 1000, True),
        ("integer", "2.5", "2.5", False),
        ("date", "never", "never", False),
        ("string", "  ACME GmbH: ", "ACME GmbH", True),
    ],
)
def test_normalize_by_type(type_: str, raw: str, value: object, ok: bool) -> None:
    result = normalize(raw, f(type_))
    assert (result.value, result.ok) == (value, ok)
    assert (result.reason is None) == ok


def test_iban_checksum() -> None:
    assert iban_is_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_is_valid("DE89 3704 0044 0532 0130 01")
    assert not iban_is_valid("not an iban")
