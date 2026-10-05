# Confidence calibration (generated)

Dataset: synthetic (tests/fixtures/benchmark.py fixture_set, seed 2026) · OCR: tesseract 5.3.4 · LLM: none configured (deterministic extractors only)

Documents 84: {'WAITING_FOR_HUMAN': 46, 'COMPLETED': 35, 'FAILED': 3}. Straight-through 41.7 %, review 54.8 %, failed 3.6 %. **Silent errors** (completed automatically with a wrong or invented value): 0. Completed with a missed value: 6. Documents with a wrong value that went to review: 15.

| Kind | Docs | Field accuracy | Document accuracy | Line-cell accuracy |
|---|---|---|---|---|
| native_invoice | 60 | 0.9761 | 0.7667 | 1.0 |
| scanned_invoice | 15 | 0.7671 | 0.0 | 0.3409 |

| Field | Outcomes |
|---|---|
| currency | {'missed': 19, 'correct': 56} |
| due_date | {'correct': 55, 'true_absent': 20} |
| iban | {'correct': 58, 'true_absent': 17} |
| invoice_date | {'correct': 75} |
| invoice_number | {'correct': 75} |
| purchase_order_number | {'true_absent': 28, 'correct': 47} |
| subtotal | {'correct': 69, 'wrong': 6} |
| tax | {'correct': 60, 'wrong': 15} |
| total | {'correct': 67, 'missed': 8} |
| vendor_name | {'correct': 75} |
| vendor_tax_number | {'correct': 46, 'true_absent': 29} |

| Field | Correct (n, confidence range) | Wrong (n, confidence range) |
|---|---|---|
| currency | 56 [0.854, 0.89] | 0 None |
| due_date | 55 [0.844, 0.9] | 0 None |
| iban | 58 [0.829, 0.87] | 0 None |
| invoice_date | 75 [0.86, 0.9] | 0 None |
| invoice_number | 75 [0.806, 0.92] | 0 None |
| purchase_order_number | 47 [0.822, 0.9] | 0 None |
| subtotal | 69 [0.711, 0.9] | 6 [0.729, 0.763] |
| tax | 60 [0.865, 0.87] | 15 [0.327, 0.841] |
| total | 67 [0.761, 0.9] | 0 None |
| vendor_name | 75 [0.432, 0.895] | 0 None |
| vendor_tax_number | 46 [0.796, 0.89] | 0 None |

Field observations with a machine value: 704, of which wrong: 21. Expected calibration error: 0.1256.

| Confidence band | n | Accuracy | Wilson 95 % | Mean confidence |
|---|---|---|---|---|
| [0.00, 0.50) | 29 | 0.9655 | [0.828, 0.994] | 0.444 |
| [0.70, 0.75) | 4 | 0.25 | [0.046, 0.699] | 0.725 |
| [0.75, 0.80) | 17 | 0.8235 | [0.59, 0.938] | 0.769 |
| [0.80, 0.85) | 59 | 0.7627 | [0.64, 0.853] | 0.831 |
| [0.85, 0.90) | 426 | 1.0 | [0.991, 1.0] | 0.881 |
| [0.90, 0.95) | 169 | 1.0 | [0.978, 1.0] | 0.905 |

Current thresholds: wrong values at/above threshold (skip review): 14; correct values below threshold (needless review): 55.

Recommendation: {"auto_accept": {"threshold": 0.839, "n": 610, "accuracy_wilson_low": 0.9833}, "escalate_below": {"threshold": 0.432, "n": 1, "accuracy": 0.0}}

Sufficient to validate an auto-accept threshold: **no** (fewer than 30 errors observed)
