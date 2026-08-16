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
  const emitted = new Set();

  const emitBlock = (el, key) => {
    if (emitted.has(el)) return;
    emitted.add(el);
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
  };

  // Repeating rows found STRUCTURALLY: any parent holding 3+ visible children
  // that share a tag+class signature is a list, whatever its markup is called.
  // The tag/class allowlist below cannot find these on its own — waltonbd.com
  // builds each result as `div.single-prodcut` (the site misspells "product"),
  // so `[class*=product]` matched one element on the whole page and the model
  // was offered nothing but nav chrome to mark. Emitted BEFORE the allowlist
  // so the real data outranks menus and pagination in the listing.
  const MIN_GROUP = 3;
  const MIN_ROW_TEXT = 15;
  const MAX_GROUPS = 2;
  const sigOf = (el) =>
    el.tagName.toLowerCase() + '.' +
    (el.className || '').toString().trim().split(/\s+/).filter(Boolean).sort().join('.');

  // Page chrome repeats just as reliably as data does — a mega-menu has 413
  // dropdown entries and a footer has link columns of several hundred
  // characters each, so neither row count nor text length alone separates
  // them from results. Excluding chrome outright is what leaves the result
  // grid as the standout group.
  const CHROME = 'nav,header,footer,[role=navigation],[role=banner],[role=contentinfo],' +
                 '.navbar,.nav,.menu,.dropdown-menu,.breadcrumb,.pagination';
  const isChrome = (el) => el.closest(CHROME) !== null;

  const groups = [];
  for (const parent of document.querySelectorAll('body *')) {
    if (parent.children.length < MIN_GROUP) continue;
    if (isChrome(parent)) continue;
    const bySig = new Map();
    for (const child of parent.children) {
      if (!visible(child) || isChrome(child)) continue;
      const text = (child.innerText || '').trim();
      if (text.length < MIN_ROW_TEXT) continue;
      const sig = sigOf(child);
      if (!bySig.has(sig)) bySig.set(sig, []);
      bySig.get(sig).push(child);
    }
    for (const [sig, members] of bySig) {
      if (members.length < MIN_GROUP) continue;
      const median = members
        .map((m) => (m.innerText || '').trim().length)
        .sort((a, b) => a - b)[Math.floor(members.length / 2)];
      groups.push({ sig, members, median });
    }
  }
  // Most rows first: a result grid has one member per result (20 here), while
  // an incidental repeat outside the chrome — a 3-up promo strip — has few.
  groups.sort((a, b) => b.members.length - a.members.length || b.median - a.median);
  for (const g of groups.slice(0, MAX_GROUPS)) {
    for (const el of g.members.slice(0, 3)) emitBlock(el, g.sig);
  }

  const counts = new Map();
  for (const el of document.querySelectorAll('li,article,tr,[class*=item],[class*=card],[class*=product]')) {
    if (!visible(el)) continue;
    const key = el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(/\s+/)[0];
    counts.set(key, (counts.get(key) || 0) + 1);
    if (counts.get(key) > 3) continue;
    emitBlock(el, key);
  }

  return {
    url: location.href,
    title: document.title,
    interactive: lines.join('\n'),
    blocks: blocks.join('\n'),
    refCount: window.__abRefs.length,
  };
})()
