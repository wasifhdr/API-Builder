(() => {
  if (window.__abInjected) return;
  window.__abInjected = true;

  window.__abMode = 'record';
  let overlayEl = null;

  function hideOverlay() {
    if (overlayEl) overlayEl.style.display = 'none';
  }

  window.__abSetMode = (mode) => {
    window.__abMode = mode;
    if (mode !== 'pick') hideOverlay();
  };

  // Looks generated: hex/uuid-ish or purely numeric ids, e.g. "a1b2c3d4" or
  // "3f9e8d7c-....". Real app ids ("search-input") pass through untouched.
  const GENERATED_ID_RE = /^[a-f0-9]{8,}$|^\d+$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function cssPath(el, maxLevels = 4) {
    const parts = [];
    let node = el;
    for (let i = 0; i < maxLevels && node && node.nodeType === 1 && node !== document.body; i++) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  // Best-first: [data-testid] -> #id (skip generated-looking ids) -> [name] ->
  // role+aria-label -> trimmed CSS path. Top 3 kept; replay tries them in order.
  function rankSelectors(el) {
    const candidates = [];

    const testid = el.getAttribute('data-testid');
    if (testid) candidates.push(`[data-testid="${testid}"]`);

    const id = el.id;
    if (id && !GENERATED_ID_RE.test(id)) candidates.push(`#${id}`);

    const name = el.getAttribute('name');
    if (name) candidates.push(`[name="${name}"]`);

    const role = el.getAttribute('role');
    const ariaLabel = el.getAttribute('aria-label');
    if (role && ariaLabel) candidates.push(`[role="${role}"][aria-label="${ariaLabel}"]`);

    candidates.push(cssPath(el));

    return candidates.slice(0, 3);
  }

  // For "select similar": strips the trailing :nth-of-type(n) from the last
  // path segment so e.g. "ul > li:nth-of-type(3)" generalizes to "ul > li",
  // which then matches every item in the list, not just the clicked one.
  function stripLastNthOfType(path) {
    const parts = path.split(' > ');
    parts[parts.length - 1] = parts[parts.length - 1].replace(/:nth-of-type\(\d+\)$/, '');
    return parts.join(' > ');
  }

  function emit(event) {
    if (window.__abEmit) window.__abEmit(event);
  }

  // --- record mode: click / fill (debounced) / press Enter|Tab / select ---

  const fillTimers = new WeakMap();

  document.addEventListener('click', (e) => {
    if (window.__abMode !== 'record') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    emit({ type: 'click', selectors: rankSelectors(el) });
  }, true);

  document.addEventListener('input', (e) => {
    if (window.__abMode !== 'record') return;
    const el = e.target;
    if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return;

    clearTimeout(fillTimers.get(el));
    const timer = setTimeout(() => {
      emit({ type: 'fill', selectors: rankSelectors(el), value: el.value });
    }, 400);
    fillTimers.set(el, timer);
  }, true);

  document.addEventListener('keydown', (e) => {
    if (window.__abMode !== 'record') return;
    if (e.key !== 'Enter' && e.key !== 'Tab') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    emit({ type: 'press', selectors: rankSelectors(el), key: e.key });
  }, true);

  document.addEventListener('change', (e) => {
    if (window.__abMode !== 'record') return;
    const el = e.target;
    if (!(el instanceof HTMLSelectElement)) return;
    emit({ type: 'select_option', selectors: rankSelectors(el), value: el.value });
  }, true);

  // --- pick mode: hover overlay + click captures the element instead of acting on it ---

  function ensureOverlay() {
    if (overlayEl) return overlayEl;
    overlayEl = document.createElement('div');
    overlayEl.style.position = 'fixed';
    overlayEl.style.pointerEvents = 'none';
    overlayEl.style.border = '2px solid #3b82f6';
    overlayEl.style.background = 'rgba(59, 130, 246, 0.15)';
    overlayEl.style.zIndex = '2147483647';
    overlayEl.style.display = 'none';
    document.documentElement.appendChild(overlayEl);
    return overlayEl;
  }

  document.addEventListener('mouseover', (e) => {
    if (window.__abMode !== 'pick') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    const rect = el.getBoundingClientRect();
    const overlay = ensureOverlay();
    overlay.style.display = 'block';
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
  }, true);

  document.addEventListener('mouseout', () => {
    if (window.__abMode !== 'pick') return;
    hideOverlay();
  }, true);

  let __abPickCounter = 0;

  // Compact, LLM-friendly description of an element and its ancestors. Drops
  // generated-looking ids/classes so the model anchors on stable attributes.
  function describeNode(el) {
    const classes = Array.from(el.classList || [])
      .filter((c) => !GENERATED_ID_RE.test(c))
      .slice(0, 6);
    const data = {};
    for (const attr of Array.from(el.attributes || [])) {
      if (attr.name.startsWith('data-') && attr.name !== 'data-ab-pick') {
        data[attr.name] = attr.value.slice(0, 40);
      }
    }
    const id = el.id && !GENERATED_ID_RE.test(el.id) ? el.id : '';
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      id,
      classes,
      data,
      role: el.getAttribute ? (el.getAttribute('role') || '') : '',
      aria: el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
      text: (el.textContent || '').trim().slice(0, 80),
    };
  }

  function buildOutline(el, maxLevels = 5) {
    const outline = [];
    let node = el;
    for (let i = 0; i < maxLevels && node && node.nodeType === 1 && node !== document.body; i++) {
      outline.push(describeNode(node));
      node = node.parentElement;
    }
    return outline;
  }

  document.addEventListener('click', (e) => {
    if (window.__abMode !== 'pick') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    e.preventDefault();
    e.stopPropagation();

    const pickId = `p${++__abPickCounter}`;
    el.setAttribute('data-ab-pick', pickId);

    const selectors = rankSelectors(el);
    const generalized = stripLastNthOfType(selectors[selectors.length - 1]);
    let count = 1;
    try {
      count = document.querySelectorAll(generalized).length;
    } catch {
      count = 1;
    }
    const rect = el.getBoundingClientRect();
    const preview = (el.textContent || '').trim().slice(0, 200);
    emit({
      type: 'pick_result',
      pickId,
      selectors,
      preview,
      count,
      generalized,
      outline: buildOutline(el),
      rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
    });
  }, true);
})();
