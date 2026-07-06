(() => {
  if (window.__abInjected) return;
  window.__abInjected = true;

  window.__abMode = 'record';
  window.__abSetMode = (mode) => {
    window.__abMode = mode;
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
})();
