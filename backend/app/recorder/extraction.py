from typing import Any

from playwright.async_api import Page

# Runs entirely in the page: querying hundreds of list items via CDP
# round-trips would be far slower than one evaluate call.
EXTRACTION_JS = """
(config) => {
  function takeValue(el, take) {
    if (take === 'text') return el.textContent == null ? null : el.textContent.trim();
    if (take === 'html') return el.innerHTML;
    if (take && take.startsWith('attr:')) return el.getAttribute(take.slice(5));
    return null;
  }

  function applyTransform(value, transform) {
    if (value == null) return value;
    if (!transform || transform === 'none') return value;
    if (transform === 'trim') return String(value).trim();
    if (transform === 'number') {
      const cleaned = String(value).replace(/[^0-9.-]/g, '');
      const n = parseFloat(cleaned);
      return Number.isNaN(n) ? null : n;
    }
    if (transform === 'abs_url') {
      try { return new URL(value, window.location.href).href; } catch (e) { return value; }
    }
    return value;
  }

  function extractFields(scope, fields) {
    const obj = {};
    for (const f of fields) {
      const el = scope.querySelector(f.selector);
      let value = el ? takeValue(el, f.take) : null;
      value = applyTransform(value, f.transform);
      obj[f.name] = value;
    }
    return obj;
  }

  if (config.mode === 'single') {
    return extractFields(document, config.fields);
  }
  const roots = Array.from(document.querySelectorAll(config.root));
  return roots.map((root) => extractFields(root, config.fields));
}
"""


async def run_extraction(page: Page, config: dict) -> Any:
    return await page.evaluate(EXTRACTION_JS, config)
