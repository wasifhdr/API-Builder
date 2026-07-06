import re

from bs4.element import Tag

# Mirrors the ranking in injected.js so the algorithm is unit-testable without
# a browser. Keep the two in sync when the ranking changes.
GENERATED_ID_RE = re.compile(
    r"^[a-f0-9]{8,}$|^\d+$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def css_path(el: Tag, max_levels: int = 4) -> str:
    parts: list[str] = []
    node: Tag | None = el
    for _ in range(max_levels):
        if node is None or not isinstance(node, Tag) or node.name == "body":
            break
        part = node.name
        parent = node.parent
        if isinstance(parent, Tag):
            siblings = [c for c in parent.find_all(node.name, recursive=False)]
            if len(siblings) > 1:
                part += f":nth-of-type({siblings.index(node) + 1})"
        parts.insert(0, part)
        node = parent
    return " > ".join(parts)


def rank_selectors(el: Tag) -> list[str]:
    candidates: list[str] = []

    testid = el.get("data-testid")
    if testid:
        candidates.append(f'[data-testid="{testid}"]')

    el_id = el.get("id")
    if el_id and not GENERATED_ID_RE.match(el_id):
        candidates.append(f"#{el_id}")

    name = el.get("name")
    if name:
        candidates.append(f'[name="{name}"]')

    role = el.get("role")
    aria_label = el.get("aria-label")
    if role and aria_label:
        candidates.append(f'[role="{role}"][aria-label="{aria_label}"]')

    candidates.append(css_path(el))

    return candidates[:3]
