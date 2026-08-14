"""Redaction must hold on every path that shows a recorded transcript to an
LLM: distill (bind_parameters/redact_steps, tested in test_agent_distill.py)
and repair (build_repair_context, wired into every failure branch of
run_agent's retry loop). This file is the dedicated proof for the repair
path — the one place a typed credential could otherwise leak, since
drive-time values come from the plan rather than the page."""
import json

from app.agent.distill import redact_steps
from app.agent.runner import build_repair_context

SECRET_STEPS = [
    {"type": "goto", "url": "https://x/login"},
    {"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}},
    {"type": "fill", "selectors": ["input[name=otp]"], "value": {"literal": "654321"}},
    {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
]


def test_serialized_redacted_steps_contain_no_secrets():
    payload = json.dumps(redact_steps(SECRET_STEPS))
    assert "hunter2" not in payload
    assert "654321" not in payload
    assert "television" in payload  # the real search term survives


def test_repair_context_is_built_from_redacted_steps():
    context = build_repair_context(SECRET_STEPS, failure="differs_from_drive")
    assert "hunter2" not in context
    assert "654321" not in context
    assert "television" in context


def test_repair_context_does_not_mutate_the_caller_steps():
    build_repair_context(SECRET_STEPS, failure="x")
    assert SECRET_STEPS[1]["value"]["literal"] == "hunter2"  # caller's copy untouched


def test_repair_context_includes_the_failure_reason():
    context = build_repair_context(SECRET_STEPS, failure="a very specific failure reason")
    assert "a very specific failure reason" in context


def test_repair_context_caps_long_literals_too():
    steps = [{"type": "fill", "selectors": ["#q"], "value": {"literal": "x" * 500}}]
    context = build_repair_context(steps, failure="x")
    # redact_steps caps at 120 chars; the 500-char literal must not survive whole.
    assert "x" * 500 not in context
