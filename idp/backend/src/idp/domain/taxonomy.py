"""Configurable taxonomy: document types are described by versioned schema definitions.

A `SchemaDefinition` drives classification (keywords/markers), extraction
(fields, aliases, hints, normalisation) and validation (field and cross-field
rules). Definitions are validated strictly on save; published versions are
immutable. User-supplied regular expressions are compiled with the `regex`
engine and always executed with a timeout (no catastrophic backtracking).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

import regex
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
MAX_PATTERN_LENGTH = 400
REGEX_TIMEOUT_SECONDS = 0.05
MAX_FIELDS = 200


class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    CURRENCY = "currency"  # ISO 4217 code, e.g. EUR
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    IBAN = "iban"
    TAX_NUMBER = "tax_number"
    ARRAY = "array"
    OBJECT = "object"


SCALAR_TYPES = frozenset(set(FieldType) - {FieldType.ARRAY, FieldType.OBJECT})

Severity = Literal["FAIL", "WARNING", "REQUIRES_HUMAN"]


def compile_user_regex(pattern: str) -> regex.Pattern[str]:
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"pattern longer than {MAX_PATTERN_LENGTH} characters")
    try:
        return regex.compile(pattern, regex.IGNORECASE | regex.MULTILINE)
    except regex.error as exc:
        raise ValueError(f"invalid regular expression: {exc}") from exc


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleSpec(_Strict):
    """A deterministic validation rule. `type` selects the implementation (phase 6)."""

    type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "FAIL"
    message: str | None = Field(default=None, max_length=300)


class ExtractionHints(_Strict):
    # Regexes with one capture group (or a named group `value`) for the value.
    patterns: list[str] = Field(default_factory=list, max_length=20)
    # Where the value sits relative to a label: same line to the right, or below.
    position: Literal["right", "below", "any"] = "any"
    # When no label is found: "first_line" proposes the first line of the first
    # page (issuer names on letterheads) at deliberately low confidence.
    fallback: Literal["none", "first_line"] = "none"

    @field_validator("patterns")
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            compiled = compile_user_regex(pattern)
            if compiled.groups < 1:
                raise ValueError(f"pattern needs a capture group: {pattern}")
        return patterns


class FieldDefinition(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    type: FieldType
    required: bool = False
    description: str = Field(default="", max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    extraction_hints: ExtractionHints = Field(default_factory=ExtractionHints)
    validation_rules: list[RuleSpec] = Field(default_factory=list, max_length=20)
    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    normalization: dict[str, Any] = Field(default_factory=dict)
    children: list[FieldDefinition] = Field(default_factory=list)  # for OBJECT
    item: FieldDefinition | None = None  # for ARRAY

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.type is FieldType.OBJECT and not self.children:
            raise ValueError(f"object field '{self.name}' needs children")
        if self.type is FieldType.ARRAY and self.item is None:
            raise ValueError(f"array field '{self.name}' needs an item definition")
        if self.type in SCALAR_TYPES and (self.children or self.item):
            raise ValueError(f"scalar field '{self.name}' cannot have children/item")
        names = [c.name for c in self.children]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate child names in '{self.name}'")
        return self


class ClassificationRules(_Strict):
    # Distinctive words/phrases for this type (any language). Matching is case-insensitive.
    keywords: list[str] = Field(default_factory=list, max_length=50)
    negative_keywords: list[str] = Field(default_factory=list, max_length=50)
    # Phrases that typically appear on the FIRST page of this document type.
    first_page_markers: list[str] = Field(default_factory=list, max_length=20)
    min_score: float = Field(default=0.5, ge=0, le=1)


CONNECTION_KEY_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
ATTRIBUTE_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"


class EnrichmentRule(_Strict):
    """Look up a business entity (e.g. the vendor) in a configured connection.

    `match` maps lookup attributes (`tax_id`, `iban`, `name`, `key`, or any
    attribute the connection understands) to extracted field paths. `outputs`
    selects the record attributes kept in the result (empty = all).
    """

    name: str = Field(pattern=NAME_PATTERN)
    connection: str = Field(pattern=CONNECTION_KEY_PATTERN)
    entity: str = Field(default="vendor", pattern=ATTRIBUTE_PATTERN)
    match: dict[str, str] = Field(min_length=1, max_length=10)
    outputs: list[str] = Field(default_factory=list, max_length=50)
    min_score: float = Field(default=0.85, ge=0.5, le=1)

    @field_validator("match")
    @classmethod
    def _attributes(cls, match: dict[str, str]) -> dict[str, str]:
        for attribute in match:
            if not regex.fullmatch(ATTRIBUTE_PATTERN, attribute):
                raise ValueError(f"invalid lookup attribute '{attribute}'")
        return match


class SchemaDefinition(_Strict):
    fields: list[FieldDefinition] = Field(default_factory=list)
    # Cross-field rules, e.g. {"type": "sum", "params": {"total": "total", "parts": [...]}}.
    rules: list[RuleSpec] = Field(default_factory=list, max_length=50)
    classification: ClassificationRules = Field(default_factory=ClassificationRules)
    enrichment: list[EnrichmentRule] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        flat = self.flatten()
        if len(flat) > MAX_FIELDS:
            raise ValueError(f"a schema may define at most {MAX_FIELDS} fields")
        # Rules are checked when the schema is saved, not discovered broken at runtime.
        from idp.domain.validation import check_rule_spec  # noqa: PLC0415 — breaks import cycle

        for path, definition in flat:
            for rule in definition.validation_rules:
                try:
                    check_rule_spec(rule, cross_field=False)
                except ValueError as exc:
                    raise ValueError(f"{path}: {exc}") from exc
        known = {path for path, _ in flat}
        for i, rule in enumerate(self.rules):
            check_rule_spec(rule, cross_field=True)
            for key in ("target", "left", "right"):
                ref = rule.params.get(key)
                if isinstance(ref, str) and ref not in known:
                    raise ValueError(f"rules[{i}] refers to unknown field '{ref}'")
            for ref in rule.params.get("terms", []):
                if ref not in known:
                    raise ValueError(f"rules[{i}] refers to unknown field '{ref}'")
        return self

    @model_validator(mode="after")
    def _enrichment_refs(self) -> Self:
        names = [e.name for e in self.enrichment]
        if len(names) != len(set(names)):
            raise ValueError("enrichment names must be unique")
        known = {path for path, _ in self.flatten()}
        for e in self.enrichment:
            for path in e.match.values():
                if path not in known or "[]" in path:
                    raise ValueError(f"enrichment '{e.name}' matches on unknown field '{path}'")
        for i, rule in enumerate(self.rules):
            name = rule.params.get("enrichment")
            if rule.type == "lookup" and name not in names:
                raise ValueError(f"rules[{i}] refers to unknown enrichment '{name}'")
        return self

    def flatten(self) -> list[tuple[str, FieldDefinition]]:
        """Leaf and container paths: `vendor_name`, `lines[]`, `lines[].total`, `address.city`."""
        out: list[tuple[str, FieldDefinition]] = []

        def walk(prefix: str, definition: FieldDefinition) -> None:
            if definition.type is FieldType.ARRAY:
                path = f"{prefix}{definition.name}[]"
                out.append((path, definition))
                # `item` is guaranteed for arrays by the model validator.
                if definition.item is not None and definition.item.type is FieldType.OBJECT:
                    for child in definition.item.children:
                        walk(f"{path}.", child)
            elif definition.type is FieldType.OBJECT:
                path = f"{prefix}{definition.name}"
                out.append((path, definition))
                for child in definition.children:
                    walk(f"{path}.", child)
            else:
                out.append((f"{prefix}{definition.name}", definition))

        for field in self.fields:
            walk("", field)
        return out

    def field(self, path: str) -> FieldDefinition | None:
        return dict(self.flatten()).get(path)

    @property
    def is_classifiable(self) -> bool:
        return bool(self.classification.keywords)


class SchemaStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"
