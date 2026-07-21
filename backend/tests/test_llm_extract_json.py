from app.llm.client import _extract_json


def test_strips_thought_block_and_ignores_decoy_json_inside_it():
    # Google-served Gemma wraps its answer in <thought>…</thought> and often
    # echoes the schema/example JSON inside it. The reasoning block (and its
    # decoy braces) must be stripped so we parse the REAL trailing object.
    raw = (
        '<thought>I should return the format '
        '{"selectors": ["...", "..."]} with real values.</thought>'
        '{"selectors": ["a[data-testid=\\"title-link\\"] h3", ".real"]}'
    )
    assert _extract_json(raw) == {"selectors": ['a[data-testid="title-link"] h3', ".real"]}


def test_strips_think_and_thinking_tags_too():
    assert _extract_json('<think>noise {"x": 0}</think>{"x": 1}') == {"x": 1}
    assert _extract_json('<thinking>noise {"x": 0}</thinking>{"x": 1}') == {"x": 1}
