from idp.domain.evaluation import (
    GroundTruth,
    ItemOutcome,
    MachineOutput,
    Prediction,
    aggregate,
    align_rows,
    normalized_match,
    score_item,
)


def test_normalized_match_is_numeric_and_case_insensitive() -> None:
    assert normalized_match("1249.50", "1249.5")
    assert normalized_match(" ACME  GmbH. ", "acme gmbh")
    assert not normalized_match("INV-1", "INV-2")
    assert normalized_match("nan", "NaN ")  # not parsed as a number
    assert normalized_match(10, "10.00")


def test_field_counts_precision_recall_and_calibration() -> None:
    truth = GroundTruth(fields={"total": "100.00", "vendor": "ACME", "due": None, "po": "PO-1"})
    output = MachineOutput(
        fields={
            "total": Prediction("100.0", 0.9, 0.85),  # tp
            "vendor": Prediction("ACNE", 0.95, 0.8),  # fp + fn, overconfident
            "due": Prediction("2026-01-01", 0.5, 0.8),  # fp
            # po missing → fn
        }
    )
    scores = score_item(truth, output)
    assert (scores["total"].tp, scores["vendor"].fp, scores["vendor"].fn) == (1, 1, 1)
    assert scores["due"].fp == 1 and scores["po"].fn == 1
    metrics, per_field = aggregate(
        [ItemOutcome(scores, needed_review=True, cost=0.01, latency_ms=200)]
    )
    assert metrics["tp"] == 1 and metrics["fp"] == 2 and metrics["fn"] == 2
    assert metrics["precision"] == round(1 / 3, 4)
    assert metrics["recall"] == round(1 / 3, 4)
    assert metrics["overconfident"] == 1
    assert metrics["intervention_rate"] == 1.0
    assert metrics["mean_confidence_correct"] == 0.9
    assert per_field["total"]["f1"] == 1.0
    assert per_field["po"]["precision"] is None


def test_rows_are_aligned_by_content_not_position() -> None:
    expected = [{"desc": "Pump", "total": "900"}, {"desc": "Seal", "total": "25"}]
    predicted = [
        {"desc": Prediction("Seal", 0.9), "total": Prediction("25.00", 0.9)},
        {"desc": Prediction("Pump", 0.9), "total": Prediction("901", 0.9)},
        {"desc": Prediction("Shipping", 0.6), "total": Prediction("10", 0.6)},
    ]
    pairs = align_rows(expected, predicted)
    assert pairs[0][1] is predicted[1] and pairs[1][1] is predicted[0]
    assert pairs[2] == (None, predicted[2])
    scores = score_item(
        GroundTruth({}, {"lines[]": expected}), MachineOutput({}, {"lines[]": predicted})
    )
    assert scores["lines[].desc"].tp == 2 and scores["lines[].desc"].fp == 1
    assert scores["lines[].total"].tp == 1 and scores["lines[].total"].fn == 1
