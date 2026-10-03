"""Deterministic validation rule engine. No AI, Decimal arithmetic only.

Rules come from the schema: field-level `validation_rules` and schema-level
cross-field `rules`. Each rule type has a parameter validator (run when a
schema is saved) and an evaluator (run on extracted data). A violated rule
yields the severity configured on the rule (FAIL, WARNING or REQUIRES_HUMAN).
Implicit checks — required fields present, confidence above the field's
threshold — are added for every schema.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from idp.domain.normalization import ISO_4217, iban_is_valid
from idp.domain.taxonomy import (
    REGEX_TIMEOUT_SECONDS,
    FieldDefinition,
    RuleSpec,
    SchemaDefinition,
    compile_user_regex,
)


class Outcome(StrEnum):
    PASS = "PASS"  # noqa: S105 — validation outcome, not a credential
    WARNING = "WARNING"
    FAIL = "FAIL"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


NEEDS_HUMAN = frozenset({Outcome.FAIL, Outcome.REQUIRES_HUMAN})


@dataclass(frozen=True, slots=True)
class FieldState:
    value: Any
    confidence: float
    status: str  # extracted | missing | accepted | corrected | rejected


@dataclass(slots=True)
class ValidationContext:
    schema: SchemaDefinition
    fields: dict[str, FieldState]  # scalar paths
    rows: dict[str, list[dict[str, FieldState]]] = field(default_factory=dict)  # "lines[]" -> rows
    today: date = field(default_factory=date.today)

    def value(self, path: str) -> Any:
        state = self.fields.get(path)
        if state is None or state.status in ("missing", "rejected"):
            return None
        return state.value


@dataclass(frozen=True, slots=True)
class Issue:
    rule_id: str
    rule_type: str
    outcome: Outcome
    fields: tuple[str, ...]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


Evaluator = Callable[
    [RuleSpec, ValidationContext, str | None], tuple[bool, str, dict[str, Any]] | None
]
"""Returns None when the rule does not apply (inputs absent), else (passed, message, details)."""


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value is not None else None
    except ValueError:
        return None


_RELATIVE = re.compile(r"^today(?:([+-])(\d{1,5})d)?$")


def _resolve_date(spec: str, today: date) -> date:
    if m := _RELATIVE.match(spec):
        delta = timedelta(days=int(m.group(2) or 0))
        return today + delta if m.group(1) != "-" else today - delta
    return date.fromisoformat(spec)


# --- field-level evaluators --------------------------------------------------------


def _regex(
    rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    try:
        ok = (
            compile_user_regex(rule.params["pattern"]).fullmatch(
                str(value), timeout=REGEX_TIMEOUT_SECONDS
            )
            is not None
        )
    except TimeoutError:
        ok = False
    return ok, f"{path} does not match the expected format", {}


def _min_max(op: str) -> Evaluator:
    def evaluate(
        rule: RuleSpec, ctx: ValidationContext, path: str | None
    ) -> tuple[bool, str, dict[str, Any]] | None:
        value, bound = _dec(ctx.value(path or "")), _dec(rule.params.get("value"))
        if value is None or bound is None:
            return None
        ok = value >= bound if op == "min" else value <= bound
        word = "at least" if op == "min" else "at most"
        return ok, f"{path} must be {word} {bound}", {"value": str(value), "bound": str(bound)}

    return evaluate


def _length(
    rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    n = len(str(value))
    lo, hi = rule.params.get("min", 0), rule.params.get("max", 10**6)
    return lo <= n <= hi, f"{path} length must be between {lo} and {hi}", {"length": n}


def _date_range(
    rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = _date(ctx.value(path or ""))
    if value is None:
        return None
    lo = _resolve_date(rule.params["min"], ctx.today) if "min" in rule.params else None
    hi = _resolve_date(rule.params["max"], ctx.today) if "max" in rule.params else None
    ok = (lo is None or value >= lo) and (hi is None or value <= hi)
    return (
        ok,
        f"{path} {value.isoformat()} is outside the allowed range",
        {
            "min": lo.isoformat() if lo else None,
            "max": hi.isoformat() if hi else None,
        },
    )


def _one_of(
    rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    allowed = [str(v).lower() for v in rule.params["values"]]
    return (
        str(value).lower() in allowed,
        f"{path} must be one of {', '.join(rule.params['values'])}",
        {},
    )


def _iban(
    _rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    return iban_is_valid(str(value)), f"{path} is not a valid IBAN (checksum)", {}


TAX_NUMBER_FORMATS: dict[str, str] = {
    "DE": r"DE\d{9}",
    "AT": r"ATU\d{8}",
    "FR": r"FR[0-9A-Z]{2}\d{9}",
    "NL": r"NL\d{9}B\d{2}",
    "GB": r"GB(\d{9}|\d{12}|GD\d{3}|HA\d{3})",
    "IT": r"IT\d{11}",
    "ES": r"ES[0-9A-Z]\d{7}[0-9A-Z]",
    "BE": r"BE[01]\d{9}",
    "PL": r"PL\d{10}",
    "TR": r"(TR)?\d{10,11}",
    "CH": r"CHE\d{9}(MWST|TVA|IVA)?",
}


def _tax_number(
    rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    compact = re.sub(r"[\s.-]", "", str(value)).upper()
    country = rule.params.get("country") or compact[:2]
    pattern = TAX_NUMBER_FORMATS.get(country)
    if pattern is None:
        return True, f"no format known for {country}", {"country": country}
    return (
        re.fullmatch(pattern, compact) is not None,
        f"{path} is not a valid {country} tax number",
        {"country": country},
    )


def _currency(
    _rule: RuleSpec, ctx: ValidationContext, path: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    value = ctx.value(path or "")
    if value is None:
        return None
    return str(value).upper() in ISO_4217, f"{path} is not an ISO 4217 currency code", {}


# --- cross-field evaluators ---------------------------------------------------------


def _tolerance(rule: RuleSpec) -> Decimal:
    return _dec(rule.params.get("tolerance", "0.01")) or Decimal("0.01")


def _sum(
    rule: RuleSpec, ctx: ValidationContext, _p: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    target = _dec(ctx.value(rule.params["target"]))
    terms = [_dec(ctx.value(t)) for t in rule.params["terms"]]
    if target is None or any(t is None for t in terms):
        return None
    total = sum((t for t in terms if t is not None), Decimal(0))
    diff = abs(target - total)
    return (
        diff <= _tolerance(rule),
        (f"{rule.params['target']} ({target}) != {' + '.join(rule.params['terms'])} ({total})"),
        {"expected": str(total), "actual": str(target), "difference": str(diff)},
    )


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _compare(
    rule: RuleSpec, ctx: ValidationContext, _p: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    left_raw, right_raw = ctx.value(rule.params["left"]), ctx.value(rule.params["right"])
    left: Any = _date(left_raw) or _dec(left_raw)
    right: Any = _date(right_raw) or _dec(right_raw)
    if left is None or right is None or type(left) is not type(right):
        return None
    op = rule.params["op"]
    return (
        _OPS[op](left, right),
        f"{rule.params['left']} {op} {rule.params['right']} is not satisfied",
        {
            "left": str(left),
            "right": str(right),
        },
    )


def _line_items_sum(
    rule: RuleSpec, ctx: ValidationContext, _p: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    rows = ctx.rows.get(f"{rule.params['lines']}[]", [])
    target = _dec(ctx.value(rule.params["target"]))
    column = rule.params["line_total"]
    values = [
        _dec(r[column].value)
        for r in rows
        if column in r and r[column].status not in ("missing", "rejected")
    ]
    if target is None or not values or any(v is None for v in values):
        return None
    total = sum((v for v in values if v is not None), Decimal(0))
    diff = abs(target - total)
    return (
        diff <= _tolerance(rule),
        f"line items add up to {total}, {rule.params['target']} is {target}",
        {
            "expected": str(total),
            "actual": str(target),
            "rows": len(values),
        },
    )


def _line_item_math(
    rule: RuleSpec, ctx: ValidationContext, _p: str | None
) -> tuple[bool, str, dict[str, Any]] | None:
    rows = ctx.rows.get(f"{rule.params['lines']}[]", [])
    q, p, t = rule.params["quantity"], rule.params["unit_price"], rule.params["total"]
    bad = []
    checked = 0
    for row in rows:
        qty, price, total = (_dec(row[c].value) if c in row else None for c in (q, p, t))
        if qty is None or price is None or total is None:
            continue
        checked += 1
        if abs(qty * price - total) > _tolerance(rule):
            bad.append(str(total))
    if not checked:
        return None
    return (
        not bad,
        f"{len(bad)} line(s) where quantity x unit price != total",
        {"rows_checked": checked},
    )


@dataclass(frozen=True, slots=True)
class RuleKind:
    evaluate: Evaluator
    required_params: tuple[str, ...] = ()
    cross_field: bool = False


RULES: dict[str, RuleKind] = {
    "regex": RuleKind(_regex, ("pattern",)),
    "min": RuleKind(_min_max("min"), ("value",)),
    "max": RuleKind(_min_max("max"), ("value",)),
    "length": RuleKind(_length),
    "date_range": RuleKind(_date_range),
    "one_of": RuleKind(_one_of, ("values",)),
    "iban": RuleKind(_iban),
    "tax_number": RuleKind(_tax_number),
    "currency": RuleKind(_currency),
    "sum": RuleKind(_sum, ("target", "terms"), cross_field=True),
    "compare": RuleKind(_compare, ("left", "op", "right"), cross_field=True),
    "line_items_sum": RuleKind(
        _line_items_sum, ("lines", "line_total", "target"), cross_field=True
    ),
    "line_item_math": RuleKind(
        _line_item_math, ("lines", "quantity", "unit_price", "total"), cross_field=True
    ),
}


def register_rule(name: str, kind: RuleKind) -> None:
    """Extension point (e.g. master-data lookups in phase 10)."""
    RULES[name] = kind


def check_rule_spec(rule: RuleSpec, *, cross_field: bool) -> None:
    """Raise ValueError when a rule is unknown or misconfigured (schema save time)."""
    kind = RULES.get(rule.type)
    if kind is None:
        raise ValueError(f"unknown rule type '{rule.type}'")
    if kind.cross_field != cross_field:
        where = "schema-level `rules`" if kind.cross_field else "a field's `validation_rules`"
        raise ValueError(f"rule '{rule.type}' belongs in {where}")
    missing = [p for p in kind.required_params if p not in rule.params]
    if missing:
        raise ValueError(f"rule '{rule.type}' is missing params: {', '.join(missing)}")
    if rule.type == "regex":
        compile_user_regex(str(rule.params["pattern"]))
    if rule.type == "compare" and rule.params["op"] not in _OPS:
        raise ValueError(f"compare op must be one of {', '.join(_OPS)}")
    if rule.type == "date_range":
        for bound in ("min", "max"):
            if bound in rule.params:
                _resolve_date(str(rule.params[bound]), date.today())


def _implicit(path: str, definition: FieldDefinition, ctx: ValidationContext) -> list[Issue]:
    state = ctx.fields.get(path)
    issues: list[Issue] = []
    if definition.required:
        present = (
            state is not None
            and state.status not in ("missing", "rejected")
            and state.value not in (None, "")
        )
        issues.append(
            Issue(
                rule_id=f"field:{path}:required",
                rule_type="required",
                outcome=Outcome.PASS if present else Outcome.REQUIRES_HUMAN,
                fields=(path,),
                message="" if present else f"{path} is required but was not found",
            )
        )
    if (
        state is not None
        and state.status == "extracted"
        and state.confidence < definition.confidence_threshold
    ):
        issues.append(
            Issue(
                rule_id=f"field:{path}:confidence",
                rule_type="confidence",
                outcome=Outcome.REQUIRES_HUMAN,
                fields=(path,),
                message=(
                    f"{path} confidence {state.confidence:.0%} is below its "
                    f"{definition.confidence_threshold:.0%} threshold"
                ),
                details={
                    "confidence": state.confidence,
                    "threshold": definition.confidence_threshold,
                },
            )
        )
    return issues


def _apply(rule: RuleSpec, rule_id: str, ctx: ValidationContext, path: str | None) -> Issue | None:
    kind = RULES.get(rule.type)
    if kind is None:
        return Issue(
            rule_id,
            rule.type,
            Outcome.REQUIRES_HUMAN,
            (path,) if path else (),
            f"unknown rule type '{rule.type}'",
        )
    result = kind.evaluate(rule, ctx, path)
    if result is None:
        return None
    passed, message, details = result
    fields = (
        (path,)
        if path
        else tuple(
            str(v)
            for k, v in rule.params.items()
            if k in ("target", "left", "right") and isinstance(v, str)
        )
        + tuple(rule.params.get("terms", []))
    )
    return Issue(
        rule_id=rule_id,
        rule_type=rule.type,
        outcome=Outcome.PASS if passed else Outcome(rule.severity),
        fields=fields,
        message="" if passed else (rule.message or message),
        details=details,
    )


def evaluate(ctx: ValidationContext) -> list[Issue]:
    issues: list[Issue] = []
    for path, definition in ctx.schema.flatten():
        if "[]" in path:
            continue  # row-level checks are cross-field rules over the table
        issues.extend(_implicit(path, definition, ctx))
        for i, rule in enumerate(definition.validation_rules):
            if (issue := _apply(rule, f"field:{path}:{rule.type}:{i}", ctx, path)) is not None:
                issues.append(issue)
    for i, rule in enumerate(ctx.schema.rules):
        if (issue := _apply(rule, f"rule:{i}:{rule.type}", ctx, None)) is not None:
            issues.append(issue)
    return issues


def needs_human(issues: list[Issue]) -> bool:
    return any(i.outcome in NEEDS_HUMAN for i in issues)
