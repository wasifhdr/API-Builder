// Builds a compact, interactive-first view of the page for an LLM agent and
// parks the matching element references on window.__abRefs so the driver can
// act on them WITHOUT tagging the DOM (attributes would pollute the recorder's
// generated selectors for these elements).
(() => {
  const INTERACTIVE = 'a,button,input,select,textarea,[role=button],[role=link],[role=tab],[onclick],[contenteditable=true]';
  const MAX_TEXT = 120;

  window.__abRefs = [];

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };

  const label = (el) => {
    const parts = [
      el.getAttribute('aria-label'),
      el.getAttribute('placeholder'),
      el.getAttribute('name'),
      el.getAttribute('title'),
      el.value,
      el.innerText,
    ];
    for (const p of parts) {
      if (p && p.trim()) return p.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ');
    }
    return '';
  };

  const lines = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (!visible(el)) continue;
    const i = window.__abRefs.length;
    window.__abRefs.push(el);
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type');
    lines.push(`[ref_${i}] <${tag}${type ? ' type=' + type : ''}> ${label(el)}`);
  }

  // An element counts as a field LEAF if it carries its own direct text and
  // has no element child that itself carries text — the actual node a
  // selector should target (a title span), not a wrapping container.
  const MAX_LEAVES_PER_BLOCK = 6;
  const isTextLeaf = (el) => {
    const ownText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join(' ').trim();
    if (!ownText) return false;
    return !Array.from(el.children).some((c) => c.innerText && c.innerText.trim());
  };

  // A sample of repeated content blocks, so the agent can see what data the
  // page holds and pick an extraction target. Each block ALSO gets its own
  // field-leaf refs (title, price, ...) exposed underneath it: the selector
  // compiler validates a field candidate by walking UP from the marked
  // element to its containing row, so a field's pick context must be the
  // field's own element, not the row — marking only the row produces a
  // pick_id that never resolves inside itself, and every field selector
  // fails validation.
  const blocks = [];
  const counts = new Map();
  for (const el of document.querySelectorAll('li,article,tr,[class*=item],[class*=card],[class*=product]')) {
    if (!visible(el)) continue;
    const key = el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(/\s+/)[0];
    counts.set(key, (counts.get(key) || 0) + 1);
    if (counts.get(key) > 3) continue;

    const blockRef = window.__abRefs.length;
    window.__abRefs.push(el);
    blocks.push(`[ref_${blockRef}] (${key}) ${el.innerText.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ')}`);

    let leafCount = 0;
    const walk = (node) => {
      for (const child of node.children) {
        if (leafCount >= MAX_LEAVES_PER_BLOCK) return;
        if (!visible(child)) continue;
        if (isTextLeaf(child)) {
          const leafRef = window.__abRefs.length;
          window.__abRefs.push(child);
          const text = child.innerText.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ');
          blocks.push(`  [ref_${leafRef}] (field inside ref_${blockRef}) ${text}`);
          leafCount++;
        } else {
          walk(child);
        }
      }
    };
    walk(el);
  }

  return {
    url: location.href,
    title: document.title,
    interactive: lines.join('\n'),
    blocks: blocks.join('\n'),
    refCount: window.__abRefs.length,
  };
})()
