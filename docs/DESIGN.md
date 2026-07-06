# API Builder — Visual Design System ("Warm Editorial")

**Status:** approved design contract for the frontend. Implement it as written; when a value you
need is missing, use the nearest token in this file — never invent new hex values, font sizes, or
shadows. If anything here conflicts with `CLAUDE.md`, `CLAUDE.md` wins.

**Provenance:** adapted from a style extraction of a slide deck
(`career-progression-in-the-ai-era-DESIGN.md` in the repo root — reference only, do not implement
from it) plus screenshots of that deck. Presentation-scale values have been rescaled for an app;
a danger red, a mono font, small type steps, and status semantics were added deliberately. This
file supersedes the extraction.

**The look in one paragraph:** warm printed-paper editorial. Parchment surfaces (`cream` shell,
`paper` cards), one dominant foreground (`ink` — a deep espresso used for text, borders, and dark
chrome), terracotta `orange` as the single action accent. Depth comes from **hard-offset shadows
(no blur)** — cards look like paper stacked on paper. Big statements are set in **Source Serif 4**
with exactly one accent-colored phrase; everything else is **Fira Sans**; code and identifiers are
**Fira Mono**. Marketing surfaces (Landing, Docs, empty states) are loud; data surfaces (admin,
tables, logs) use the same palette quietly.

---

## 1. Setup

Frontend stack (from CLAUDE.md): React + Vite + **Tailwind v4, CSS-first** — all tokens live in
`frontend/src/index.css` inside `@theme`. There is **no** `tailwind.config.js`; do not create one.

### 1.1 Fonts

```powershell
cd frontend; npm i @fontsource-variable/source-serif-4 @fontsource/fira-sans @fontsource/fira-mono
```

Add to the top of `frontend/src/main.tsx` (before `./index.css`):

```ts
import '@fontsource-variable/source-serif-4/wght.css'
import '@fontsource/fira-sans/400.css'
import '@fontsource/fira-sans/500.css'
import '@fontsource/fira-sans/700.css'
import '@fontsource/fira-sans/800.css'
import '@fontsource/fira-mono/400.css'
import '@fontsource/fira-mono/700.css'
```

If a package name fails to resolve, fall back to a Google Fonts `<link>` in `index.html` for the
same families/weights. Note: the source deck also used Inter for nav chrome — **deliberately
dropped**; Fira Sans 700/800 covers those roles. Do not add Inter.

### 1.2 Theme tokens — replace `frontend/src/index.css` with exactly this

```css
@import "tailwindcss";

@theme {
  /* Fonts */
  --font-display: "Source Serif 4 Variable", "Source Serif 4", Georgia, serif;
  --font-sans: "Fira Sans", system-ui, sans-serif;
  --font-mono: "Fira Mono", ui-monospace, Consolas, monospace;

  /* Palette */
  --color-paper: #fffdf8;        /* card / page surface (lightest warm white) */
  --color-cream: #faf3e7;        /* app-shell background, inset surfaces, tints */
  --color-ink: #3d3229;          /* text, borders, dark chrome — the dominant foreground */
  --color-sand: #cfcabe;         /* muted borders, dividers, disabled */
  --color-orange: #c2703d;       /* brand accent / primary action ONLY (never errors) */
  --color-orange-deep: #8a4a22;  /* orange for small text on light bg; orange hover */
  --color-gold: #e9b44c;         /* pending / warning / active indicator on dark chrome */
  --color-gold-deep: #8a6420;    /* gold for small text on light bg */
  --color-green: #6b8f71;        /* success / published / verified */
  --color-green-deep: #43614a;   /* green for small text on light bg */
  --color-blue: #5b7b9a;         /* info / running / GET / pro tier */
  --color-blue-deep: #3d5a75;    /* blue for small text on light bg */
  --color-purple: #8a7ba8;       /* max tier accent */
  --color-purple-deep: #5f527e;  /* purple for small text on light bg */
  --color-red: #a8443a;          /* danger / failed / destructive (added — not in deck) */
  --color-red-deep: #7c2f27;     /* red hover / red text on tints */
  /* Light variants — ONLY as text on ink surfaces (log feeds, dark panels) */
  --color-green-soft: #a3c4aa;
  --color-red-soft: #e0a49b;
  --color-blue-soft: #a9c4dd;

  /* Radii */
  --radius-dot: 6px;             /* tiny elements: inline code, grid cells, checkboxes */
  --radius-control: 12px;        /* buttons, inputs, selects, callouts */
  --radius-card: 16px;           /* cards, tables, code blocks, panels */
  --radius-card-lg: 20px;        /* modals, hero cards */
  --radius-pill: 999px;          /* badges, nav pills, progress tracks */

  /* Hard-offset shadows — NEVER blurred, always ink */
  --shadow-offset-sm: 2px 2px 0 0 #3d3229;
  --shadow-offset: 3px 3px 0 0 #3d3229;
  --shadow-offset-lg: 6px 6px 0 0 #3d3229;

  /* Display type (font-display only) */
  --text-display: 3.5rem;
  --text-display--line-height: 1.02;
  --text-display--letter-spacing: -0.015em;
  --text-display--font-weight: 800;
  --text-display-sm: 2.25rem;
  --text-display-sm--line-height: 1.08;
  --text-display-sm--letter-spacing: -0.01em;
  --text-display-sm--font-weight: 800;

  /* Headings (font-sans) */
  --text-h1: 1.75rem;
  --text-h1--line-height: 1.15;
  --text-h1--font-weight: 700;
  --text-h2: 1.3125rem;
  --text-h2--line-height: 1.25;
  --text-h2--font-weight: 700;
  --text-h3: 1.0625rem;
  --text-h3--line-height: 1.3;
  --text-h3--font-weight: 700;

  /* Caps label — badges, eyebrows, table headers (always with `uppercase`) */
  --text-label: 0.72rem;
  --text-label--line-height: 1.2;
  --text-label--letter-spacing: 0.08em;
  --text-label--font-weight: 800;
}

/* Dot-grid paper texture — Landing hero / marketing bands only, never data screens */
@utility bg-dotgrid {
  background-image: radial-gradient(
    color-mix(in srgb, var(--color-ink) 10%, transparent) 1px,
    transparent 1px
  );
  background-size: 22px 22px;
}

body {
  @apply bg-cream font-sans text-ink antialiased;
}
```

Body text sizes reuse Tailwind defaults: `text-base` (16px) prose, `text-[15px]` default UI/body,
`text-sm` (14px) secondary + table cells, `text-xs` (12px) captions, `text-[13px]` mono/code.

---

## 2. Color rules

### 2.1 Semantic map — statuses across the app MUST use these

| Meaning | Color | App examples |
|---|---|---|
| Primary action / brand / links | `orange` | Publish button, Start recording, active tab, link text (≥15px bold) |
| Success / done / positive | `green` | published API, verified payment, replay succeeded, 2xx badge |
| Pending / warning / attention | `gold` | pending trx, near-quota, draft-in-review, active nav indicator |
| Failure / danger / destructive | `red` | failed replay, rejected trx, revoke/delete buttons, 4xx/5xx, at-limit |
| Info / running / neutral-active | `blue` | running job, GET method badge, pro tier |
| Max tier | `purple` | max-tier badge, max plan card accent |
| Muted / draft / disabled | `sand` + `ink/40–70` | draft badge, dividers, secondary text |

HTTP method badges: GET=`blue`, POST=`green`, PUT/PATCH=`gold`, DELETE=`red`.
Tier badges: free=neutral(sand), pro=`blue`, max=`purple`.
**Never use orange for errors or warnings** — orange means "act here."

### 2.2 Contrast rules (checked; do not re-derive)

- `ink` on `paper`/`cream` ≈ 11:1 — the default pairing, always safe.
- Mid-tone accents (`orange` `green` `blue` `gold` `purple`) on light backgrounds are ~3–3.7:1:
  **allowed only** for borders, fills, icons, large text (≥24px, or ≥19px bold), and decorative
  accent phrases in display headlines.
- Any colored text **below 19px on a light background uses the `-deep` variant** (all ≥4.5:1).
- White text is allowed on `red`, `ink`, and on `orange` only for button labels ≥15px bold.
- On `ink` surfaces: text is `paper`/`cream` (muted: `cream/70`); status text uses `gold`,
  `green-soft`, `red-soft`, `blue-soft`; never use mid-tone accents as text on ink.
- Secondary text = `text-ink/70`, tertiary/placeholder = `text-ink/45`. No grays — never use
  Tailwind `gray-*`/`slate-*`/`zinc-*` anywhere.

---

## 3. Typography rules

| Role | Classes | Use |
|---|---|---|
| Hero statement | `font-display text-display` | Landing hero only |
| Section statement | `font-display text-display-sm` | Landing sections, empty states, big moments |
| Page title | `text-h1` | One per page, top of content |
| Card/section title | `text-h2` | Card headings, panel titles |
| Sub-heading | `text-h3` | Sub-sections, modal titles |
| Body | `text-[15px]` / `text-base` | Default UI text / docs prose |
| Secondary | `text-sm text-ink/70` | Descriptions, meta, table cells |
| Caption | `text-xs text-ink/60` | Timestamps, footnotes, source lines |
| Caps label | `text-label uppercase` | Eyebrows, badges, table headers, form labels |
| Code / IDs | `font-mono text-[13px]` | keys, URLs, JSON, trx IDs, money amounts, counts |

- **The accent-phrase move (signature):** display headlines are `ink` with exactly ONE phrase
  wrapped in `text-orange` (or `text-green` on alternating sections). Never two accents in one
  headline. Example: `<h1 class="font-display text-display">Turn any website <span class="text-orange">into an API.</span></h1>`
- **Serif is display-only.** Never `font-display` for body, buttons, tables, or headings below
  `display-sm` — except the tiny brand wordmark in the nav.
- **Eyebrow pattern:** caps label above a page/section title, colored `text-orange-deep` (or
  `-deep` of the section's accent): `<p class="text-label uppercase text-orange-deep mb-2">Billing</p>`
- Numbers that align in columns (money, counts, quotas): add `font-mono tabular-nums`.

---

## 4. Surfaces, borders, elevation, interaction

**Surface stack:** `cream` app background → `paper` cards/panels → `ink` chrome (top nav, code
blocks, stat chips). Cards never sit on cards; use quiet dividers inside a card instead.

**Border rule:** interactive or anchoring elements get `border-2 border-ink`; supporting/dense
containers get `border border-sand`. Decorative accent edges: `border-l-4 border-l-{accent}`
(callouts) or `border-t-4 border-t-{accent}` (plan/feature cards).

**Elevation (hard offsets, never blur):**

| Token | Use |
|---|---|
| `shadow-offset-sm` | small controls: sm buttons, chips |
| `shadow-offset` | buttons, standard cards |
| `shadow-offset-lg` | hero/feature cards, modals, toasts |
| none | quiet cards, tables, anything inside a card, everything on `ink` surfaces |

**Interaction physics (buttons and clickable cards):**

- Hover: background shifts one step (`paper→cream`, `orange→orange-deep`, `red→red-deep`,
  `ink→ink/85`); clickable cards additionally lift: `hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-offset-lg`.
- Press: element collapses into the page — translate by its shadow offset and drop it:
  `active:translate-x-[3px] active:translate-y-[3px] active:shadow-none`.
- Focus: `focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2`
  (on ink surfaces: `focus-visible:outline-gold`). Every interactive element gets this.
- Disabled: `disabled:pointer-events-none disabled:border-ink/30 disabled:bg-sand/40 disabled:text-ink/50 disabled:shadow-none`.
- Transitions: `transition-[transform,box-shadow,background-color] duration-100` — snappy, no
  slow eases, no scale animations.

The only blur allowed anywhere: modal overlay `backdrop-blur-[2px]`.

---

## 5. Component recipes

Build these once as shared components in `frontend/src/components/ui/` (one file per component or
a single `ui.tsx` — implementer's choice) and use them everywhere. Class strings below are the
spec; variant props map to them.

### Button

```
base:    inline-flex items-center justify-center gap-2 rounded-control border-2 border-ink
         font-bold transition-[transform,box-shadow,background-color] duration-100
         focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2
         active:translate-x-[3px] active:translate-y-[3px] active:shadow-none
         disabled:pointer-events-none disabled:border-ink/30 disabled:bg-sand/40
         disabled:text-ink/50 disabled:shadow-none
size md: px-4 py-2 text-[15px] shadow-offset          size sm: px-3 py-1.5 text-sm shadow-offset-sm
default: bg-paper text-ink hover:bg-cream
primary: bg-orange text-white hover:bg-orange-deep    ← ONE per screen, the main action
ink:     bg-ink text-paper hover:bg-ink/85            ← strong secondary; buttons on dark chrome
danger:  bg-red text-white hover:bg-red-deep          ← revoke, delete, reject, stop
ghost:   border-transparent shadow-none bg-transparent text-ink/70 hover:bg-cream hover:text-ink
         (ghost has no shadow/press physics; danger-ghost: text-red-deep hover:bg-red/10)
```

Primary buttons keep labels ≥15px bold (white-on-orange is borderline AA — weight + ink border
carry it; never smaller). Small primary actions use the `ink` variant instead.

### Badge (status + tier)

```
base:     inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-label uppercase
neutral:  border-sand bg-cream text-ink/70            (draft, free tier, unknown)
success:  border-green/40 bg-green/10 text-green-deep
pending:  border-gold/50 bg-gold/15 text-gold-deep
failed:   border-red/40 bg-red/10 text-red-deep
info:     border-blue/40 bg-blue/10 text-blue-deep    (running, GET, pro tier)
purple:   border-purple/40 bg-purple/10 text-purple-deep  (max tier)
```

Live/recording indicator: prepend `<span class="size-2 rounded-pill bg-red animate-pulse" />`.

### CapsLabel (eyebrow)

`<p class="text-label uppercase text-orange-deep">…</p>` — accent `-deep` colors or `text-ink/60`.

### Card

```
feature:  rounded-card border-2 border-ink bg-paper p-6 shadow-offset-lg   (hero moments, 1–2/screen)
standard: rounded-card border-2 border-ink bg-paper p-5 shadow-offset      (dashboard cards)
clickable: standard + block transition-[transform,box-shadow] duration-100
           hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-offset-lg
quiet:    rounded-card border border-sand bg-paper p-5                     (admin, settings, dense)
callout:  rounded-control border border-sand border-l-4 border-l-gold bg-cream p-4
          (instructions/notes; left-border color = semantic; starts with a CapsLabel)
plan:     feature/standard + border-t-4 border-t-blue (pro) / border-t-purple (max)
```

### StatChip (inverse stat display — deck signature)

```
<div class="inline-flex flex-col gap-0.5 rounded-card bg-ink px-5 py-3 text-paper">
  <span class="text-2xl font-extrabold tabular-nums leading-none">265,660</span>
  <span class="text-xs font-bold text-cream/70">calls this month</span>
</div>
```

### Form field

```
label:    mb-1.5 block text-label uppercase text-ink/70
input:    w-full rounded-control border-2 border-ink bg-paper px-3.5 py-2 text-[15px]
          placeholder:text-ink/45 focus-visible:outline-[3px] focus-visible:outline-ink
          focus-visible:outline-offset-2 disabled:border-ink/30 disabled:bg-cream disabled:text-ink/50
error:    input + border-red focus-visible:outline-red; message: mt-1 text-xs font-medium text-red-deep
help:     mt-1 text-xs text-ink/60
select/textarea: same as input; checkbox: size-4 rounded-dot border-2 border-ink accent-orange
```

### Table (all admin/data tables)

```
wrapper:  overflow-x-auto rounded-card border border-sand bg-paper
table:    w-full text-sm
thead th: border-b-2 border-ink px-3 py-2 text-left text-label uppercase text-ink/60
tbody tr: border-b border-sand last:border-0 hover:bg-cream/60
td:       px-3 py-2.5   (ids/keys/money/timestamps: font-mono text-[13px] tabular-nums)
```

Empty table body: single full-width cell, `py-8 text-center text-sm text-ink/60`.

### Modal

```
overlay: fixed inset-0 z-50 grid place-items-center bg-ink/50 p-4 backdrop-blur-[2px]
panel:   w-full max-w-md rounded-card-lg border-2 border-ink bg-paper p-6 shadow-offset-lg
title:   text-h3 (or text-h2)  · actions row: mt-6 flex justify-end gap-3 (cancel=default, confirm=primary/danger)
```

### Toast

`fixed bottom-4 right-4 z-50 w-80 rounded-card border-2 border-ink bg-paper p-4 shadow-offset-lg`
+ `border-l-4 border-l-green|gold|red` by severity. Title `text-sm font-bold`, body `text-sm text-ink/70`.

### CodeBlock (curl examples, JSON responses, spec)

```
container: overflow-hidden rounded-card border-2 border-ink bg-ink
header:    flex items-center justify-between border-b border-cream/15 px-4 py-2
           lang: text-label uppercase text-cream/60
           copy: rounded-dot border border-cream/30 px-2 py-1 text-xs font-bold text-cream
                 hover:bg-cream/10 focus-visible:outline-gold
pre:       overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-cream
```

Inline code / key display: `rounded-dot border border-sand bg-cream px-1.5 py-0.5 font-mono text-[0.9em]`.

### Quota display (segmented cells — deck signature, use for daily attempt quota)

```
row:     flex items-center gap-3
cells:   flex flex-wrap gap-1.5
cell:    size-5 rounded-dot border   unused: border-sand bg-cream
         used: border-green-deep/30 bg-green   (ALL used cells flip to border-red-deep/30 bg-red at limit)
caption: font-mono text-sm tabular-nums text-ink/70  ("7 / 10 today")
```

Unlimited quota: no cells, just `text-sm text-ink/60` "Unlimited". Continuous meters (storage
etc.): track `h-2 w-full max-w-xs rounded-pill border border-sand bg-cream`, fill `h-full
rounded-pill bg-green` (`bg-gold` ≥80%, `bg-red` at 100%).

### Live event feed (RecorderSession)

```
panel: h-72 overflow-y-auto rounded-card border-2 border-ink bg-ink p-3 font-mono text-xs
       leading-relaxed text-cream/90
line:  timestamp text-cream/50 · event-type prefix: navigation=text-blue-soft, click/input=text-gold,
       extraction-mark=text-green-soft, error=text-red-soft
```

### Empty state

```
rounded-card border-2 border-dashed border-sand bg-cream/50 p-10 text-center
+ font-display text-display-sm statement with one orange phrase, text-sm text-ink/70 line, primary Button
```

### Loading

Spinner: `size-5 animate-spin rounded-pill border-2 border-sand border-t-orange`.
Skeleton: `animate-pulse rounded-control bg-cream`.

---

## 6. App shell & layout

One shared `AppShell` (create it; adopt on every authed page — pages currently render their own
headers, remove those). Top bar is **ink** (the deck's dark chrome):

```
header:  sticky top-0 z-40 bg-ink text-paper
inner:   mx-auto flex h-14 max-w-6xl items-center justify-between px-6
brand:   font-display text-lg font-extrabold tracking-tight text-paper   ("API Builder")
links:   rounded-pill px-3 py-1.5 text-sm font-bold text-cream/75 hover:bg-paper/10 hover:text-paper
active:  bg-paper/10 text-gold
right:   user email text-xs text-cream/60 · tier Badge (solid on dark: bg-gold text-ink for pro,
         bg-purple text-paper for max, bg-cream/20 text-cream for free) · logout ghost
         (text-cream/75 hover:bg-paper/10 hover:text-paper focus-visible:outline-gold)
```

Content: `mx-auto max-w-6xl px-6 py-8`. Page header pattern: optional eyebrow CapsLabel,
`text-h1`, optional `text-sm text-ink/70` subline, actions right-aligned; `mb-8`.
Section gaps `space-y-8`; card grids `grid gap-5 md:grid-cols-2 lg:grid-cols-3`.
Admin pages keep the existing `AdminNav` but restyled as pill tabs: active
`rounded-pill bg-ink px-3 py-1.5 text-sm font-bold text-paper`, inactive
`text-ink/70 hover:bg-cream`.

Responsive: desktop-first (recorder requires desktop). One breakpoint of care: ≤900px stacks
grids to one column and the nav collapses to brand + logout. Don't invest beyond that.

---

## 7. Page-by-page guide

| Page | Tone | Specifics |
|---|---|---|
| Landing | LOUD | `bg-cream bg-dotgrid` hero; display headline with orange accent phrase; feature cards `border-t-4` rotating green/orange/blue; ink footer band (`bg-ink text-cream`, serif brand); one primary CTA ("Sign in with Google") |
| Dashboard | medium | Page header + quota **segmented cells**; API cards = clickable cards: name `text-h2`, method Badge + path in inline-code, status Badge, owner/shared CapsLabel; serif empty state |
| RecorderStart | medium | Centered feature card: URL input + primary "Start recording"; gold callout explaining the headful browser flow |
| RecorderSession | medium | Status row (recording Badge with pulsing dot, StatChip step count); **live event feed** panel; danger "Stop & save" |
| WorkflowEditor | quiet-med | Form fields; params as neutral Badges + `font-mono`; steps in a Table; primary "Save" |
| ApiDetail | medium | Header: name + status Badge + primary "Publish" (or ink "Unpublish"); endpoint URL inline-code + copy sm button; StatChip row (calls today, quota left); params Table; test panel: fields + CodeBlock response |
| ApiDocs | docs | Serif `text-display-sm` title; prose `text-base leading-relaxed`; CodeBlocks for curl/responses; method Badges per endpoint; quiet cards per section |
| Keys | quiet | Table (mono key prefixes); create = primary; reveal/copy = ghost sm; revoke = danger-ghost sm + confirm Modal |
| Billing | med-loud | 3 plan cards: free=quiet, pro=`border-t-blue`, max=`border-t-purple` (feature variant); price `font-display text-display-sm` + `font-mono` BDT; current plan = gold "CURRENT" Badge; bKash steps in gold callout; trx-ID form field + primary submit; history Table with pending/verified/rejected Badges |
| Settings | quiet | Quiet cards per section; form fields; danger zone: quiet card + `border-red/40` + danger buttons |
| InviteAccept | medium | Single centered feature card; serif statement; primary "Accept invite" |
| AdminUsers / AdminTransactions / AdminSms | QUIET | Dense Tables (`text-sm`, mono ids/amounts); filters as sm inputs; verify=primary sm, reject=danger sm; counts as StatChips; no serif, no dotgrid, no feature cards |

---

## 8. Do / Don't

| Do | Don't |
|---|---|
| One orange primary action per screen | Orange for errors/warnings (red/gold exist) |
| `-deep` variants for colored text <19px on light bg | Mid-tone accent text at small sizes on light bg |
| Hard-offset shadows from the 3 tokens | Any blurred/soft drop shadow (except modal `backdrop-blur-[2px]`) |
| `border-2 border-ink` interactive, `border-sand` static | Shadows on quiet cards, table rows, or anything on ink surfaces |
| Serif for display statements + brand only | Serif body text, serif buttons, serif table content |
| One accent phrase per display headline | Rainbow headlines, two accents in one statement |
| `font-mono tabular-nums` for ids, keys, money, counts | Proportional digits in tables/quotas |
| Radius by role (control 12 / card 16 / pill badges) | Mixing radii within one component class |
| `text-ink/70`, `text-ink/45` for muted text | Any `gray-*`/`slate-*`/`zinc-*`/`text-black`/`bg-white` |
| Reuse `components/ui/` everywhere | Re-hand-rolling per-page button/card/badge classes |

---

## 9. Implementation order & definition of done

Work in this order; keep diffs reviewable (commit per step or per page group):

1. **Tokens**: fonts installed + imported in `main.tsx`; `index.css` replaced with §1.2. App still
   runs (everything default-styled on cream — expected).
2. **UI kit**: `frontend/src/components/ui/` — Button, Badge, CapsLabel, Card, StatChip, Field/
   Input, Table, Modal, Toast (if used), CodeBlock, QuotaCells, Spinner, EmptyState per §5.
3. **Shell**: `AppShell` per §6; adopt on all authed pages; restyle `AdminNav`.
4. **Pages** (loud→quiet): Landing → Dashboard → ApiDetail → ApiDocs → Billing → Keys →
   RecorderStart → RecorderSession → WorkflowEditor → Settings → InviteAccept → Admin×3.
5. **Sweep**: delete dead per-page style constants (e.g. `TIER_STYLES` in Dashboard).

Done when ALL of these hold:

- `grep -rnE "(gray|slate|zinc|stone|neutral)-[0-9]|text-black|bg-white" frontend/src` → **zero hits**
  (`bg-paper`/`text-white` on colored fills are fine; `bg-white` is not).
- Every status color matches the §2.1 semantic map; exactly one orange primary button per screen.
- Every interactive element has visible `focus-visible` outline; disabled states render per §4.
- `cd frontend; npm run build` passes; visual pass at 1280px and ~900px shows no broken layout.
- Fonts actually render (serif hero on Landing, Fira Sans UI, mono code) — check in the browser,
  not just in code.
