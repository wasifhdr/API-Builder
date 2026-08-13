import pytest

from app.agent.distill import DistillError, bind_parameters, redact_steps

PLAN = {
    "parameters": [{
        "name": "query", "type": "string", "required": True,
        "drive_value": "television", "verify_value": "fan", "description": None,
    }],
    "fields": [{"name": "title", "type": "string"}],
}


def test_binds_a_fill_step_value():
    steps = [
        {"type": "goto", "url": "https://x/"},
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
        {"type": "click", "selectors": ["button"]},
    ]
    bound = bind_parameters(steps, PLAN)
    assert bound[1]["value"] == {"param": "query"}


def test_binds_a_goto_url_as_a_template():
    steps = [{"type": "goto", "url": "https://x/search?q=television&page=1"}]
    bound = bind_parameters(steps, PLAN)
    assert bound[0]["url_template"] == "https://x/search?q={query}&page=1"
    assert bound[0]["url"] == "https://x/search?q=television&page=1"


def test_binds_a_url_encoded_value_in_a_goto():
    plan = {"parameters": [{**PLAN["parameters"][0], "drive_value": "smart tv"}],
            "fields": PLAN["fields"]}
    steps = [{"type": "goto", "url": "https://x/search?q=smart%20tv"}]
    bound = bind_parameters(steps, plan)
    assert bound[0]["url_template"] == "https://x/search?q={query}"


def test_does_not_mutate_the_input_steps():
    steps = [{"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}}]
    bind_parameters(steps, PLAN)
    assert steps[0]["value"] == {"literal": "television"}


def test_raises_when_a_parameter_was_never_used():
    steps = [{"type": "goto", "url": "https://x/"}]
    with pytest.raises(DistillError, match="query"):
        bind_parameters(steps, PLAN)


def test_binds_every_occurrence_of_the_value():
    steps = [
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
        {"type": "fill", "selectors": ["#q2"], "value": {"literal": "television"}},
    ]
    bound = bind_parameters(steps, PLAN)
    assert bound[0]["value"] == {"param": "query"}
    assert bound[1]["value"] == {"param": "query"}


def test_redacts_password_like_steps():
    steps = [
        {"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}},
        {"type": "fill", "selectors": ["input[name=otp]"], "value": {"literal": "123456"}},
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
    ]
    safe = redact_steps(steps)
    assert safe[0]["value"]["literal"] == "[REDACTED]"
    assert safe[1]["value"]["literal"] == "[REDACTED]"
    assert safe[2]["value"]["literal"] == "television"


def test_redaction_caps_long_literals():
    steps = [{"type": "fill", "selectors": ["#q"], "value": {"literal": "x" * 500}}]
    assert len(redact_steps(steps)[0]["value"]["literal"]) == 120


def test_redaction_does_not_mutate_the_input():
    steps = [{"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}}]
    redact_steps(steps)
    assert steps[0]["value"]["literal"] == "hunter2"


def test_append_extract_step_adds_one_when_missing():
    from app.agent.distill import append_extract_step

    steps = append_extract_step([{"type": "goto", "url": "https://x/"}], {"main": {"mode": "list"}})
    assert steps[-1] == {"type": "extract", "ref": "main"}


def test_append_extract_step_is_idempotent():
    from app.agent.distill import append_extract_step

    existing = [{"type": "extract", "ref": "main"}]
    assert append_extract_step(existing, {"main": {}}) == existing


def test_append_extract_step_noop_without_extraction():
    from app.agent.distill import append_extract_step

    steps = [{"type": "goto", "url": "https://x/"}]
    assert append_extract_step(steps, {}) == steps
