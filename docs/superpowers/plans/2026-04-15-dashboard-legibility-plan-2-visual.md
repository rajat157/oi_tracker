# Dashboard Legibility — Plan 2 of 4: Visual Design System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply Variant A — Cool Professional design language to the existing dashboard. Replaces the current glassmorphism + ad-hoc spacing + emoji-icon mix with a tokenised system: Inter typography, cool-grey palette, 4-pt spacing grid, Lucide iconography, WCAG AA contrast.

**Architecture:** Introduces `static/styles/tokens.css` with CSS custom properties for colour, type, spacing, radius, motion, and elevation. Refactors `static/styles.css` (3107 lines) to consume those tokens. Removes `backdrop-filter: blur(...)` (11 occurrences) in favour of subtle borders + inset highlights. Adds Inter via web font and Lucide via CDN; replaces ~SVG/emoji icon mix selectively (mood faces and V-Shape banner emoji are preserved per spec).

**Crucial constraint:** This plan does NOT change the dashboard's information architecture. Same panels, same data, same layout. Only the visual treatment changes. The novice "What to Watch" tiles + story headline arrive in Plan 3; the panel restructure arrives in Plan 4. Plan 2 ships as standalone visible polish on day one.

**Tech Stack:** CSS custom properties (no preprocessor), Inter web font, Lucide icons (CDN), Chart.js (existing, palette tweaked only). No new build step. No JavaScript framework.

**Reference spec:** `docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md` Section 6 (Visual Design Language) — palette, typography, spacing, radius, elevation, motion, iconography, accessibility.

---

## File Structure

**Create:**
- `static/styles/tokens.css` — CSS custom properties (colour palette, type scale, spacing, radius, motion, elevation tokens)

**Modify:**
- `static/styles.css` — refactor existing rules to consume tokens; remove `backdrop-filter`; tighten typography; clean spacing
- `static/chart.js` — replace hard-coded chart colours with token references (read via `getComputedStyle(document.documentElement)`)
- `templates/dashboard.html` — load Inter web font, load Lucide CDN, link new tokens.css; selectively swap emoji icons to Lucide `<i data-lucide="...">` markers; preserve mood face emojis (🚀 😊 😐 😬 😱) and V-Shape banner emoji
- `templates/trades.html` — same font / Lucide / tokens treatment for the trades page

**Out of scope:**
- Novice view (story headline, 4 tiles, show-more) — Plan 3
- Expert view restructure (panel merges, strike-zone chart consolidation) — Plan 4
- Mobile-first redesign (responsive behaviour preserved, not re-engineered)
- New panels, removed panels, or rewired data sources

---

## Branch

This plan is independent of Plan 1 (different files entirely — Plan 1 was Python backend, Plan 2 is CSS/HTML/icons). Branch from `master`, not from `feat/dashboard-backend`.

```bash
git checkout master
git checkout -b feat/dashboard-visual
```

When merging back: `feat/dashboard-backend` and `feat/dashboard-visual` should merge cleanly side-by-side because they touch disjoint file sets.

---

## Baseline: capture "before" screenshots

- [ ] **Step 0.1: Start the app and screenshot every panel BEFORE any changes**

Run: `uv run python app.py` → open `http://localhost:5000`

Take screenshots of:
- Full dashboard, scrolled top → bottom (3-4 screenshots)
- Trades page (`/trades`)
- Mobile width (Chrome DevTools, 390 × 844)

Save under `docs/superpowers/screenshots/before/` (gitignored). These are reference for visual regression checks at the end. The user does this manually — agents don't need browser access.

- [ ] **Step 0.2: Run the test suite to establish baseline count**

Run: `uv run python -m pytest tests/ -q`
Expected: all tests pass. Record the count. CSS changes should NOT affect any test (Python-only suite).

---

## Task 1: Create tokens.css with the full design system

**Files:**
- Create: `static/styles/tokens.css`

This task introduces the design tokens but doesn't yet apply them. After this commit, the dashboard looks unchanged because nothing references the tokens yet.

- [ ] **Step 1.1: Create the tokens file**

Create `static/styles/tokens.css`:

```css
/* Dashboard Design Tokens — Variant A "Cool Professional"
 * Spec reference: docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md (Section 6.1)
 * All values defined as CSS custom properties on :root for dashboard-wide consumption.
 */

:root {
  /* ===== Colour palette ===== */

  /* Surfaces */
  --color-bg:            #0c0e13;
  --color-bg-raised:     #131721;
  --color-bg-elevated:   #1a2030;   /* hover / focus surface */
  --color-border:        #1e2430;
  --color-border-strong: #253145;

  /* Text */
  --color-text-primary:   #eef1f8;
  --color-text-secondary: #8e94ac;
  --color-text-muted:     #6b7289;
  --color-text-inverse:   #0c0e13;  /* text on accent surfaces */

  /* Semantic accents */
  --color-accent-up:    #60e0a8;   /* bullish / positive */
  --color-accent-dn:    #ff7a82;   /* bearish / negative */
  --color-accent-warn:  #f5b963;   /* caution / forming signal */
  --color-accent-info:  #7cb7ff;   /* neutral info */

  /* Soft variants for backgrounds */
  --color-accent-up-soft:   rgba(96, 224, 168, 0.10);
  --color-accent-dn-soft:   rgba(255, 122, 130, 0.10);
  --color-accent-warn-soft: rgba(245, 185, 99, 0.10);
  --color-accent-info-soft: rgba(124, 183, 255, 0.10);

  /* ===== Typography ===== */
  --font-sans: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Consolas", monospace;

  /* Type scale (px values; derived line-heights) */
  --type-display: 22px;
  --type-h1:      15px;
  --type-body:    13px;
  --type-small:   11px;
  --type-caption: 9px;

  /* Line heights — display tighter, body looser */
  --leading-tight:  1.2;
  --leading-normal: 1.4;
  --leading-loose:  1.55;

  /* Weights */
  --weight-regular: 400;
  --weight-medium:  500;
  --weight-semi:    600;

  /* Letter spacing */
  --tracking-display: -0.02em;
  --tracking-normal:  normal;
  --tracking-label:   0.08em;   /* uppercase eyebrow labels */

  /* ===== Spacing — 4-pt grid ===== */
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-6:  24px;
  --space-8:  32px;
  --space-12: 48px;

  /* ===== Radius ===== */
  --radius-sm: 4px;     /* inputs, small chips */
  --radius-md: 8px;     /* buttons, pill borders */
  --radius-lg: 10px;    /* cards (default) */
  --radius-xl: 14px;    /* hero / modal */
  --radius-pill: 999px; /* fully rounded */

  /* ===== Elevation — replace heavy glassmorphism blur ===== */
  --elev-card-border: 1px solid var(--color-border);
  --elev-card-inset:  0 1px 0 rgba(255, 255, 255, 0.03) inset;
  --elev-card-hover-bg: rgba(255, 255, 255, 0.02);

  /* ===== Motion ===== */
  --motion-fast: 100ms ease-out;
  --motion-default: 150ms ease-out;
  --motion-slow: 300ms ease-out;
  --motion-flash: 500ms ease-out;   /* value-change flash */

  /* ===== Focus ===== */
  --focus-ring-color: var(--color-accent-info);
  --focus-ring-width: 2px;
  --focus-ring-offset: 2px;
}

/* Optional: a "light" variant placeholder, currently unused (deferred per Section 9) */
/* :root[data-theme="light"] { ... } */
```

- [ ] **Step 1.2: Verify the file is well-formed**

Run a quick CSS lint pass via Python to ensure no typos:

```bash
uv run python -c "
import re, pathlib
p = pathlib.Path('static/styles/tokens.css')
src = p.read_text(encoding='utf-8')
# Count :root blocks
roots = re.findall(r':root\s*\{', src)
print('roots:', len(roots))
# Count custom properties
props = re.findall(r'--[a-z-]+:', src)
print('properties:', len(props))
# Sanity: tokens.css must define at least 30 properties
assert len(props) >= 30, f'expected ≥30 tokens, got {len(props)}'
print('OK')
"
```

Expected: `roots: 1`, `properties: 35-50`, `OK`.

- [ ] **Step 1.3: Commit**

```bash
git add static/styles/tokens.css
git commit -m "feat(visual): add design-system tokens (Variant A — Cool Professional)"
```

---

## Task 2: Load tokens.css + Inter web font + Lucide icons in dashboard.html

**Files:**
- Modify: `templates/dashboard.html` (add 3 `<link>`/`<script>` tags in `<head>` and one Lucide init script before `</body>`)

After this commit the dashboard reloads with Inter as the default font (no other rule references it yet, so visual change is mostly the typography shift on un-styled inline text).

- [ ] **Step 2.1: Add font + tokens link in `<head>`**

Find the existing `<link rel="stylesheet" href="{{ url_for('static', filename='styles.css') }}">` line in `templates/dashboard.html`. Above it, add:

```html
<!-- Inter web font (display swap to avoid FOIT) -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">

<!-- Design tokens (must precede styles.css so token references resolve) -->
<link rel="stylesheet" href="{{ url_for('static', filename='styles/tokens.css') }}">
```

- [ ] **Step 2.2: Add Lucide CDN script before `</body>`**

Find the closing `</body>` in `templates/dashboard.html`. Just before it, add:

```html
<!-- Lucide icons — replaces ad-hoc emoji+SVG mix.
     Mood face emojis (🚀😊😐😬😱) and V-Shape banner emoji are preserved as
     intentional spec choices; everywhere else uses <i data-lucide="..."></i>. -->
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
<script>
  // Initial render. After dynamic content updates (SocketIO push), call lucide.createIcons() again.
  if (window.lucide) { lucide.createIcons(); }
</script>
```

- [ ] **Step 2.3: Confirm app still serves the page**

Run: `uv run python app.py`
Then `curl http://localhost:5000/ -o /tmp/dash.html && grep -c "tokens.css\|lucide" /tmp/dash.html`
Expected: ≥3 (tokens.css link + lucide CDN script + lucide.createIcons call).

Stop the app (Ctrl+C in the terminal running it).

- [ ] **Step 2.4: Commit**

```bash
git add templates/dashboard.html
git commit -m "feat(visual): load Inter font, design tokens, Lucide icons in dashboard.html"
```

---

## Task 3: Refactor styles.css — colours

`styles.css` is 3107 lines and uses ~21 hex colour literals plus many `rgba(...)` values. This task replaces them with token references.

**Strategy:** Keep the file structure intact. Only swap colour values and remove `backdrop-filter` lines (Task 4 handles those). Don't touch typography or spacing rules in this task.

- [ ] **Step 3.1: Map every colour literal to a token**

First, enumerate colour usage:

```bash
grep -nE "#[0-9a-fA-F]{3,6}|rgba?\(" static/styles.css > /tmp/colors-before.txt
wc -l /tmp/colors-before.txt
```

Record the count for verification later.

- [ ] **Step 3.2: Replace colours with token references**

Open `static/styles.css`. For each colour literal, decide which token it corresponds to using this mapping table:

| Old value (or close) | New token |
|---|---|
| `#0c0e13`, `#0a0a0f`, dark page-bg | `var(--color-bg)` |
| `#131721`, `#181c25`, card backgrounds | `var(--color-bg-raised)` |
| Borders ~ `#1e2430`, `#21283a`, `rgba(255,255,255,0.08)` | `var(--color-border)` |
| Pills, hover borders ~ `#253145`, `rgba(150,180,255,0.25)` | `var(--color-border-strong)` |
| Body text ~ `#eef1f8`, `#f3f4fa`, `white` | `var(--color-text-primary)` |
| Captions ~ `#8e94ac`, `#a0a8c0` | `var(--color-text-secondary)` |
| Muted/labels ~ `#6b7289`, `#7b8096` | `var(--color-text-muted)` |
| Greens (bull, positive) ~ `#34d399`, `#60e0a8`, `rgba(120,220,120,...)` | `var(--color-accent-up)` |
| Reds (bear, negative) ~ `#ef4444`, `#ff7a82`, `rgba(255,120,120,...)` | `var(--color-accent-dn)` |
| Ambers (warn) ~ `#f59e0b`, `#f5b963`, `rgba(255,200,80,...)` | `var(--color-accent-warn)` |
| Blues (info, links) ~ `#7cb7ff`, `#3b82f6` | `var(--color-accent-info)` |

For nuanced rgba values (e.g. `rgba(96,224,168,0.10)` for soft bullish background), use the `*-soft` token equivalents.

For any colour you can't cleanly map, **leave it and note the line in the commit message** for follow-up.

- [ ] **Step 3.3: Verify visually**

Run the app and load the dashboard. The colours should be visually similar (same intent — bull green, bear red, etc.) but slightly more cohesive (no random hex variations).

Diff the colour literals after:

```bash
grep -nE "#[0-9a-fA-F]{3,6}|rgba?\(" static/styles.css > /tmp/colors-after.txt
diff /tmp/colors-before.txt /tmp/colors-after.txt | head -30
```

Most lines should now reference `var(--color-*)`. Remaining hex literals (if any) should be acceptable explained cases — for example, gradient stops where a single-token reference reads worse than the literal.

- [ ] **Step 3.4: Run tests + commit**

```bash
uv run python -m pytest tests/ -q  # confirm baseline still passes (CSS doesn't affect Python tests)
git add static/styles.css
git commit -m "refactor(visual): replace hard-coded colours with design tokens"
```

---

## Task 4: Remove glassmorphism — replace `backdrop-filter` blur with subtle elevation

**Files:**
- Modify: `static/styles.css`

Spec Section 6.1: "Replace heavy glassmorphism blur with subtle inset highlights." 11 `backdrop-filter` uses currently.

- [ ] **Step 4.1: List all backdrop-filter usages**

```bash
grep -nE "backdrop-filter|--webkit-backdrop-filter" static/styles.css
```

Record the line numbers.

- [ ] **Step 4.2: Replace each with token-based elevation**

For each rule containing `backdrop-filter: blur(...)`, replace as follows:

**Before (typical pattern):**
```css
.card {
  background: rgba(20, 24, 38, 0.7);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
```

**After:**
```css
.card {
  background: var(--color-bg-raised);
  border: var(--elev-card-border);
  box-shadow: var(--elev-card-inset);
}
```

If the rule has a hover state with `backdrop-filter: blur(...)` increased, replace with:

```css
.card:hover {
  background: var(--color-bg-elevated);
  border-color: var(--color-border-strong);
}
```

Remove the `-webkit-backdrop-filter` companion line wherever it appears.

- [ ] **Step 4.3: Verify all blur removed**

```bash
grep -cE "backdrop-filter" static/styles.css
```

Expected: `0`.

Reload the dashboard. Cards should look crisper — solid surfaces with subtle borders, no translucent blur. Some panels may now have less visual depth than before; that's intentional per the spec.

- [ ] **Step 4.4: Commit**

```bash
git add static/styles.css
git commit -m "refactor(visual): remove glassmorphism blur, use subtle elevation tokens"
```

---

## Task 5: Refactor styles.css — typography, spacing, radius

**Files:**
- Modify: `static/styles.css`

- [ ] **Step 5.1: Apply font-family to base elements**

At the top of `static/styles.css`, ensure these base rules use the tokens:

```css
html {
  font-family: var(--font-sans);
  font-size: var(--type-body);
  line-height: var(--leading-normal);
  color: var(--color-text-primary);
  background: var(--color-bg);
}

/* Monospace for tabular numerics (strike tables, flow tables, P&L tables) */
table.numeric, .mono, .tabular-nums {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
```

Find the existing `body { font-family: ... }` rule and update it to use `var(--font-sans)` if it doesn't already inherit from `html`.

- [ ] **Step 5.2: Replace font-size literals with type-scale tokens**

Search for hard-coded `font-size:` values:

```bash
grep -nE "font-size:\s*[0-9]+px" static/styles.css | head -40
```

Map them:
- `22-24px` → `var(--type-display)`
- `15-16px` → `var(--type-h1)`
- `13-14px` → `var(--type-body)` (often this is the body default — may be removed entirely)
- `11-12px` → `var(--type-small)`
- `9-10px` → `var(--type-caption)`

Don't replace mid-rule sizes you don't recognise — leave them and note unresolved cases in the commit message.

- [ ] **Step 5.3: Replace spacing literals with spacing tokens**

Search for `padding:`, `margin:`, `gap:` with px values:

```bash
grep -nE "(padding|margin|gap):\s*[0-9]+px" static/styles.css | head -50
```

Map to the 4-pt grid (`--space-1` through `--space-12`). Only replace values that fit cleanly. Off-grid values (e.g. `7px`, `13px`) should be replaced with the nearest grid value — `7px` → `var(--space-2)` (8px), `13px` → `var(--space-3)` (12px).

Multi-value shorthand:
- `padding: 12px 16px` → `padding: var(--space-3) var(--space-4)`

- [ ] **Step 5.4: Replace border-radius literals**

```bash
grep -nE "border-radius:\s*[0-9]+px" static/styles.css | head -30
```

- `4-6px` → `var(--radius-sm)`
- `7-9px` → `var(--radius-md)`
- `10-12px` → `var(--radius-lg)`
- `13-15px+` → `var(--radius-xl)`
- `999px` or `9999px` (pills) → `var(--radius-pill)`

- [ ] **Step 5.5: Add motion default**

Search for `transition:` rules. Replace timing functions with token defaults:

- `200ms ease`, `0.2s ease` → `var(--motion-default)` (which is 150ms — slightly snappier per spec)
- `300ms ease` → `var(--motion-slow)`
- Long transitions (>500ms) → keep literal but note for review

- [ ] **Step 5.6: Verify and commit**

Run `uv run python -m pytest tests/ -q` (still passes — CSS only).

Reload dashboard. Typography should look more uniform, spacing tighter, animations snappier.

```bash
git add static/styles.css
git commit -m "refactor(visual): apply type, spacing, radius, motion tokens"
```

---

## Task 6: Add focus rings + WCAG AA contrast audit

**Files:**
- Modify: `static/styles.css`

- [ ] **Step 6.1: Add a global focus-ring rule**

Append to `static/styles.css`:

```css
/* WCAG AA — visible focus on all interactive elements */
:focus-visible {
  outline: var(--focus-ring-width) solid var(--focus-ring-color);
  outline-offset: var(--focus-ring-offset);
  border-radius: var(--radius-sm);
}

/* Prevent default browser focus rings (we have our own) */
:focus:not(:focus-visible) {
  outline: none;
}
```

- [ ] **Step 6.2: Audit contrast for body text and captions**

The Variant A palette was designed to pass WCAG AA at the default text-on-bg combinations. Verify by sampling a few key pairs. Run this Python check:

```bash
uv run python -c "
def relative_luminance(r, g, b):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

def contrast(fg, bg):
    L1 = relative_luminance(*fg)
    L2 = relative_luminance(*bg)
    if L1 < L2: L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

pairs = [
    ('text-primary on bg',         '#eef1f8', '#0c0e13'),
    ('text-primary on bg-raised',  '#eef1f8', '#131721'),
    ('text-secondary on bg-raised','#8e94ac', '#131721'),
    ('text-muted on bg-raised',    '#6b7289', '#131721'),
    ('accent-up on bg',            '#60e0a8', '#0c0e13'),
    ('accent-dn on bg',            '#ff7a82', '#0c0e13'),
    ('accent-warn on bg',          '#f5b963', '#0c0e13'),
    ('accent-info on bg',          '#7cb7ff', '#0c0e13'),
]

for label, fg, bg in pairs:
    r = contrast(hex_to_rgb(fg), hex_to_rgb(bg))
    aa_body = 'PASS' if r >= 4.5 else 'FAIL'
    aa_large = 'PASS' if r >= 3.0 else 'FAIL'
    print(f'{label:40s} {r:5.2f}:1   AA-body={aa_body}  AA-large={aa_large}')
"
```

Expected: text-primary, text-secondary, all accents pass AA-body (≥4.5:1). text-muted may fail body but pass large (≥3:1) — that's acceptable for caption-sized labels.

If any pair fails AA-body and is used for body-sized text in the dashboard, adjust the token in `static/styles/tokens.css` (lighten by 5-10 lightness points) and re-run the check until passing.

- [ ] **Step 6.3: Commit**

```bash
git add static/styles.css static/styles/tokens.css
git commit -m "feat(visual): add WCAG AA focus rings, verify contrast tokens"
```

---

## Task 7: Replace emoji icons with Lucide

**Files:**
- Modify: `templates/dashboard.html` (and potentially `static/chart.js` if any inline icons live there)

**Preserve these emoji** — they are intentional per spec:
- Mood faces in score/verdict cards: 🚀 😊 😐 😬 😱
- V-Shape recovery banner emoji
- Any emoji inside `data-mood` or `class="mood"` elements

**Replace these emoji** with Lucide:
- Refresh button (e.g. 🔄, ↻) → `<i data-lucide="refresh-cw"></i>`
- Settings gear (⚙) → `<i data-lucide="settings"></i>`
- Up/down trend arrows used as icons (NOT inside metric values where they're textual indicators) → `<i data-lucide="arrow-up-right"></i>` / `arrow-down-right`
- Status dots (●) where used as icons → `<i data-lucide="circle"></i>`
- Warning triangle (⚠) → `<i data-lucide="alert-triangle"></i>`
- Tooltip / info circles (ℹ) → `<i data-lucide="info"></i>`
- Chevrons / disclosure arrows (▾ ▸) → `<i data-lucide="chevron-down"></i>` / `chevron-right`

- [ ] **Step 7.1: Inventory emoji usage**

```bash
grep -nP "[\x{1F300}-\x{1FAFF}]|[\x{2600}-\x{27BF}]" templates/dashboard.html | head -40
```

This lists all Unicode emoji and dingbat characters. Decide which to swap based on the preserve/replace lists above.

- [ ] **Step 7.2: Replace each non-preserved emoji**

Edit `templates/dashboard.html`. For each emoji being replaced, swap it for the Lucide marker:

```html
<!-- Before -->
<button class="refresh-btn">🔄 Refresh</button>

<!-- After -->
<button class="refresh-btn"><i data-lucide="refresh-cw"></i> Refresh</button>
```

For emoji inside JavaScript-generated content (e.g. SocketIO update handlers in `<script>` blocks at the bottom of dashboard.html), swap the emoji string and add a `lucide.createIcons();` call after the DOM mutation.

- [ ] **Step 7.3: Style the Lucide icons consistently**

Append to `static/styles.css`:

```css
[data-lucide] {
  width: 1em;
  height: 1em;
  vertical-align: -0.125em;
  stroke-width: 1.75;
  color: currentColor;
}

button [data-lucide], a [data-lucide] {
  margin-right: var(--space-1);
}
```

- [ ] **Step 7.4: Verify**

Reload dashboard. Mood faces and V-Shape emoji still render as emoji. Other icons (refresh, settings, info) now render as Lucide line icons. Hover over a button — the icon should inherit the button's text colour and animate with the button.

- [ ] **Step 7.5: Commit**

```bash
git add templates/dashboard.html static/styles.css
git commit -m "feat(visual): replace ad-hoc emoji icons with Lucide; preserve mood + V-Shape emoji"
```

---

## Task 8: Update chart.js colour palette to match tokens

**Files:**
- Modify: `static/chart.js`

Charts currently use hard-coded colours that don't match the new palette. Read tokens at runtime so chart colours stay in sync with `tokens.css`.

- [ ] **Step 8.1: Add a token-reading helper at the top of `chart.js`**

After the existing imports/setup at the top of `static/chart.js`, add:

```javascript
// Read CSS custom properties from :root so chart colours stay in sync with the design system.
function token(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const CHART_COLORS = {
  up:    token('--color-accent-up',   '#60e0a8'),
  down:  token('--color-accent-dn',   '#ff7a82'),
  warn:  token('--color-accent-warn', '#f5b963'),
  info:  token('--color-accent-info', '#7cb7ff'),
  text:  token('--color-text-primary',   '#eef1f8'),
  muted: token('--color-text-muted',     '#6b7289'),
  grid:  token('--color-border',         '#1e2430'),
  bg:    token('--color-bg-raised',      '#131721'),
};
```

- [ ] **Step 8.2: Replace hard-coded chart colours with `CHART_COLORS.*` references**

Search for hex literals in `chart.js`:

```bash
grep -nE "#[0-9a-fA-F]{3,6}" static/chart.js | head -30
```

For each, decide which `CHART_COLORS` entry it corresponds to. Common mappings:
- Bull line/fill → `CHART_COLORS.up`
- Bear line/fill → `CHART_COLORS.down`
- Grid lines / axis ticks → `CHART_COLORS.grid`
- Axis labels / legend text → `CHART_COLORS.text` or `CHART_COLORS.muted`

For Chart.js gradient fills, use the colour with explicit alpha — e.g. for the "score over time" area chart:
```javascript
const gradient = ctx.createLinearGradient(0, 0, 0, ctx.canvas.height);
gradient.addColorStop(0, CHART_COLORS.up + '66');  // ~40% alpha
gradient.addColorStop(1, CHART_COLORS.up + '00');  // transparent
```

- [ ] **Step 8.3: Verify charts render correctly**

Reload the dashboard. Each chart should now use the new palette — bullish areas in the new green, bearish in the new red, gridlines in the new border colour. The contrast against `--color-bg-raised` should be visibly better than before.

- [ ] **Step 8.4: Commit**

```bash
git add static/chart.js
git commit -m "refactor(visual): chart palette reads design tokens from CSS"
```

---

## Task 9: Apply tokens + fonts + Lucide to trades.html

**Files:**
- Modify: `templates/trades.html`

`trades.html` is smaller (225 lines). Same treatment as dashboard.html — load the same fonts, tokens, and Lucide CDN. Replace any emoji icons consistently with Task 7's policy.

- [ ] **Step 9.1: Add font + tokens + Lucide to `<head>` and end of `<body>`**

Apply the same `<link>` and `<script>` additions from Task 2 to `templates/trades.html`.

- [ ] **Step 9.2: Audit and replace emoji icons** the same way Task 7 did for dashboard.html.

- [ ] **Step 9.3: Visual smoke**

Reload `/trades`. Page should look consistent with the dashboard — same font, same colours, same icon style.

- [ ] **Step 9.4: Commit**

```bash
git add templates/trades.html
git commit -m "feat(visual): apply tokens + Inter + Lucide to trades page"
```

---

## Task 10: Final visual smoke + screenshot diff

**Files:**
- None modified — verification step only

- [ ] **Step 10.1: Capture "after" screenshots**

Restart the app, take screenshots of the same views captured in Step 0.1. Save to `docs/superpowers/screenshots/after/`.

- [ ] **Step 10.2: Compare before/after**

Open the before/after pairs side-by-side. Verify:

- ✅ Typography is more uniform (everything is Inter; no system-font fallback rendering)
- ✅ Colour palette is cohesive — no random pinks, no inconsistent greys
- ✅ No glassmorphism blur — surfaces are crisp
- ✅ Mood emoji faces still render as emoji (not Lucide icons)
- ✅ V-Shape banner emoji still renders as emoji
- ✅ Refresh / settings / warning / info icons now use Lucide
- ✅ Charts use the new palette
- ✅ Focus rings appear when tabbing through interactive elements
- ✅ All panels still display the same data they did before — nothing functional changed

If any panel looks broken (overlapping text, missing borders, wrong colour), file a follow-up commit on the same branch. Common fixes:
- Missing token reference → add the literal back temporarily, file a TODO comment
- Lucide icon not rendering → check for typo in `data-lucide="..."` name; verify `lucide.createIcons()` is called after DOM mutations

- [ ] **Step 10.3: Run the test suite one final time**

```bash
uv run python -m pytest tests/ -q
```

Should match the baseline count from Step 0.2. CSS changes never affect Python tests.

- [ ] **Step 10.4: No commit needed unless smoke uncovered fixes**

If everything looks good, Plan 2 is done. Move to user review.

---

## Self-Review Checklist (run after Task 10)

- [ ] Spec Section 6.1 (palette) — all 11 colour tokens present in tokens.css ✓
- [ ] Spec Section 6.1 (typography) — Inter loaded, type scale tokens applied ✓
- [ ] Spec Section 6.1 (spacing) — 4-pt grid tokens applied ✓
- [ ] Spec Section 6.1 (radius) — radius tokens applied ✓
- [ ] Spec Section 6.1 (elevation) — backdrop-filter blur fully removed; replaced with border + inset shadow ✓
- [ ] Spec Section 6.1 (motion) — 150ms default applied ✓
- [ ] Spec Section 6.1 (iconography) — Lucide loaded; non-preserved emoji swapped ✓
- [ ] Spec Section 6.2 (accessibility) — focus rings global; WCAG AA contrast verified ✓
- [ ] No layout changes (information architecture preserved per Plan 2 scope) ✓

## Definition of Done

1. `static/styles/tokens.css` exists and defines ≥30 tokens.
2. `static/styles.css` references tokens for colour, type scale, spacing, radius, motion. No `backdrop-filter` rules remain.
3. `templates/dashboard.html` and `templates/trades.html` both load Inter, tokens.css, and Lucide.
4. Mood face emoji and V-Shape banner emoji still render as emoji; other emoji icons swapped for Lucide.
5. `static/chart.js` reads colours from CSS tokens at runtime.
6. WCAG AA contrast confirmed for all token text/bg pairs used at body size.
7. All existing Python tests still pass.
8. Manual visual smoke confirms no panels broken.

---

## Future Improvements (out of scope, deferred per spec Section 9)

- Light-mode token set (`:root[data-theme="light"]`)
- Mobile-first responsive overhaul
- Replace Lucide CDN with bundled SVG sprite (currently CDN dependency)
- Animation library for value-change flash (spec mentions but doesn't yet implement)

---

## Done for Plan 2

- 10 tasks. ~25-30 incremental commits. Estimated 3-5 hours focused work.
- Output: visual polish layer applied to existing dashboard. No layout/data changes.
- Next: Plan 3 (novice view) implements the 4-tile + story layout consuming Plan 1's APIs.
