"""Deterministic entity matching for enrichment (master data, ERP lookups).

Identifiers (tax ids, IBANs, record keys) match exactly after normalisation.
Names match fuzzily after removing case, punctuation and legal-form suffixes,
scored with a sequence ratio and capped below an identifier match.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

IDENTIFIER_ATTRIBUTES = frozenset({"key", "tax_id", "iban"})
NAME_WEIGHT = 0.95  # a perfect name match still ranks below an identifier match
AMBIGUITY_MARGIN = 0.05

_LEGAL_FORMS = (
    "gmbh",
    "mbh",
    "ag",
    "kg",
    "ohg",
    "ug",
    "se",
    "ltd",
    "limited",
    "plc",
    "llc",
    "inc",
    "corp",
    "corporation",
    "co",
    "company",
    "sa",
    "sas",
    "sarl",
    "srl",
    "spa",
    "bv",
    "nv",
    "ab",
    "as",
    "oy",
    "a s",
    "as",
    "ltd sti",
    "sti",
    "anonim sirketi",
    "pty",
)
_LEGAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(f) for f in _LEGAL_FORMS) + r")\b")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


def normalize_name(value: str) -> str:
    text = _NON_ALNUM.sub(" ", value.casefold())
    text = _LEGAL_RE.sub(" ", text)
    return " ".join(text.split())


def name_similarity(a: str, b: str) -> float:
    left, right = normalize_name(a), normalize_name(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


@dataclass(frozen=True, slots=True)
class Record:
    key: str
    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def attribute(self, name: str) -> str | None:
        if name == "key":
            return self.key
        if name == "name":
            return self.name
        value = self.attributes.get(name)
        return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class Match:
    record: Record
    score: float
    matched_on: tuple[str, ...]


def score_record(record: Record, criteria: Mapping[str, str]) -> Match | None:
    """Best evidence wins: any identifier hit scores 1.0; a contradicting one rules out."""
    matched: list[str] = []
    score = 0.0
    for attribute, wanted in criteria.items():
        have = record.attribute(attribute)
        if have is None or not wanted:
            continue
        if attribute == "name":
            similarity = name_similarity(wanted, have)
            if similarity > 0:
                score = max(score, round(similarity * NAME_WEIGHT, 4))
                matched.append("name")
        elif normalize_identifier(have) == normalize_identifier(wanted):
            score = 1.0
            matched.append(attribute)
        elif attribute in IDENTIFIER_ATTRIBUTES:
            return None  # e.g. a different tax id: certainly not this record
        elif have.strip().casefold() == wanted.strip().casefold():
            score = max(score, 1.0)
            matched.append(attribute)
    return Match(record, score, tuple(matched)) if matched else None


@dataclass(frozen=True, slots=True)
class Decision:
    status: str  # matched | not_found | ambiguous
    best: Match | None
    candidates: tuple[Match, ...]


def decide(matches: Sequence[Match], min_score: float) -> Decision:
    ranked = sorted(matches, key=lambda m: m.score, reverse=True)[:5]
    good = [m for m in ranked if m.score >= min_score]
    if not good:
        return Decision("not_found", None, tuple(ranked[:3]))
    if len(good) > 1 and good[0].score - good[1].score < AMBIGUITY_MARGIN:
        return Decision("ambiguous", None, tuple(good[:3]))
    return Decision("matched", good[0], tuple(ranked[:3]))
