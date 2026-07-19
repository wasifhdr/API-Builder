"""LLM fallback for extraction.

The deterministic extractor (extraction.py) uses one rigid CSS selector per
field. When list items differ in shape, a selector matches some items and
misses others, leaving null fields. This module fills those gaps with the
configured LLM (local llama or a remote gateway via LLM_PROVIDER).

Design:
- Only runs when the LLM is enabled AND some field is null (pure fallback —
  costs nothing when selectors already worked).
- Uses values the selector *did* extract as few-shot examples, so the model
  learns what each field means even when field names are opaque ("field1").
- Instructed to return null rather than invent, so genuinely-absent fields
  (e.g. an announcement with no body) stay null.
- Never raises: any failure returns the deterministic data unchanged.
"""

import asyncio
import logging
from typing import Any

from playwright.async_api import Page

from app.config import settings
from app.llm.client import complete_json

log = logging.getLogger("llm")

# Serialize fallback calls so concurrent replays don't fan out onto a
# single-GPU llama-server (LLM concurrency is 1 by project rule). A remote
# gateway tolerates more, but occasional extraction fallback is cheap to
# serialize either way.
_LLM_LOCK = asyncio.Semaphore(1)

MAX_ITEMS = 40  # cap tokens on very long lists
ITEM_TEXT_CAP = 1200  # chars of item text sent per row
MAX_EXAMPLES = 2  # few-shot examples per field


def _llm_configured() -> bool:
    if not settings.llm_enabled:
        return False
    if settings.llm_provider == "craftx":
        return bool(settings.craftx_base_url and settings.craftx_api_key and settings.craftx_model)
    return True


def _apply_transform(value: Any, transform: str | None) -> Any:
    if value is None or not transform or transform == "none":
        return value
    if transform == "trim":
        return str(value).strip()
    if transform == "number":
        cleaned = "".join(c for c in str(value) if c.isdigit() or c in ".-")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return value


async def _item_texts(page: Page, root: str) -> list[str]:
    js = (
        "(cfg) => Array.from(document.querySelectorAll(cfg.root))"
        ".map(r => (r.innerText || '').trim().slice(0, cfg.cap))"
    )
    return await page.evaluate(js, {"root": root, "cap": ITEM_TEXT_CAP})


def _field_examples(fields: list[dict], rows: list[dict], targets: list[str]) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {}
    for name in targets:
        seen: list[str] = []
        for row in rows:
            v = row.get(name)
            if isinstance(v, str) and v.strip():
                seen.append(v.strip()[:200])
            if len(seen) >= MAX_EXAMPLES:
                break
        examples[name] = seen
    return examples


def _build_schema(targets: list[str]) -> dict:
    props: dict = {"index": {"type": "integer"}}
    for name in targets:
        props[name] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", *targets],
                    "properties": props,
                },
            }
        },
    }


def _build_user_prompt(targets: list[str], examples: dict[str, list[str]], items: list[tuple[int, str]]) -> str:
    lines = ["Extract these fields from each list item below:"]
    for name in targets:
        ex = examples.get(name) or []
        hint = f'  example{"s" if len(ex) > 1 else ""}: {", ".join(repr(e) for e in ex)}' if ex else ""
        lines.append(f"- {name}{(' —' + hint) if hint else ''}")
    lines.append("")
    lines.append(
        'Return JSON {"items": [{"index": <n>, ...fields}]} for exactly the indices shown. '
        "If an item genuinely does not contain a field, use null — never invent a value."
    )
    lines.append("")
    lines.append("Items:")
    for idx, text in items:
        lines.append(f"[index {idx}] {text}")
    return "\n".join(lines)


async def llm_fill_missing(page: Page, config: dict, data: Any) -> Any:
    """Fill null fields in list-mode extraction results via the LLM. Returns
    data unchanged on any error or when nothing needs filling."""
    if not _llm_configured():
        return data
    if config.get("mode") != "list" or not isinstance(data, list) or not data:
        return data  # single-mode / empty handled by the deterministic path

    fields = config.get("fields") or []
    field_names = [f["name"] for f in fields]
    targets = [n for n in field_names if any(row.get(n) is None for row in data)]
    if not targets:
        return data

    try:
        texts = await _item_texts(page, config["root"])
        # Only send items that actually have a gap, tagged with their real index.
        pending = [
            (i, texts[i]) for i in range(min(len(data), len(texts)))
            if any(data[i].get(n) is None for n in targets) and texts[i]
        ][:MAX_ITEMS]
        if not pending:
            return data
        if len(data) > MAX_ITEMS:
            log.warning("llm extraction fallback: capped at %s of %s items", MAX_ITEMS, len(data))

        examples = _field_examples(fields, data, targets)
        schema = _build_schema(targets)
        system = (
            "You extract structured field values from short HTML list-item texts. "
            "Return only valid JSON matching the schema. Never fabricate values."
        )
        user = _build_user_prompt(targets, examples, pending)

        async with _LLM_LOCK:
            out = await complete_json(system, user, schema, max_tokens=min(4000, 200 * len(pending) + 500))

        by_index = {it.get("index"): it for it in out.get("items", []) if isinstance(it, dict)}
        transforms = {f["name"]: f.get("transform") for f in fields}
        for i, row in enumerate(data):
            filled = by_index.get(i)
            if not filled:
                continue
            for n in targets:
                if row.get(n) is None and filled.get(n) is not None:
                    row[n] = _apply_transform(filled[n], transforms.get(n))
        return data
    except Exception as exc:  # never let the fallback break a working replay
        log.warning("llm extraction fallback failed: %s", exc)
        return data
