"""Deterministic action payloads (no templating language, no code).

A payload is assembled only from the processing result: field values (after
human correction), tables, enrichment outputs and document metadata. Nothing in
it is model-generated at execution time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from idp.domain.taxonomy import ActionSpec


@dataclass(frozen=True, slots=True)
class ActionContext:
    document: Mapping[str, Any]  # id, filename, type, pages
    fields: Mapping[str, Any]  # scalar path -> value
    tables: Mapping[str, list[dict[str, Any]]] = field(default_factory=dict)
    enrichment: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # name -> outputs


def resolve(ref: str, ctx: ActionContext) -> Any:
    kind, _, target = ref.partition(":")
    if kind == "field":
        return ctx.fields.get(target)
    if kind == "table":
        return ctx.tables.get(target, [])
    if kind == "enrichment":
        name, _, attribute = target.partition(".")
        outputs = ctx.enrichment.get(name, {})
        return dict(outputs) if not attribute else outputs.get(attribute)
    if kind == "document":
        return ctx.document.get(target)
    if kind == "const":
        return target
    raise ValueError(f"unknown reference '{ref}'")


def build_payload(spec: ActionSpec, ctx: ActionContext) -> dict[str, Any]:
    if spec.payload:
        return {key: resolve(ref, ctx) for key, ref in spec.payload.items()}
    return {
        "document": dict(ctx.document),
        "fields": dict(ctx.fields),
        "tables": {k: list(v) for k, v in ctx.tables.items()},
        "enrichment": {k: dict(v) for k, v in ctx.enrichment.items()},
    }
