"""Value normalisation per field type (pure, deterministic).

Returns a JSON-safe canonical value: decimals as strings ("1234.56", never
floats), dates as ISO strings, currencies as ISO 4217 codes. A failed
normalisation keeps the raw text and reports why, so a reviewer sees both.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from idp.domain.taxonomy import FieldDefinition, FieldType


def _codes(text: str) -> frozenset[str]:
    return frozenset(text.split())


ISO_4217 = _codes(
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL "
    "BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP "
    "ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR "
    "IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL "
    "LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR "
    "NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD "
    "SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX "
    "USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL "
)
CURRENCY_SYMBOLS = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "¥": "JPY",
    "₺": "TRY",
    "₹": "INR",
    "₽": "RUB",
    "CHF": "CHF",
    "Fr.": "CHF",
    "zł": "PLN",
    "kr": "SEK",
}

_MONTHS = {
    # en
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    # de
    "januar": 1,
    "jänner": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "oktober": 10,
    "okt": 10,
    "dezember": 12,
    "dez": 12,
    # tr
    "ocak": 1,
    "şubat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "eylül": 9,
    "ekim": 10,
    "kasım": 11,
    "aralık": 12,
    # fr / es (common)
    "janvier": 1,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

AMOUNT_PATTERN = (
    r"[-−(]?\s*(?:[€$£¥₺₹]\s*)?\d{1,3}(?:[.,'\s]\d{3})*(?:[.,]\d{1,4})?\s*(?:[€$£¥₺₹])?\)?-?"
)
DATE_PATTERN = (
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"\d{1,2}\.?\s+[A-Za-zÄÖÜäöüşŞğĞıİçÇéû]{3,10}\.?\s+\d{4}|"
    r"[A-Za-z]{3,10}\.?\s+\d{1,2},?\s+\d{4})\b"
)
IBAN_PATTERN = r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"
EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
PHONE_PATTERN = r"\+?\d[\d\s()/.-]{6,}\d"
_EMAIL = re.compile(rf"^{EMAIL_PATTERN}$")


@dataclass(frozen=True, slots=True)
class Normalized:
    value: Any
    ok: bool
    reason: str | None = None


def _fail(raw: str, reason: str) -> Normalized:
    return Normalized(value=raw, ok=False, reason=reason)


def _auto_separators(text: str) -> str:
    """Rewrite digits with ',' and '.' into a canonical '1234.56' form."""
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        # Whichever comes last is the decimal separator.
        if last_comma > last_dot:
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")
    for sep in (",", "."):
        if text.count(sep) > 1:
            return text.replace(sep, "")
    if last_comma < 0 and last_dot < 0:
        return text
    sep = "," if last_comma >= 0 else "."
    position = text.rfind(sep)
    # "1,234" / "1.000": a single separator before exactly three digits (and not
    # a leading zero) is a thousands separator; otherwise it is the decimal mark.
    if len(text) - position - 1 == 3 and not text.startswith("0"):
        return text.replace(sep, "")
    return text.replace(sep, ".")


def parse_decimal(raw: str, separator: str = "auto") -> Decimal | None:
    text = raw.strip().replace("−", "-").replace(" ", " ")
    negative = text.startswith(("-", "(")) or text.endswith(("-", ")"))
    text = re.sub(r"[^\d.,' ]", "", text).strip().replace(" ", "").replace("'", "")
    if not text or not any(c.isdigit() for c in text):
        return None
    if separator == ",":
        text = text.replace(".", "").replace(",", ".")
    elif separator == ".":
        text = text.replace(",", "")
    else:
        text = _auto_separators(text)
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_date(raw: str, order: str = "DMY") -> date | None:  # noqa: PLR0911 — one return per format
    text = raw.strip().lower().replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)
    if m := re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text):
        a, b, y = (int(g) for g in m.groups())
        y = y + 2000 if y < 100 else y
        # Unambiguous when one side exceeds 12; otherwise use the configured order.
        if a > 12 >= b:
            return _safe_date(y, b, a)
        if b > 12 >= a:
            return _safe_date(y, a, b)
        return _safe_date(y, b, a) if order == "DMY" else _safe_date(y, a, b)
    if m := re.fullmatch(r"(\d{1,2})\.? ([^\d\s.]+)\.? (\d{4})", text):
        month = _MONTHS.get(m.group(2))
        return _safe_date(int(m.group(3)), month, int(m.group(1))) if month else None
    if m := re.fullmatch(r"([^\d\s.]+)\.? (\d{1,2}) (\d{4})", text):
        month = _MONTHS.get(m.group(1))
        return _safe_date(int(m.group(3)), month, int(m.group(2))) if month else None
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalize_currency(raw: str) -> str | None:
    text = raw.strip()
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    code = re.sub(r"[^A-Za-z]", "", text).upper()
    return code if code in ISO_4217 else None


def iban_is_valid(iban: str) -> bool:
    compact = re.sub(r"\s", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


_TRUE = {"yes", "true", "y", "x", "ja", "evet", "oui", "sí", "si", "1", "✓"}
_FALSE = {"no", "false", "n", "nein", "hayır", "non", "0"}
_IBAN_SHAPE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}")
Normalizer = Callable[[str, str, dict[str, Any]], Normalized]


def _string(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    return Normalized(text.strip(" :;,"), ok=True)


def _integer(raw: str, text: str, o: dict[str, Any]) -> Normalized:
    number = parse_decimal(text, o.get("decimal_separator", "auto"))
    if number is None or number != number.to_integral_value():
        return _fail(raw, "not an integer")
    return Normalized(int(number), ok=True)


def _decimal(raw: str, text: str, o: dict[str, Any]) -> Normalized:
    number = parse_decimal(text, o.get("decimal_separator", "auto"))
    return Normalized(str(number), ok=True) if number is not None else _fail(raw, "not a number")


def _currency(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    code = normalize_currency(text)
    return Normalized(code, ok=True) if code else _fail(raw, "unknown currency")


def _date(raw: str, text: str, o: dict[str, Any]) -> Normalized:
    parsed = parse_date(text, o.get("date_order", "DMY"))
    return Normalized(parsed.isoformat(), ok=True) if parsed else _fail(raw, "unrecognised date")


def _datetime(raw: str, text: str, o: dict[str, Any]) -> Normalized:
    try:
        return Normalized(datetime.fromisoformat(text).isoformat(), ok=True)
    except ValueError:
        return _date(raw, text, o)


def _boolean(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    lowered = text.lower()
    if lowered in _TRUE:
        return Normalized(True, ok=True)
    if lowered in _FALSE:
        return Normalized(False, ok=True)
    return _fail(raw, "not a boolean")


def _email(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    return Normalized(text.lower(), ok=True) if _EMAIL.match(text) else _fail(raw, "not an email")


def _phone(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    digits = re.sub(r"[^\d+]", "", text.replace("(0)", ""))
    if len(re.sub(r"\D", "", digits)) < 7:
        return _fail(raw, "not a phone number")
    return Normalized(digits, ok=True)


def _iban(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    # Checksum validity is a validation rule (phase 6); normalisation canonicalises.
    compact = re.sub(r"\s", "", text).upper()
    return (
        Normalized(compact, ok=True)
        if _IBAN_SHAPE.fullmatch(compact)
        else _fail(raw, "not an IBAN")
    )


def _tax_number(raw: str, text: str, _o: dict[str, Any]) -> Normalized:
    return Normalized(re.sub(r"[\s.-]", "", text).upper(), ok=True)


_NORMALIZERS: dict[FieldType, Normalizer] = {
    FieldType.STRING: _string,
    FieldType.ADDRESS: _string,
    FieldType.INTEGER: _integer,
    FieldType.DECIMAL: _decimal,
    FieldType.CURRENCY: _currency,
    FieldType.DATE: _date,
    FieldType.DATETIME: _datetime,
    FieldType.BOOLEAN: _boolean,
    FieldType.EMAIL: _email,
    FieldType.PHONE: _phone,
    FieldType.IBAN: _iban,
    FieldType.TAX_NUMBER: _tax_number,
}


def normalize(raw: str, field: FieldDefinition) -> Normalized:
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return _fail(raw, "empty")
    normalizer = _NORMALIZERS.get(field.type)
    if normalizer is None:
        return _fail(raw, f"type {field.type} is not a scalar")
    return normalizer(raw, text, field.normalization)


# Patterns used to pick the value out of the text following a label.
TYPE_VALUE_PATTERNS: dict[FieldType, str] = {
    FieldType.DECIMAL: AMOUNT_PATTERN,
    FieldType.INTEGER: r"-?\d[\d.,' ]*",
    FieldType.DATE: DATE_PATTERN,
    FieldType.DATETIME: DATE_PATTERN,
    FieldType.IBAN: IBAN_PATTERN,
    FieldType.EMAIL: EMAIL_PATTERN,
    FieldType.PHONE: PHONE_PATTERN,
    FieldType.CURRENCY: r"\b[A-Z]{3}\b|[€$£¥₺₹]",
}
