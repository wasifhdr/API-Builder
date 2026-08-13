import logging

from playwright.async_api import Page

from app.agent.observe import RefNotFound, resolve_ref
from app.recorder.selector_compiler import compile_from_pick, compile_root_from_pick

log = logging.getLogger("agent")

_BUILD_PICK_RESULT_JS = "el => window.__abBuildPickResult ? window.__abBuildPickResult(el) : null"


class ExtractionError(Exception):
    """The marked elements could not be turned into an extraction config."""


async def pick_context_for_ref(page: Page, ref: str) -> dict:
    """Manufactures pick mode's payload for a ref the agent marked, by calling
    the SAME selector-ranking/outline computation injected.js uses for a human
    pick click (window.__abBuildPickResult, exposed there for exactly this
    reuse) — so an agent's picks are indistinguishable from a human's to the
    selector compiler, and there is no second ranking implementation to drift.
    """
    try:
        element = await resolve_ref(page, ref)
    except RefNotFound as exc:
        raise ExtractionError(str(exc)) from exc

    raw = await page.evaluate(_BUILD_PICK_RESULT_JS, element)
    if not raw:
        raise ExtractionError(
            f"{ref} could not be described for the compiler "
            "(is the recorder script injected on this page?)"
        )
    return {
        "pick_id": raw["pickId"],
        "selectors": raw.get("selectors") or [],
        "preview": raw.get("preview"),
        "generalized": raw.get("generalized"),
        "outline": raw.get("outline") or [],
        "rect": raw.get("rect"),
    }


async def build_extraction(page: Page, marks: list[str], plan: dict) -> dict:
    """Turns the agent's marked elements into an extraction config.

    marks[0] is the repeating container; marks[1:] are individual field
    elements, matched positionally to plan["fields"]. Each field needs its
    OWN pick context, not the row's: the compiler validates a field candidate
    by walking up from the marked element to its containing row
    (selector_compiler._validate_relative), so a pick_id that IS the row can
    never resolve inside itself — reusing the row's context for every field
    would produce zero valid selectors for all of them, not merely worse ones.

    A field with no corresponding mark is left with no selectors rather than
    guessed at, so a genuinely missing field surfaces as a verify failure
    (fields_present) instead of shipping silently broken data.
    """
    if not marks:
        raise ExtractionError("agent finished with no extraction target marked")

    root_ctx = await pick_context_for_ref(page, marks[0])
    roots = await compile_root_from_pick(page, root_ctx)
    mode = "list" if roots else "single"
    root = roots[0] if roots else None

    field_marks = marks[1:]
    declared = plan.get("fields") or []

    fields = []
    for i, decl in enumerate(declared):
        field = {"name": decl["name"], "take": "text"}
        if i < len(field_marks):
            field_ctx = await pick_context_for_ref(page, field_marks[i])
            selectors = await compile_from_pick(page, field_ctx, mode=mode, root=root, field=field)
        else:
            log.info("no mark provided for declared field %s", decl["name"])
            selectors = []
        fields.append({**field, "selectors": selectors})

    if not any(f["selectors"] for f in fields):
        raise ExtractionError("no declared field could be located on the page")

    config: dict = {"mode": mode, "fields": fields, "engine": "compiled"}
    if root:
        config["root"] = root
    return {"main": config}
