"""Evaluation metrics: machine output vs. ground truth (ARCHITECTURE.md §15).

Pure functions. Ground truth comes from approved human reviews (or manual
curation); predictions are the *machine* values of a run (`original_value`),
never the human-corrected ones, so a run measures the pipeline itself.

Per field path, comparisons that involve a value on either side are counted:

* expected and predicted agree (normalised) → true positive
* expected present, predicted absent → false negative
* expected absent, predicted present → false positive
* both present but different → false positive *and* false negative

Repeating groups are aligned row-by-row by best cell agreement (row order and
row ids differ between runs), then scored per cell path (`lines[].total`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_EDGE_PUNCT = ".,;:"


@dataclass(frozen=True, slots=True)
class Prediction:
    value: Any
    confidence: float
    threshold: float = 0.0


@dataclass(frozen=True, slots=True)
class GroundTruth:
    fields: Mapping[str, Any]
    tables: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MachineOutput:
    fields: Mapping[str, Prediction]
    tables: Mapping[str, Sequence[Mapping[str, Prediction]]] = field(default_factory=dict)


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def normalize(value: Any) -> Any:
    """Comparison form: numbers by value, text case/whitespace-insensitive."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).normalize()
    text = _WS.sub(" ", str(value)).strip().strip(_EDGE_PUNCT).strip().casefold()
    if _NUMBER.match(text):
        try:
            return Decimal(text).normalize()
        except InvalidOperation:  # pragma: no cover - guarded by the pattern
            return text
    return text


def exact_match(expected: Any, predicted: Any) -> bool:
    return bool(expected == predicted)


def normalized_match(expected: Any, predicted: Any) -> bool:
    return bool(normalize(expected) == normalize(predicted))


@dataclass(slots=True)
class FieldScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    compared: int = 0
    exact: int = 0
    normalized: int = 0
    confidence_correct: list[float] = field(default_factory=list)
    confidence_incorrect: list[float] = field(default_factory=list)
    overconfident: int = 0  # wrong but at/above its threshold: would skip review

    def add(self, expected: Any, predicted: Prediction | None) -> None:
        has_expected = _present(expected)
        value = predicted.value if predicted else None
        has_predicted = _present(value)
        if not has_expected and not has_predicted:
            return
        self.compared += 1
        if has_expected and has_predicted:
            self.exact += exact_match(expected, value)
            correct = normalized_match(expected, value)
        else:
            correct = False
        if correct:
            self.tp += 1
            self.normalized += 1
        else:
            self.fp += has_predicted
            self.fn += has_expected
        if predicted is not None and has_predicted:
            bucket = self.confidence_correct if correct else self.confidence_incorrect
            bucket.append(predicted.confidence)
            if not correct and predicted.confidence >= predicted.threshold > 0:
                self.overconfident += 1

    def merge(self, other: FieldScore) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.compared += other.compared
        self.exact += other.exact
        self.normalized += other.normalized
        self.confidence_correct.extend(other.confidence_correct)
        self.confidence_incorrect.extend(other.confidence_incorrect)
        self.overconfident += other.overconfident

    def summary(self) -> dict[str, Any]:
        precision = _ratio(self.tp, self.tp + self.fp)
        recall = _ratio(self.tp, self.tp + self.fn)
        f1 = (
            round(2 * precision * recall / (precision + recall), 4)
            if precision is not None and recall is not None and precision + recall > 0
            else None
        )
        confidences = self.confidence_correct + self.confidence_incorrect
        return {
            "compared": self.compared,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "exact_match": _ratio(self.exact, self.compared),
            "normalized_match": _ratio(self.normalized, self.compared),
            "mean_confidence": _mean(confidences),
            "mean_confidence_correct": _mean(self.confidence_correct),
            "mean_confidence_incorrect": _mean(self.confidence_incorrect),
            "overconfident": self.overconfident,
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def align_rows(
    expected: Sequence[Mapping[str, Any]], predicted: Sequence[Mapping[str, Prediction]]
) -> list[tuple[Mapping[str, Any] | None, Mapping[str, Prediction] | None]]:
    """Greedy alignment by number of agreeing cells; leftovers pair with None."""
    remaining = list(range(len(predicted)))
    pairs: list[tuple[Mapping[str, Any] | None, Mapping[str, Prediction] | None]] = []
    for row in expected:
        best, best_score = None, 0
        for index in remaining:
            score = sum(
                1
                for key, value in row.items()
                if key in predicted[index] and normalized_match(value, predicted[index][key].value)
            )
            if score > best_score:
                best, best_score = index, score
        if best is None:
            pairs.append((row, None))
        else:
            remaining.remove(best)
            pairs.append((row, predicted[best]))
    pairs.extend((None, predicted[i]) for i in remaining)
    return pairs


def score_item(truth: GroundTruth, output: MachineOutput) -> dict[str, FieldScore]:
    scores: dict[str, FieldScore] = defaultdict(FieldScore)
    for path in set(truth.fields) | set(output.fields):
        scores[path].add(truth.fields.get(path), output.fields.get(path))
    for array in set(truth.tables) | set(output.tables):
        for expected_row, predicted_row in align_rows(
            truth.tables.get(array, ()), output.tables.get(array, ())
        ):
            cells = set(expected_row or {}) | set(predicted_row or {})
            for cell in cells:
                scores[f"{array}.{cell}"].add(
                    (expected_row or {}).get(cell), (predicted_row or {}).get(cell)
                )
    return dict(scores)


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    scores: Mapping[str, FieldScore]
    needed_review: bool
    cost: float
    latency_ms: int | None


def aggregate(outcomes: Sequence[ItemOutcome]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overall metrics and per-field metrics for a run."""
    per_field: dict[str, FieldScore] = defaultdict(FieldScore)
    overall = FieldScore()
    for outcome in outcomes:
        for path, score in outcome.scores.items():
            per_field[path].merge(score)
            overall.merge(score)
    latencies = [o.latency_ms for o in outcomes if o.latency_ms is not None]
    metrics = {
        **overall.summary(),
        "items": len(outcomes),
        "intervention_rate": _ratio(sum(o.needed_review for o in outcomes), len(outcomes)),
        "cost_per_document": _mean([o.cost for o in outcomes]),
        "latency_ms_per_document": _mean([float(v) for v in latencies]),
    }
    return metrics, {path: score.summary() for path, score in sorted(per_field.items())}
