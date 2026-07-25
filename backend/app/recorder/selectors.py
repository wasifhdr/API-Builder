import re

import soupsieve as sv
from bs4.element import Tag

# Mirrors the ranking in injected.js so the algorithm is unit-testable without
# a browser. Keep the two in sync when the ranking changes.
GENERATED_ID_RE = re.compile(
    r"^[a-f0-9]{8,}$|^\d+$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# A class is worth pinning a selector to only if it reads like an authored,
# semantic name. CSS-in-JS class names carry a build hash that changes on every
# deploy ("sc-b5df6b60-0", "ldgsgc", "bycWPN"), so a selector built on one works
# today and breaks silently after the site ships. Requiring at least one -/_
# separator and all-lowercase is what reliably separates
# "results-section-exact-match-btn" from the hashes.
STABLE_CLASS_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_]+[a-z0-9]+)+$")
HASHED_CLASS_PREFIX_RE = re.compile(r"^(?:sc|css|jsx|emotion)-")

# How far above the element we'll look for a stable container to anchor a
# positional path to. Beyond the contiguous window the path is joined with a
# descendant combinator, so the intervening levels don't have to be spelled.
ANCHOR_LOOKUP_LEVELS = 8

MAX_CLASS_PARTS = 4
MAX_TEXT_SELECTOR_LEN = 40

INTERACTIVE_SELECTOR = ", ".join(
    [
        "button",
        "a[href]",
        "input",
        "select",
        "textarea",
        "summary",
        "label",
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[role="checkbox"]',
        '[role="radio"]',
        "[onclick]",
        "[tabindex]",
    ]
)

# Uniqueness buckets. A selector that resolves to exactly one node beats a
# "better-looking" attribute that resolves to thirty: replay binds to a single
# element, so an ambiguous selector doesn't fail loudly, it silently acts on the
# wrong node (or on a hidden copy in a collapsed menu).
UNIQUE = 0
UNKNOWN = 1
AMBIGUOUS = 2


def _escape_attr(value: str) -> str:
    """Attribute values land inside "..."; a quote/backslash would break parsing."""
    return re.sub(r'(["\\])', r"\\\1", str(value))


def _root(el: Tag):
    root = el
    for parent in el.parents:
        root = parent
    return root


def _anchor_for(node: Tag) -> str:
    """A container is a usable anchor if it identifies itself the way we'd pick
    a selector for it: testid first, then a non-generated id."""
    testid = node.get("data-testid")
    if testid:
        return f'[data-testid="{_escape_attr(testid)}"]'
    node_id = node.get("id")
    if node_id and not GENERATED_ID_RE.match(node_id):
        return f"#{node_id}"
    return ""


def css_path(el: Tag, max_levels: int = 4) -> str:
    """A bare positional path floats: "div:nth-of-type(1) > div > div > a"
    matches anywhere in the document, so on a real page it selects dozens of
    nodes (nav drawers, footers) and replay binds to whichever comes first.
    Anchoring the path at the nearest identifiable ancestor confines it."""
    parts: list[str] = []
    node: Tag | None = el
    for level in range(ANCHOR_LOOKUP_LEVELS):
        if node is None or not isinstance(node, Tag) or node.name == "body":
            break
        if level > 0:
            anchor = _anchor_for(node)
            if anchor and parts:
                # Within the contiguous window every level is spelled out, so
                # the anchor is the direct parent; past it, levels were skipped.
                combinator = " > " if level <= max_levels else " "
                return anchor + combinator + " > ".join(parts)
        if level < max_levels:
            part = node.name
            parent = node.parent
            if isinstance(parent, Tag):
                siblings = list(parent.find_all(node.name, recursive=False))
                if len(siblings) > 1:
                    part += f":nth-of-type({siblings.index(node) + 1})"
            parts.insert(0, part)
        node = node.parent if isinstance(node.parent, Tag) else None
    return " > ".join(parts)


def _stable_classes(el: Tag) -> list[str]:
    classes = el.get("class") or []
    return [
        c
        for c in classes
        if STABLE_CLASS_RE.match(c)
        and not HASHED_CLASS_PREFIX_RE.match(c)
        and not GENERATED_ID_RE.match(c)
    ]


def _selector_rank(sel: str, el: Tag) -> int | None:
    try:
        nodes = _root(el).select(sel)
    except Exception:
        # Playwright-only engine (:has-text) — not countable here, and not
        # countable by the browser at replay time either. A middle bet.
        return UNKNOWN
    # A candidate that doesn't select the very element it was derived from is
    # broken (bad escaping, a class the element doesn't really carry).
    if not any(n is el for n in nodes):
        return None
    return UNIQUE if len(nodes) == 1 else AMBIGUOUS


def rank_selectors(el: Tag) -> list[str]:
    """Best-first by identity: [data-testid] -> #id (skip generated-looking ids)
    -> [name] -> role+aria-label -> stable class -> href -> visible text ->
    anchored CSS path. That order is then re-sorted so unambiguous candidates
    come first. Top 3 kept; replay tries them in order."""
    tag = el.name
    raw: list[str] = []

    testid = el.get("data-testid")
    if testid:
        raw.append(f'[data-testid="{_escape_attr(testid)}"]')

    el_id = el.get("id")
    if el_id and not GENERATED_ID_RE.match(el_id):
        raw.append(f"#{el_id}")

    name = el.get("name")
    if name:
        raw.append(f'[name="{_escape_attr(name)}"]')

    role = el.get("role")
    aria_label = el.get("aria-label")
    if aria_label:
        # A native control (<button>, <a>) has an implicit ARIA role but no
        # literal role attribute, so gating on role would drop a perfectly
        # stable aria-label selector. Tag-qualify it when there's no role.
        if role:
            raw.append(f'[role="{_escape_attr(role)}"][aria-label="{_escape_attr(aria_label)}"]')
        else:
            raw.append(f'{tag}[aria-label="{_escape_attr(aria_label)}"]')

    # Sites that ship no testid/aria at all still tend to name the one class
    # that says what the control is ("results-section-exact-match-btn"). Scan
    # every stable class for one that already pins the element down — the
    # telling name is often buried behind a pile of styling classes.
    classes = _stable_classes(el)
    if classes:
        unique = next(
            (s for s in (f"{tag}.{c}" for c in classes) if _selector_rank(s, el) == UNIQUE),
            None,
        )
        raw.append(unique or f"{tag}." + ".".join(classes[:MAX_CLASS_PARTS]))

    # Text is the thing a human would use to find the control, and it survives
    # markup reshuffles that break every structural selector. Only for controls
    # (a :has-text on a <div> would match every wrapper up the tree).
    text = " ".join((el.get_text() or "").split())
    if text and len(text) <= MAX_TEXT_SELECTOR_LEN and sv.match(INTERACTIVE_SELECTOR, el):
        raw.append(f'{tag}:has-text("{_escape_attr(text)}")')

    raw.append(css_path(el))

    # Last deliberately, even though it's usually unique: a recorded href tends
    # to embed the values typed during recording (?q=obsession), so it goes
    # stale the moment the workflow runs with different parameters. Useful as a
    # final fallback, never as the selector we lead with.
    href = el.get("href") or "" if tag == "a" else ""
    if href and href != "#" and not href.lower().startswith("javascript:"):
        raw.append(f'a[href="{_escape_attr(href)}"]')

    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for sel in raw:
        if not sel or sel in seen:
            continue
        rank = _selector_rank(sel, el)
        if rank is None:
            continue
        seen.add(sel)
        scored.append((rank, len(scored), sel))
    scored.sort(key=lambda s: (s[0], s[1]))

    out = [sel for _, _, sel in scored[:3]]
    return out or [css_path(el)]
