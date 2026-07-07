import pytest

from app.llm.client import _extract_json


def test_extract_json_bare():
    assert _extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_extract_json_code_fence():
    content = 'Sure, here you go:\n```json\n{"a": 1}\n```\n'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_bare_code_fence_no_language_tag():
    content = '```\n{"a": 1}\n```'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_think_block():
    content = "<think>reasoning about the answer...</think>\n" '{"a": 1}'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_nested_braces_and_strings():
    content = '{"a": {"b": "a } string with a brace"}, "c": [1, 2]}'
    assert _extract_json(content) == {"a": {"b": "a } string with a brace"}, "c": [1, 2]}


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        _extract_json("I cannot help with that request.")


def test_extract_json_unbalanced_raises():
    with pytest.raises(ValueError):
        _extract_json('{"a": 1')
