(() => {
  if (window.__abInjected) return;
  window.__abInjected = true;

  window.__abMode = 'record';
  window.__abBusy = false;
  let overlayEl = null;
  let busyEl = null;

  function hideOverlay() {
    if (overlayEl) overlayEl.style.display = 'none';
  }

  window.__abSetMode = (mode) => {
    window.__abMode = mode;
    if (mode !== 'pick') hideOverlay();
  };

  // --- busy lock: the window is frozen while the worker runs a compile ---
  //
  // Compiling a selector is a multi-second LLM round-trip against a snapshot of
  // the current DOM. Anything the user does meanwhile either lands in the step
  // list as an interaction they didn't mean to record, or navigates/mutates the
  // page out from under the compiler so the returned selector no longer
  // matches. A full-viewport shield swallows pointer events before the page
  // sees them; keys are cancelled in the capture phase.

  function ensureBusyShield() {
    if (busyEl) return busyEl;
    busyEl = document.createElement('div');
    busyEl.style.position = 'fixed';
    busyEl.style.inset = '0';
    busyEl.style.zIndex = '2147483647';
    busyEl.style.cursor = 'wait';
    busyEl.style.background = 'rgba(17, 17, 17, 0.25)';
    busyEl.style.transition = 'opacity 150ms ease';
    busyEl.style.display = 'none';
    busyEl.style.alignItems = 'flex-start';
    busyEl.style.justifyContent = 'center';
    const label = document.createElement('div');
    label.textContent = 'Compiling selector — the page is locked for a moment…';
    label.style.margin = '16px';
    label.style.padding = '8px 14px';
    label.style.borderRadius = '999px';
    label.style.background = '#111';
    label.style.color = '#fff';
    label.style.font = '13px/1.4 system-ui, sans-serif';
    busyEl.appendChild(label);
    document.documentElement.appendChild(busyEl);
    return busyEl;
  }

  // The shield goes up invisible and only dims once the compiler has taken its
  // screenshot of the picked element — dimming first would tint the very image
  // the model reasons about. opacity (not display/visibility) so the shield is
  // hit-testable the whole time: it swallows clicks while still invisible.
  window.__abDimBusyShield = () => {
    if (busyEl) busyEl.style.opacity = '1';
  };

  window.__abSetBusy = (busy) => {
    window.__abBusy = !!busy;
    const shield = ensureBusyShield();
    shield.style.opacity = '0';
    shield.style.display = window.__abBusy ? 'flex' : 'none';
    if (window.__abBusy) {
      hideOverlay();
      // The shield only stops the mouse; a focused input would still take keys.
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    }
  };

  // passive:false — wheel/touch listeners on document default to passive, and a
  // passive listener can't preventDefault, so the page would still scroll.
  for (const type of ['keydown', 'keypress', 'keyup', 'wheel', 'touchstart', 'contextmenu']) {
    document.addEventListener(type, (e) => {
      if (!window.__abBusy) return;
      e.preventDefault();
      e.stopPropagation();
    }, { capture: true, passive: false });
  }

  // Looks generated: hex/uuid-ish or purely numeric ids, e.g. "a1b2c3d4" or
  // "3f9e8d7c-....". Real app ids ("search-input") pass through untouched.
  const GENERATED_ID_RE = /^[a-f0-9]{8,}$|^\d+$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  // A class is worth pinning a selector to only if it reads like an authored,
  // semantic name. CSS-in-JS class names carry a build hash that changes on
  // every deploy ("sc-b5df6b60-0", "ldgsgc", "bycWPN"), so a selector built on
  // one works today and breaks silently after the site ships. Requiring at
  // least one -/_ separator and all-lowercase is what reliably separates
  // "results-section-exact-match-btn" from the hashes.
  const STABLE_CLASS_RE = /^[a-z][a-z0-9]*(?:[-_]+[a-z0-9]+)+$/;
  const HASHED_CLASS_PREFIX_RE = /^(?:sc|css|jsx|emotion)-/;

  // How far above the element we'll look for a stable container to anchor a
  // positional path to. Beyond the contiguous window the path is joined with a
  // descendant combinator, so the intervening levels don't have to be spelled.
  const ANCHOR_LOOKUP_LEVELS = 8;

  // Attribute values land inside "..." — a quote or backslash would otherwise
  // produce a selector that doesn't parse.
  function escapeAttr(value) {
    return String(value).replace(/(["\\])/g, '\\$1');
  }

  // A container is a usable anchor if it identifies itself the same way we'd
  // pick a selector for it: testid first, then a non-generated id.
  function anchorFor(node) {
    const testid = node.getAttribute ? node.getAttribute('data-testid') : '';
    if (testid) return `[data-testid="${escapeAttr(testid)}"]`;
    const id = node.id;
    if (id && !GENERATED_ID_RE.test(id)) return `#${id}`;
    return '';
  }

  // A bare positional path floats: "div:nth-of-type(1) > div > div > a" matches
  // anywhere in the document, so on a real page it selects dozens of nodes
  // (nav drawers, footers) and replay binds to whichever comes first. Anchoring
  // the path at the nearest identifiable ancestor confines it to that container.
  function cssPath(el, maxLevels = 4) {
    const parts = [];
    let node = el;
    for (let level = 0; level < ANCHOR_LOOKUP_LEVELS; level++) {
      if (!node || node.nodeType !== 1 || node === document.body) break;
      if (level > 0) {
        const anchor = anchorFor(node);
        if (anchor && parts.length) {
          // Within the contiguous window every level is spelled out, so the
          // anchor is the direct parent; past it, levels were skipped.
          const combinator = level <= maxLevels ? ' > ' : ' ';
          return anchor + combinator + parts.join(' > ');
        }
      }
      if (level < maxLevels) {
        let part = node.tagName.toLowerCase();
        const parent = node.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
          if (siblings.length > 1) {
            part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
          }
        }
        parts.unshift(part);
      }
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  // A click usually lands on an inner icon/span/svg; the meaningfully
  // selectable control is the nearest interactive ancestor. Resolving to it
  // before ranking records the control's aria-label/role/id rather than a
  // decorative leaf's fragile positional css path.
  const INTERACTIVE_SELECTOR = [
    'button', 'a[href]', 'input', 'select', 'textarea', 'summary', 'label',
    '[role="button"]', '[role="link"]', '[role="tab"]', '[role="menuitem"]',
    '[role="option"]', '[role="checkbox"]', '[role="radio"]',
    '[onclick]', '[tabindex]',
  ].join(', ');

  function interactiveTarget(el) {
    if (!(el instanceof Element)) return el;
    return el.closest(INTERACTIVE_SELECTOR) || el;
  }

  const MAX_CLASS_PARTS = 4;
  const MAX_TEXT_SELECTOR_LEN = 40;

  function stableClasses(el) {
    return Array.from(el.classList || [])
      .filter((c) => STABLE_CLASS_RE.test(c) && !HASHED_CLASS_PREFIX_RE.test(c) && !GENERATED_ID_RE.test(c));
  }

  // Uniqueness buckets. A selector that resolves to exactly one node beats a
  // "better-looking" attribute that resolves to thirty: replay binds to a
  // single element, so an ambiguous selector doesn't fail loudly, it silently
  // acts on the wrong node (or on a hidden copy in a collapsed menu).
  const UNIQUE = 0;
  const UNKNOWN = 1;
  const AMBIGUOUS = 2;

  function selectorRank(sel, el) {
    let nodes;
    try {
      nodes = document.querySelectorAll(sel);
    } catch {
      // Playwright-only engine (:has-text) — not countable here, and not
      // countable by the browser at replay time either. Treat as a middle bet.
      return UNKNOWN;
    }
    // A candidate that doesn't select the very element it was derived from is
    // broken (bad escaping, a class the element doesn't really carry).
    if (!Array.from(nodes).includes(el)) return null;
    return nodes.length === 1 ? UNIQUE : AMBIGUOUS;
  }

  // Best-first by identity: [data-testid] -> #id (skip generated-looking ids)
  // -> [name] -> role+aria-label -> stable class -> href -> visible text ->
  // anchored CSS path. That order is then re-sorted so unambiguous candidates
  // come first. Top 3 kept; replay tries them in order.
  function rankSelectors(el) {
    const tag = el.tagName.toLowerCase();
    const raw = [];

    const testid = el.getAttribute('data-testid');
    if (testid) raw.push(`[data-testid="${escapeAttr(testid)}"]`);

    const id = el.id;
    if (id && !GENERATED_ID_RE.test(id)) raw.push(`#${id}`);

    const name = el.getAttribute('name');
    if (name) raw.push(`[name="${escapeAttr(name)}"]`);

    const role = el.getAttribute('role');
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) {
      // A native control (<button>, <a>) has an implicit ARIA role but no
      // literal role attribute, so gating on role would drop a perfectly
      // stable aria-label selector. Tag-qualify it when there's no role.
      raw.push(role
        ? `[role="${escapeAttr(role)}"][aria-label="${escapeAttr(ariaLabel)}"]`
        : `${tag}[aria-label="${escapeAttr(ariaLabel)}"]`);
    }

    // Sites that ship no testid/aria at all still tend to name the one class
    // that says what the control is ("results-section-exact-match-btn"). Scan
    // every stable class for one that already pins the element down — the
    // telling name is often buried behind a pile of styling classes.
    const classes = stableClasses(el);
    if (classes.length) {
      const unique = classes
        .map((c) => `${tag}.${c}`)
        .find((sel) => selectorRank(sel, el) === UNIQUE);
      raw.push(unique || `${tag}.${classes.slice(0, MAX_CLASS_PARTS).join('.')}`);
    }

    // Text is the thing a human would use to find the control, and it survives
    // markup reshuffles that break every structural selector. Only for controls
    // (a :has-text on a <div> would match every wrapper up the tree).
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text && text.length <= MAX_TEXT_SELECTOR_LEN && el.matches && el.matches(INTERACTIVE_SELECTOR)) {
      raw.push(`${tag}:has-text("${escapeAttr(text)}")`);
    }

    raw.push(cssPath(el));

    // Last deliberately, even though it's usually unique: a recorded href tends
    // to embed the values typed during recording (?q=obsession), so it goes
    // stale the moment the workflow runs with different parameters. Useful as a
    // final fallback, never as the selector we lead with.
    const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
    if (href && href !== '#' && !href.toLowerCase().startsWith('javascript:')) {
      raw.push(`a[href="${escapeAttr(href)}"]`);
    }

    const scored = [];
    for (const sel of raw) {
      if (!sel || scored.some((s) => s.sel === sel)) continue;
      const rank = selectorRank(sel, el);
      if (rank === null) continue;
      scored.push({ sel, rank, order: scored.length });
    }
    scored.sort((a, b) => a.rank - b.rank || a.order - b.order);

    const out = scored.slice(0, 3).map((s) => s.sel);
    return out.length ? out : [cssPath(el)];
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

  // Pressing Enter in a form field implicitly submits the form, and the browser
  // fires a synthetic click on the default submit button (sites that intercept
  // Enter and call `button.click()` themselves produce the same thing). That
  // click is the *activation* of the Enter press, not a second interaction:
  // recording both makes replay run the search twice, and the replayed click
  // lands on the results page where the button is gone or means something else.
  // Such clicks carry no pointer (detail 0, screen coords 0), so a
  // keyboard-driven click shortly after a recorded Enter press is dropped.
  const KEY_ACTIVATION_WINDOW_MS = 1000;
  let lastEnterPressAt = 0;

  document.addEventListener('click', (e) => {
    if (window.__abMode !== 'record' || window.__abBusy) return;
    if (!(e.target instanceof Element)) return;
    const pointerDriven = e.detail > 0 || e.screenX !== 0 || e.screenY !== 0;
    if (!pointerDriven && Date.now() - lastEnterPressAt < KEY_ACTIVATION_WINDOW_MS) return;
    const el = interactiveTarget(e.target);
    emit({ type: 'click', selectors: rankSelectors(el) });
  }, true);

  document.addEventListener('input', (e) => {
    if (window.__abMode !== 'record' || window.__abBusy) return;
    const el = e.target;
    if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return;

    clearTimeout(fillTimers.get(el));
    const timer = setTimeout(() => {
      emit({ type: 'fill', selectors: rankSelectors(el), value: el.value });
    }, 400);
    fillTimers.set(el, timer);
  }, true);

  document.addEventListener('keydown', (e) => {
    if (window.__abMode !== 'record' || window.__abBusy) return;
    if (e.key !== 'Enter' && e.key !== 'Tab') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    // Only Enter activates a control; Tab just moves focus, so a keyboard click
    // after Tab (e.g. Space on a button) is a real interaction worth recording.
    if (e.key === 'Enter') lastEnterPressAt = Date.now();
    emit({ type: 'press', selectors: rankSelectors(el), key: e.key });
  }, true);

  document.addEventListener('change', (e) => {
    if (window.__abMode !== 'record' || window.__abBusy) return;
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
    if (window.__abMode !== 'pick' || window.__abBusy) return;
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
    if (window.__abMode !== 'pick' || window.__abBusy) return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    e.preventDefault();
    e.stopPropagation();

    const pickId = `p${++__abPickCounter}`;
    el.setAttribute('data-ab-pick', pickId);

    const selectors = rankSelectors(el);
    // Generalize the positional path specifically — ranking may no longer leave
    // it last (or include it at all) now that candidates are sorted by how
    // unambiguous they are.
    const generalized = stripLastNthOfType(cssPath(el));
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
