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

  // A sample of repeated content blocks, so the agent can see what data the
  // page holds and pick an extraction target, not just what it can click.
  const blocks = [];
  const counts = new Map();
  for (const el of document.querySelectorAll('li,article,tr,[class*=item],[class*=card],[class*=product]')) {
    if (!visible(el)) continue;
    const key = el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(/\s+/)[0];
    counts.set(key, (counts.get(key) || 0) + 1);
    if (counts.get(key) <= 3) {
      const i = window.__abRefs.length;
      window.__abRefs.push(el);
      blocks.push(`[ref_${i}] (${key}) ${el.innerText.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ')}`);
    }
  }

  return {
    url: location.href,
    title: document.title,
    interactive: lines.join('\n'),
    blocks: blocks.join('\n'),
    refCount: window.__abRefs.length,
  };
})()
