"""Deterministic extraction routing (ARCHITECTURE.md §6).

Routing is rules over measured signals: the page text source and quality, OCR
confidence, table density, the classified document type and its confidence,
and the tenant's processing policy. AI may contribute signals, never the route.

A plan is a list of *stages* ordered by preference (deterministic → layout →
specialised model → local LLM → cloud LLM). The extract step runs stage 0 and
escalates to the next stage only while required fields are missing or below
their confidence threshold. Every candidate that is not used is recorded with a
reason (policy, not configured, circuit open), so the route trace explains
both what ran and what was ruled out.

Pure: no I/O. The policy is typed Python with a version; a declarative,
tenant-editable form is deferred until tenants need to edit it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class Locality(StrEnum):
    LOCAL = "local"  # runs in our process / our network (incl. self-hosted models)
    CLOUD = "cloud"  # document content leaves our infrastructure


class ProcessingMode(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    HYBRID = "HYBRID"  # cloud only as a fallback after local stages
    CLOUD_ALLOWED = "CLOUD_ALLOWED"


class Tier(IntEnum):
    """Preference order: lower tiers are cheaper and more deterministic."""

    DETERMINISTIC = 0
    LAYOUT = 1
    MODEL = 2
    LOCAL_LLM = 3
    CLOUD_LLM = 4


class Route(StrEnum):
    NATIVE_TEXT = "NATIVE_TEXT"
    OCR_TEXT = "OCR_TEXT"
    MIXED_TEXT = "MIXED_TEXT"
    NO_TEXT = "NO_TEXT"  # nothing readable: only review can help
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    """The effective processing policy for one run (persisted per tenant in phase 9)."""

    mode: ProcessingMode = ProcessingMode.LOCAL_ONLY
    allow_llm: bool = True
    allow_mock_providers: bool = False
    max_cost_per_document: float | None = None
    version: int = 0  # 0 = deployment default from configuration

    def permits(self, locality: Locality) -> bool:
        return locality is Locality.LOCAL or self.mode is not ProcessingMode.LOCAL_ONLY


_MODE_ORDER = (ProcessingMode.LOCAL_ONLY, ProcessingMode.HYBRID, ProcessingMode.CLOUD_ALLOWED)


def clamp_policy(requested: PolicySnapshot, ceiling: PolicySnapshot) -> PolicySnapshot:
    """A tenant policy can only be as permissive as the deployment allows."""
    mode = min(requested.mode, ceiling.mode, key=_MODE_ORDER.index)
    limits = [
        c for c in (requested.max_cost_per_document, ceiling.max_cost_per_document) if c is not None
    ]
    return PolicySnapshot(
        mode=mode,
        allow_llm=requested.allow_llm and ceiling.allow_llm,
        allow_mock_providers=requested.allow_mock_providers and ceiling.allow_mock_providers,
        max_cost_per_document=min(limits) if limits else None,
        version=requested.version,
    )


@dataclass(frozen=True, slots=True)
class PageSignal:
    page_number: int
    text_source: str  # native | ocr | none
    text_quality: float | None
    ocr_confidence: float | None
    table_density: float | None


@dataclass(frozen=True, slots=True)
class DocumentSignals:
    pages: tuple[PageSignal, ...]
    document_type: str | None
    classification_confidence: float
    expects_tables: bool  # the schema has repeating groups

    @property
    def native_pages(self) -> int:
        return sum(1 for p in self.pages if p.text_source == "native")

    @property
    def ocr_pages(self) -> int:
        return sum(1 for p in self.pages if p.text_source == "ocr")

    @property
    def unreadable_pages(self) -> int:
        return sum(1 for p in self.pages if p.text_source not in {"native", "ocr"})

    @staticmethod
    def _mean(values: Sequence[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return round(sum(present) / len(present), 3) if present else None

    @property
    def mean_text_quality(self) -> float | None:
        return self._mean([p.text_quality for p in self.pages])

    @property
    def mean_ocr_confidence(self) -> float | None:
        return self._mean([p.ocr_confidence for p in self.pages if p.text_source == "ocr"])

    @property
    def max_table_density(self) -> float:
        return max((p.table_density or 0.0 for p in self.pages), default=0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": len(self.pages),
            "native_pages": self.native_pages,
            "ocr_pages": self.ocr_pages,
            "unreadable_pages": self.unreadable_pages,
            "mean_text_quality": self.mean_text_quality,
            "mean_ocr_confidence": self.mean_ocr_confidence,
            "max_table_density": round(self.max_table_density, 3),
            "document_type": self.document_type,
            "classification_confidence": round(self.classification_confidence, 3),
            "expects_tables": self.expects_tables,
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """What the router needs to know about a provider; no provider code here."""

    name: str
    tier: Tier
    locality: Locality
    configured: bool = True
    is_mock: bool = False
    circuit_open: bool = False
    cost_per_page: float = 0.0
    needs_tables: bool = False  # only useful when the schema has repeating groups


@dataclass(frozen=True, slots=True)
class Rejection:
    provider: str
    reason: str


@dataclass(frozen=True, slots=True)
class RoutePlan:
    route: Route
    stages: tuple[tuple[str, ...], ...]
    reasons: tuple[str, ...]
    rejected: tuple[Rejection, ...]
    policy_version: int
    routing_version: int
    signals: dict[str, Any] = field(default_factory=dict)

    def trace(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "routing_version": self.routing_version,
            "policy_version": self.policy_version,
            "signals": self.signals,
            "reasons": list(self.reasons),
            "planned_stages": [list(s) for s in self.stages],
            "rejected": [{"provider": r.provider, "reason": r.reason} for r in self.rejected],
        }


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """Typed routing rules. Bump `version` whenever behaviour changes."""

    version: int = 1
    min_classification_confidence: float = 0.5
    low_text_quality: float = 0.6
    low_ocr_confidence: float = 0.6
    table_density_threshold: float = 0.3

    def _route(self, s: DocumentSignals, reasons: list[str]) -> Route:
        if s.document_type is None:
            reasons.append("document type unknown: no schema to extract against")
            return Route.UNCLASSIFIED
        if not s.pages or s.unreadable_pages == len(s.pages):
            reasons.append("no readable text on any page (OCR not configured or failed)")
            return Route.NO_TEXT
        if s.ocr_pages == 0:
            reasons.append(
                f"native text layer on all pages (mean quality {s.mean_text_quality}); "
                "OCR not required"
            )
            return Route.NATIVE_TEXT
        if s.native_pages == 0:
            reasons.append(f"scanned pages, OCR text (mean confidence {s.mean_ocr_confidence})")
            return Route.OCR_TEXT
        reasons.append(f"{s.native_pages} native + {s.ocr_pages} OCR pages")
        return Route.MIXED_TEXT

    def _escalate_early(self, s: DocumentSignals, reasons: list[str]) -> bool:
        """Signals saying deterministic extraction alone is unlikely to be enough."""
        early = False
        if s.classification_confidence < self.min_classification_confidence:
            reasons.append(
                f"classification confidence {s.classification_confidence:.2f} "
                f"< {self.min_classification_confidence}"
            )
            early = True
        quality = s.mean_text_quality
        if quality is not None and quality < self.low_text_quality:
            reasons.append(f"text quality {quality} < {self.low_text_quality}")
            early = True
        ocr = s.mean_ocr_confidence
        if ocr is not None and ocr < self.low_ocr_confidence:
            reasons.append(f"OCR confidence {ocr} < {self.low_ocr_confidence}")
            early = True
        return early

    def _admit(self, c: Candidate, s: DocumentSignals, policy: PolicySnapshot) -> str | None:
        """Why a candidate may not run, or None when it may (first failing check wins)."""
        cost = c.cost_per_page * len(s.pages)
        limit = policy.max_cost_per_document
        checks: list[tuple[bool, str]] = [
            (not c.configured, "not configured"),
            (c.is_mock and not policy.allow_mock_providers, "mock provider disabled"),
            (
                not policy.permits(c.locality),
                f"policy {policy.mode.value} forbids {c.locality.value} providers",
            ),
            (c.tier >= Tier.LOCAL_LLM and not policy.allow_llm, "policy disallows LLM extraction"),
            (c.circuit_open, "circuit open after repeated failures"),
            (c.needs_tables and not s.expects_tables, "schema has no repeating groups"),
            (
                limit is not None and cost > limit,
                f"estimated cost {cost:.4f} exceeds policy limit {limit}",
            ),
        ]
        return next((reason for failed, reason in checks if failed), None)

    def plan(
        self,
        signals: DocumentSignals,
        candidates: Sequence[Candidate],
        policy: PolicySnapshot,
    ) -> RoutePlan:
        reasons: list[str] = []
        route = self._route(signals, reasons)
        rejected: list[Rejection] = []
        if route in (Route.UNCLASSIFIED, Route.NO_TEXT):
            return RoutePlan(
                route=route,
                stages=(),
                reasons=(*reasons, "human review required"),
                rejected=(),
                policy_version=policy.version,
                routing_version=self.version,
                signals=signals.as_dict(),
            )
        if signals.expects_tables and signals.max_table_density >= self.table_density_threshold:
            reasons.append(
                f"table density {signals.max_table_density:.2f} >= {self.table_density_threshold}"
            )
        admitted: list[Candidate] = []
        for c in candidates:
            why = self._admit(c, signals, policy)
            if why is None:
                admitted.append(c)
            else:
                rejected.append(Rejection(c.name, why))

        # Group by tier; under HYBRID cloud is only ever a later fallback, which
        # tier ordering already guarantees (CLOUD_LLM is the highest tier).
        tiers = sorted({c.tier for c in admitted})
        stages = [tuple(c.name for c in admitted if c.tier == t) for t in tiers]
        by_name = {c.name: c for c in admitted}
        if self._escalate_early(signals, reasons) and len(stages) > 1:
            # Weak signals: merge the first fallback into the primary stage so
            # both run together instead of waiting for a failed first pass —
            # unless that would send content to the cloud before local stages
            # had their chance (HYBRID means cloud is strictly a fallback).
            cloud = any(by_name[n].locality is Locality.CLOUD for n in stages[1])
            if not cloud or policy.mode is ProcessingMode.CLOUD_ALLOWED:
                stages = [stages[0] + stages[1], *stages[2:]]
                reasons.append("first fallback runs alongside the primary stage")
        if not stages:
            reasons.append("no admissible provider: human review required")
        return RoutePlan(
            route=route,
            stages=tuple(stages),
            reasons=tuple(reasons),
            rejected=tuple(rejected),
            policy_version=policy.version,
            routing_version=self.version,
            signals=signals.as_dict(),
        )


DEFAULT_ROUTING_POLICY = RoutingPolicy()


@dataclass(frozen=True, slots=True)
class FieldConfidence:
    path: str
    required: bool
    confidence: float | None  # None = missing
    threshold: float


def unresolved(fields: Sequence[FieldConfidence]) -> list[str]:
    """Required fields that are missing or below threshold: the escalation trigger."""
    return [
        f.path
        for f in fields
        if f.required and (f.confidence is None or f.confidence < f.threshold)
    ]
