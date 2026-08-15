import logging
import re

from playwright.async_api import Page

from app.agent.observe import RefNotFound, resolve_ref
from app.recorder.selector_compiler import compile_from_pick, compile_root_from_pick

log = logging.getLogger("agent")

_BUILD_PICK_RESULT_JS = "el => window.__abBuildPickResult ? window.__abBuildPickResult(el) : null"

_ELEMENT_FACTS_JS = """el => ({
  href: el.getAttribute('href'),
  src: el.getAttribute('src') || el.getAttribute('data-src'),
  text: (el.textContent || '').trim(),
})"""

# The takes app.recorder.extraction's takeValue actually implements.
VALID_TAKES = {"text", "html", "attr:href", "attr:src"}

_URL_FIELD_RE = re.compile(r"(^|_)(url|link|href|permalink)($|_)", re.I)
_IMAGE_FIELD_RE = re.compile(r"(image|img|photo|picture|thumb|logo)", re.I)


class ExtractionError(Exception):
    """The marked elements could not be turned into an extraction config."""


def normalize_marks(marks: list) -> list[dict]:
    """Accepts both the bare-ref marks the driver used to produce and the
    {ref, field, take} dicts it produces now, so a caller (or an older test)
    passing plain strings keeps working."""
    out: list[dict] = []
    for mark in marks or []:
        if isinstance(mark, str):
            out.append({"ref": mark})
        elif isinstance(mark, dict) and mark.get("ref"):
            out.append(dict(mark))
    return out


def normalize_field_name(name: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def assign_marks(field_marks: list[dict], declared: list[dict] | None) -> dict[str, dict]:
    """Maps each declared field name to the mark that supplies its value.

    A mark naming its field binds to that field; the match is loose because the
    model routinely answers with `product_title` or `Price` for a field
    declared as `title`/`price`, and an exact-match-only map would treat every
    one of those as unnamed. A `field` matching nothing declared is demoted to
    unnamed rather than dropped, and unnamed marks fill the remaining fields in
    the order the drive prompt asks for them — which is exactly the positional
    behaviour that existed before marks could carry a name at all.

    Shared with tools.unmarked_fields so the gate on `done` and the extraction
    builder can never disagree about which fields are covered.
    """
    by_norm = {normalize_field_name(d["name"]): d["name"] for d in declared or []}
    named: dict[str, dict] = {}
    unnamed: list[dict] = []
    for mark in field_marks:
        target = by_norm.get(normalize_field_name(mark.get("field"))) if mark.get("field") else None
        if target and target not in named:
            named[target] = mark
        else:
            unnamed.append(mark)

    leftovers = iter(unnamed)
    assigned: dict[str, dict] = {}
    for decl in declared or []:
        mark = named.get(decl["name"]) or next(leftovers, None)
        if mark is not None:
            assigned[decl["name"]] = mark
    return assigned


def resolve_take(field_name: str, requested: object, facts: dict) -> str:
    """What to read off a marked element.

    An explicit, valid `take` from the model always wins. Otherwise it is
    inferred from the element, because `take: "text"` for every field is how a
    `url` field marked on an <a> came back as the link's LABEL, and an
    `image_url` marked on an <img> came back as the empty string — an <img> has
    no text node at all, so the whole field extracted as null.
    """
    if isinstance(requested, str) and requested in VALID_TAKES:
        return requested
    href, src = facts.get("href"), facts.get("src")
    if _URL_FIELD_RE.search(field_name) and href:
        return "attr:href"
    if _IMAGE_FIELD_RE.search(field_name) and src:
        return "attr:src"
    # Nothing readable as text: an element with only an attribute to give is
    # far likelier to be the intended value than a guaranteed-null text node.
    if not facts.get("text"):
        if src:
            return "attr:src"
        if href:
            return "attr:href"
    return "text"


async def element_facts(page: Page, ref: str) -> dict:
    try:
        element = await resolve_ref(page, ref)
        return await page.evaluate(_ELEMENT_FACTS_JS, element) or {}
    except Exception:  # noqa: BLE001 - take inference must never fail the build
        return {}


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


async def build_extraction(page: Page, marks: list, plan: dict) -> dict:
    """Turns the agent's marked elements into an extraction config.

    marks[0] is the repeating container; marks[1:] are individual field
    elements, bound to plan["fields"] by assign_marks. Each field needs its
    OWN pick context, not the row's: the compiler validates a field candidate
    by walking up from the marked element to its containing row
    (selector_compiler._validate_relative), so a pick_id that IS the row can
    never resolve inside itself — reusing the row's context for every field
    would produce zero valid selectors for all of them, not merely worse ones.

    A field with no corresponding mark is left with no selectors rather than
    guessed at, so a genuinely missing field surfaces as a verify failure
    (fields_present) instead of shipping silently broken data.
    """
    marks = normalize_marks(marks)
    if not marks:
        raise ExtractionError("agent finished with no extraction target marked")

    root_ctx = await pick_context_for_ref(page, marks[0]["ref"])
    roots = await compile_root_from_pick(page, root_ctx)
    mode = "list" if roots else "single"
    root = roots[0] if roots else None

    field_marks = marks[1:]
    declared = plan.get("fields") or []

    # Purely positional matching silently mislabelled every column whenever the
    # model marked fields in a different order than they were declared.
    assigned = assign_marks(field_marks, declared)

    fields = []
    for decl in declared:
        field = {"name": decl["name"], "take": "text"}
        mark = assigned.get(decl["name"])
        if mark is not None:
            facts = await element_facts(page, mark["ref"])
            field["take"] = resolve_take(decl["name"], mark.get("take"), facts)
            field_ctx = await pick_context_for_ref(page, mark["ref"])
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
