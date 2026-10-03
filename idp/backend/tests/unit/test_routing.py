from idp.domain.routing import (
    Candidate,
    DocumentSignals,
    FieldConfidence,
    Locality,
    PageSignal,
    PolicySnapshot,
    ProcessingMode,
    Route,
    RoutingPolicy,
    Tier,
    unresolved,
)
from idp.providers.resilience import BreakerState, CircuitBreaker

ROUTER = RoutingPolicy()
DETERMINISTIC = [
    Candidate("regex", Tier.DETERMINISTIC, Locality.LOCAL),
    Candidate("table", Tier.DETERMINISTIC, Locality.LOCAL, needs_tables=True),
]
LOCAL_LLM = Candidate("ollama", Tier.LOCAL_LLM, Locality.LOCAL, cost_per_page=0.0)
CLOUD_LLM = Candidate("cloud", Tier.CLOUD_LLM, Locality.CLOUD, cost_per_page=0.01)


def signals(
    sources: list[str],
    *,
    quality: float = 0.95,
    ocr: float | None = None,
    doc_type: str | None = "invoice",
    confidence: float = 0.9,
    tables: bool = True,
) -> DocumentSignals:
    return DocumentSignals(
        pages=tuple(
            PageSignal(i + 1, s, quality, ocr if s == "ocr" else None, 0.4)
            for i, s in enumerate(sources)
        ),
        document_type=doc_type,
        classification_confidence=confidence,
        expects_tables=tables,
    )


def test_native_document_runs_deterministic_first_and_llm_as_fallback() -> None:
    plan = ROUTER.plan(signals(["native", "native"]), [*DETERMINISTIC, LOCAL_LLM], PolicySnapshot())
    assert plan.route is Route.NATIVE_TEXT
    assert plan.stages == (("regex", "table"), ("ollama",))
    assert any("OCR not required" in r for r in plan.reasons)
    assert any("table density" in r for r in plan.reasons)


def test_local_only_rejects_cloud_with_a_reason() -> None:
    plan = ROUTER.plan(signals(["native"]), [*DETERMINISTIC, CLOUD_LLM], PolicySnapshot())
    assert "cloud" not in {n for stage in plan.stages for n in stage}
    assert {"provider": "cloud", "reason": "policy LOCAL_ONLY forbids cloud providers"} in (
        plan.trace()["rejected"]
    )


def test_hybrid_keeps_cloud_strictly_after_local_stages_even_on_weak_signals() -> None:
    weak = signals(["ocr"], quality=0.4, ocr=0.4)
    plan = ROUTER.plan(weak, [*DETERMINISTIC, CLOUD_LLM], PolicySnapshot(ProcessingMode.HYBRID))
    assert plan.route is Route.OCR_TEXT
    assert plan.stages == (("regex", "table"), ("cloud",))
    allowed = PolicySnapshot(ProcessingMode.CLOUD_ALLOWED)
    merged = ROUTER.plan(weak, [*DETERMINISTIC, CLOUD_LLM], allowed)
    assert merged.stages == (("regex", "table", "cloud"),)


def test_weak_signals_run_a_local_fallback_alongside_the_primary_stage() -> None:
    plan = ROUTER.plan(
        signals(["native"], confidence=0.3), [*DETERMINISTIC, LOCAL_LLM], PolicySnapshot()
    )
    assert plan.stages == (("regex", "table", "ollama"),)
    assert any("classification confidence 0.30" in r for r in plan.reasons)


def test_admission_rules() -> None:
    candidates = [
        Candidate("unconfigured", Tier.LOCAL_LLM, Locality.LOCAL, configured=False),
        Candidate("mock", Tier.LOCAL_LLM, Locality.LOCAL, is_mock=True),
        Candidate("broken", Tier.MODEL, Locality.LOCAL, circuit_open=True),
        Candidate("pricey", Tier.CLOUD_LLM, Locality.CLOUD, cost_per_page=1.0),
        *DETERMINISTIC,
    ]
    policy = PolicySnapshot(ProcessingMode.CLOUD_ALLOWED, max_cost_per_document=0.5)
    plan = ROUTER.plan(signals(["native"], tables=False), candidates, policy)
    reasons = {r.provider: r.reason for r in plan.rejected}
    assert reasons == {
        "unconfigured": "not configured",
        "mock": "mock provider disabled",
        "broken": "circuit open after repeated failures",
        "pricey": "estimated cost 1.0000 exceeds policy limit 0.5",
        "table": "schema has no repeating groups",
    }
    assert plan.stages == (("regex",),)
    no_llm = ROUTER.plan(signals(["native"]), [LOCAL_LLM], PolicySnapshot(allow_llm=False))
    assert no_llm.rejected[0].reason == "policy disallows LLM extraction"


def test_unclassified_and_unreadable_documents_go_to_review() -> None:
    unknown = ROUTER.plan(signals(["native"], doc_type=None), DETERMINISTIC, PolicySnapshot())
    assert unknown.route is Route.UNCLASSIFIED
    assert unknown.stages == ()
    blank = ROUTER.plan(signals(["none", "none"]), DETERMINISTIC, PolicySnapshot())
    assert blank.route is Route.NO_TEXT
    assert "human review required" in blank.reasons
    mixed = ROUTER.plan(signals(["native", "ocr"], ocr=0.9), DETERMINISTIC, PolicySnapshot())
    assert mixed.route is Route.MIXED_TEXT
    trace = mixed.trace()
    assert trace["signals"]["native_pages"] == 1
    assert trace["routing_version"] == ROUTER.version


def test_unresolved_only_counts_required_fields() -> None:
    assert unresolved(
        [
            FieldConfidence("total", True, 0.9, 0.85),
            FieldConfidence("vendor", True, 0.4, 0.8),
            FieldConfidence("due", True, None, 0.8),
            FieldConfidence("note", False, None, 0.8),
        ]
    ) == ["vendor", "due"]


def test_circuit_breaker_opens_half_opens_and_recovers() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=10, clock=lambda: now[0])
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()
    now[0] = 11
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow()  # the single trial
    assert not breaker.allow()
    breaker.record_failure()  # failed trial: open again with a fresh cool-down
    assert breaker.state is BreakerState.OPEN
    now[0] = 22
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
