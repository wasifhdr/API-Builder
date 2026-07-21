"""Compile-time selector generation.

At pick time the worker has the exact element the user marked (stamped with
data-ab-pick). This module asks a multimodal LLM to produce ROBUST selectors for
that element from its DOM outline + a screenshot crop, validates every candidate
against the live DOM, and returns a ranked, validated list. At replay time,
`reheal` re-derives a selector from the field description/example when all stored
selectors have broken. Nothing here ever raises — callers get selectors or a
fallback/None."""

import base64
import logging

from playwright.async_api import Page

from app.llm.client import complete_json
from app.recorder.llm_extract import _LLM_LOCK, _llm_configured

log = logging.getLogger("recorder")

MAX_SELECTORS = 3
# Thinking models (Google-served Gemma) spend ~400-600 tokens in a <thought>
# block BEFORE emitting the JSON; 400 total truncated it mid-thought, so every
# call fell back to positional selectors. Give ample headroom for thought + JSON.
COMPILE_MAX_TOKENS = 1500

_SYSTEM = (
    "You write robust CSS selectors for a specific web element. Prefer stable "
    "anchors (data-testid, semantic ids, meaningful class names, ARIA) over "
    "positional nth-of-type paths. Return only valid JSON."
)

_SELECTOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selectors"],
    "properties": {"selectors": {"type": "array", "items": {"type": "string"}}},
}


def _outline_text(outline: list[dict]) -> str:
    lines = []
    for i, n in enumerate(outline):
        label = "element" if i == 0 else f"ancestor[{i}]"
        bits = [f"<{n.get('tag', '')}>"]
        if n.get("id"):
            bits.append(f"id={n['id']}")
        if n.get("classes"):
            bits.append("class=" + ".".join(n["classes"]))
        for k, v in (n.get("data") or {}).items():
            bits.append(f"{k}={v}")
        if n.get("role"):
            bits.append(f"role={n['role']}")
        if n.get("aria"):
            bits.append(f"aria-label={n['aria']}")
        if n.get("text"):
            bits.append(f'text="{n["text"]}"')
        lines.append(f"{label}: " + " ".join(bits))
    return "\n".join(lines)


async def _screenshot_b64(page: Page, rect: dict | None) -> list[str]:
    if not rect or rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
        return []
    try:
        clip = {
            "x": max(0, float(rect["x"])),
            "y": max(0, float(rect["y"])),
            "width": float(rect["width"]),
            "height": float(rect["height"]),
        }
        png = await page.screenshot(clip=clip)
        return [base64.b64encode(png).decode()]
    except Exception as exc:
        log.warning("selector-compiler screenshot failed: %s", exc)
        return []


async def _validate_single(page: Page, pick_id: str, selector: str) -> bool:
    js = """
    (cfg) => {
      let el = null;
      try { el = document.querySelector(cfg.sel); } catch (e) { return false; }
      return !!el && el.getAttribute('data-ab-pick') === cfg.pid;
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "pid": pick_id}))
    except Exception:
        return False


async def _validate_relative(page: Page, pick_id: str, root: str | None, selector: str) -> bool:
    js = """
    (cfg) => {
      const stamped = document.querySelector('[data-ab-pick="' + cfg.pid + '"]');
      if (!stamped) return false;
      const row = cfg.root ? stamped.closest(cfg.root) : stamped.parentElement;
      if (!row) return false;
      let el = null;
      try { el = row.querySelector(cfg.sel); } catch (e) { return false; }
      return el === stamped;
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "pid": pick_id, "root": root or ""}))
    except Exception:
        return False


async def _validate(page: Page, pick_id: str, mode: str, root: str | None, selector: str) -> bool:
    if not selector:
        return False
    if mode == "list":
        return await _validate_relative(page, pick_id, root, selector)
    return await _validate_single(page, pick_id, selector)


def _field_prompt(field: dict, outline: list[dict], mode: str, root: str | None) -> str:
    scope = (
        f"The element lives inside a list row matched by `{root}`; produce a "
        "selector RELATIVE to that row (it will be run as row.querySelector)."
        if mode == "list"
        else "Produce a selector run as document.querySelector on the whole page."
    )
    return (
        f"Field: {field.get('name', '')}\n"
        f"Meaning: {field.get('description') or '(no description)'}\n"
        f"Example value: {field.get('example') or '(none)'}\n\n"
        f"{scope}\n\n"
        f"DOM outline (element first, then ancestors):\n{_outline_text(outline)}\n\n"
        f"Return up to {MAX_SELECTORS} candidate selectors, best (most robust) first."
    )


async def compile_from_pick(
    page: Page, pick_ctx: dict, *, mode: str, root: str | None, field: dict
) -> list[str]:
    pick_id = pick_ctx.get("pick_id") or ""
    heuristics = list(pick_ctx.get("selectors") or [])
    ranked: list[str] = []

    if _llm_configured():
        try:
            images = await _screenshot_b64(page, pick_ctx.get("rect"))
            user = _field_prompt(field, pick_ctx.get("outline") or [], mode, root)
            async with _LLM_LOCK:
                out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=COMPILE_MAX_TOKENS, images=images)
            for sel in out.get("selectors") or []:
                if isinstance(sel, str) and await _validate(page, pick_id, mode, root, sel):
                    ranked.append(sel)
        except Exception as exc:
            log.warning("compile_from_pick LLM step failed: %s", exc)

    # Append validated heuristic candidates as lower-ranked fallbacks.
    for sel in heuristics:
        if sel not in ranked and await _validate(page, pick_id, mode, root, sel):
            ranked.append(sel)

    # Last resort: the raw heuristic list (unvalidated) so authoring never yields
    # an empty selector set — the field still works on the recorded page.
    if not ranked:
        ranked = heuristics

    return ranked[:MAX_SELECTORS]


async def compile_root_from_pick(page: Page, pick_ctx: dict) -> list[str]:
    generalized = pick_ctx.get("generalized") or ""
    pick_id = pick_ctx.get("pick_id") or ""
    ranked: list[str] = []

    async def _valid_root(sel: str) -> bool:
        js = """
        (cfg) => {
          let nodes = [];
          try { nodes = Array.from(document.querySelectorAll(cfg.sel)); } catch (e) { return false; }
          if (nodes.length < 2) return false;
          const stamped = document.querySelector('[data-ab-pick="' + cfg.pid + '"]');
          return !!stamped && nodes.some((n) => n === stamped || n.contains(stamped));
        }
        """
        try:
            return bool(await page.evaluate(js, {"sel": sel, "pid": pick_id}))
        except Exception:
            return False

    if _llm_configured():
        try:
            user = (
                "Produce robust CSS selectors that match EVERY repeated row of this "
                "list (2+ elements). Prefer a stable class over positional paths.\n\n"
                f"A representative row is described by:\n{_outline_text(pick_ctx.get('outline') or [])}\n\n"
                f"A heuristic guess is `{generalized}`. Return up to {MAX_SELECTORS} candidates, best first."
            )
            images = await _screenshot_b64(page, pick_ctx.get("rect"))
            async with _LLM_LOCK:
                out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=COMPILE_MAX_TOKENS, images=images)
            for sel in out.get("selectors") or []:
                if isinstance(sel, str) and await _valid_root(sel):
                    ranked.append(sel)
        except Exception as exc:
            log.warning("compile_root_from_pick LLM step failed: %s", exc)

    if generalized and generalized not in ranked and await _valid_root(generalized):
        ranked.append(generalized)
    if not ranked and generalized:
        ranked = [generalized]
    return ranked[:MAX_SELECTORS]


async def _page_outline_text(page: Page) -> str:
    # A coarse page description for reheal (no picked element). Reuses the visible
    # text of likely-relevant landmarks; kept small for token budget.
    js = "() => (document.body ? document.body.innerText : '').trim().slice(0, 4000)"
    try:
        return await page.evaluate(js)
    except Exception:
        return ""


async def _reheal_valid(page: Page, mode: str, root: str | None, selector: str) -> bool:
    js = """
    (cfg) => {
      function nonEmpty(el) { return !!el && (el.textContent || '').trim().length > 0; }
      if (cfg.mode === 'list') {
        let row = null;
        try { row = cfg.root ? document.querySelector(cfg.root) : document.body; } catch (e) { return false; }
        if (!row) return false;
        let el = null;
        try { el = row.querySelector(cfg.sel); } catch (e) { return false; }
        return nonEmpty(el);
      }
      let el = null;
      try { el = document.querySelector(cfg.sel); } catch (e) { return false; }
      return nonEmpty(el);
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "mode": mode, "root": root or ""}))
    except Exception:
        return False


async def reheal(page: Page, *, mode: str, root: str | None, field: dict) -> list[str] | None:
    if not _llm_configured():
        return None
    try:
        scope = (
            f"The value lives inside a list row matched by `{root}`; return a selector "
            "relative to that row."
            if mode == "list"
            else "Return a selector run as document.querySelector on the whole page."
        )
        user = (
            f"Field: {field.get('name', '')}\n"
            f"Meaning: {field.get('description') or '(no description)'}\n"
            f"Example value seen before: {field.get('example') or '(none)'}\n\n"
            f"{scope}\n\n"
            f"Page text (truncated):\n{await _page_outline_text(page)}\n\n"
            f"Return up to {MAX_SELECTORS} candidate CSS selectors, best first."
        )
        async with _LLM_LOCK:
            out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=COMPILE_MAX_TOKENS)
        validated = [
            sel
            for sel in (out.get("selectors") or [])
            if isinstance(sel, str) and await _reheal_valid(page, mode, root, sel)
        ]
        return validated[:MAX_SELECTORS] or None
    except Exception as exc:
        log.warning("reheal failed: %s", exc)
        return None
