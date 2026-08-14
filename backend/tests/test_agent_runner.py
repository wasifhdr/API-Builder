from app.agent.runner import MAX_ATTEMPTS, build_repair_context, repair_hint, should_retry
from app.agent.verify import CheckResult, VerifyResult


def _verify(passed: bool) -> VerifyResult:
    return VerifyResult(checks=[CheckResult("replays", passed, "d")])


def test_should_retry_while_attempts_remain():
    assert should_retry(_verify(False), attempt=1) is True
    assert should_retry(_verify(False), attempt=MAX_ATTEMPTS - 1) is True


def test_should_not_retry_after_the_last_attempt():
    assert should_retry(_verify(False), attempt=MAX_ATTEMPTS) is False


def test_should_not_retry_a_passing_verify():
    assert should_retry(_verify(True), attempt=1) is False


def test_repair_hint_names_the_failed_checks():
    result = VerifyResult(checks=[
        CheckResult("replays", True, "ok"),
        CheckResult("differs_from_drive", False, "output is identical"),
    ])
    hint = repair_hint(result)
    assert "differs_from_drive" in hint
    assert "identical" in hint


def test_repair_hint_suggests_interaction_for_the_differential_failure():
    result = VerifyResult(checks=[CheckResult("differs_from_drive", False, "identical")])
    assert "interact" in repair_hint(result).lower()


def test_repair_hint_empty_when_nothing_failed():
    result = VerifyResult(checks=[CheckResult("replays", True, "ok")])
    assert repair_hint(result) == ""


def test_build_repair_context_summarizes_the_previous_attempt():
    steps = [
        {"type": "goto", "url": "https://x/search.html"},
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "refrigerator"}},
    ]
    context = build_repair_context(steps, "differs_from_drive")
    assert "differs_from_drive" in context
    assert "refrigerator" in context
    assert "goto" in context and "fill" in context


from app.agent.runner import sample_failure_reason

SAMPLE_FIELDS = [{"name": "title"}, {"name": "price"}]


def test_sample_failure_reason_is_none_when_every_field_has_a_value():
    sample = [{"title": "Blue Refrigerator", "price": "45000"}]
    assert sample_failure_reason(sample, SAMPLE_FIELDS) is None


def test_sample_failure_reason_names_the_empty_fields():
    sample = [{"title": "Blue Refrigerator", "price": None}]
    assert sample_failure_reason(sample, SAMPLE_FIELDS) == (
        "the marked elements produced no value for: price"
    )


def test_sample_failure_reason_rejects_a_sample_that_never_ran():
    assert sample_failure_reason(None, SAMPLE_FIELDS) == (
        "the marked elements produced no value for: title, price"
    )
