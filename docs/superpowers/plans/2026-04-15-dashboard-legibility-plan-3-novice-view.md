# Dashboard Legibility — Plan 3 of 4: Novice View

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the novice-default dashboard experience: a 2-3 sentence story headline shared with expert view, plus 4 "What to Watch" tiles (Mood, Trade/Signal, Battle Lines, Day Bias / Time Left). Add a Novice ⇄ Expert toggle in the header that persists to `localStorage`. The existing 15-panel dashboard becomes the Expert view (untouched in this plan — Plan 4 restructures it).

**Architecture:** The dashboard.html template gains two top-level sections (`#novice-view` and `#expert-view`) plus a shared `#story-strip` above them. A small vanilla-JS layer (`static/scripts/{story,tiles,view-toggle}.js`) fetches Plan 1's APIs, renders the components, and subscribes to the new SocketIO events (`story_update`, `tiles_update`, `ih_group_update`) for live updates. CSS is token-driven (Variant A from Plan 2). No new framework, no build step.

**Tech Stack:** Plain HTML/CSS/JS using design tokens from Plan 2 and APIs from Plan 1. SocketIO via the existing `socket.io.js` client. Lucide icons for non-mood UI. Mood face emoji (🚀 😊 😐 😬 😱) per spec.

**Reference spec:** `docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md` Sections 3.1, 3.2 (novice layout), 5 (tile system), 7.2/7.3 (APIs + SocketIO events).

---

## Critical prerequisite — branching

Plan 3 depends on **both** Plan 1 (APIs) and Plan 2 (tokens) being available. Plan 1 lives on `feat/dashboard-backend`, Plan 2 on `feat/dashboard-visual`. Merge both to master first, then branch Plan 3:

```bash
git checkout master
git merge feat/dashboard-backend     # or rebase, your call
git merge feat/dashboard-visual
git checkout -b feat/dashboard-novice
```

If you'd rather not merge yet, you can create an integration branch instead:

```bash
git checkout -b integration-novice master
git merge feat/dashboard-backend
git merge feat/dashboard-visual
git checkout -b feat/dashboard-novice
```

The pre-flight task in this plan asserts that both predecessors are present.

---

## File Structure

**Create:**
- `templates/partials/novice.html` — the novice view partial (story + tiles + show-more)
- `static/scripts/story.js` — fetches `/api/story`; renders sentences or warning; listens for `story_update` SocketIO event
- `static/scripts/tiles.js` — fetches `/api/tiles`; renders the 4 tiles; listens for `tiles_update` and `ih_group_update`
- `static/scripts/view-toggle.js` — Novice ⇄ Expert toggle; persists in `localStorage` under key `dashboard_view_mode`; sets `data-view` attribute on `<body>`
- `tests/test_dashboard_novice.py` — Flask test client smoke for novice partial + toggle markup

**Modify:**
- `templates/dashboard.html` — wrap existing body content in `<section id="expert-view">`; add `<section id="novice-view">` (renders the partial); add toggle button to header; add `<div id="story-strip">` above main; load the 3 new JS files
- `static/styles.css` — add CSS for `#story-strip`, `#novice-view`, `.tile`, `.tile-rows`, `.show-more` disclosure, view-toggle button, and the body `data-view` switch (hides inactive section)

**Out of scope for this plan:**
- Expert view restructure (15 panels → 8 blocks, strike-zone chart consolidation, IH-primary trades section) — Plan 4
- The 6 deferred failure modes from spec Section 4.6 (Kite-token-expired, BN/SX feed stalled, agent timeout, etc.) — these need a `/api/health` endpoint that's also Plan 4
- Light-mode toggle, mobile-first redesign — both deferred per spec Section 9

---

## Task 0: Pre-flight + baseline

- [ ] **Step 0.1: Confirm both predecessors are merged**

```bash
cd D:/Projects/oi_tracker
git log --oneline | grep -E "narrative|tile state classifier|tokens \(Variant A\)" | head -5
```

You should see commits like `feat(narrative): add Story, Warning, Severity data types` and `feat(visual): add design-system tokens (Variant A — Cool Professional)`. If either is missing, merge the corresponding branch before proceeding.

- [ ] **Step 0.2: Confirm tests pass + APIs respond**

```bash
uv run python -m pytest tests/ -q | tail -3
# Expected: 604 passing (518 from master + 80 from Plan 1 + 6 from fix)

uv run python -c "
from app import app
c = app.test_client()
for path in ['/', '/trades', '/api/story', '/api/tiles', '/api/ih/group', '/api/multi-index', '/api/latest']:
    r = c.get(path)
    print(f'{r.status_code:3d}  {len(r.data):>8d}  {path}')
"
```

All 7 paths should return 200. Record the test count as the new baseline.

- [ ] **Step 0.3: Take baseline screenshots** (user-manual)

`uv run python app.py` → http://localhost:5000 → screenshot the existing dashboard top-to-bottom. Save to `docs/superpowers/screenshots/before-plan3/`. Reference for the visual smoke at the end of Plan 3.

---

## Task 1: Wrap existing body content + add view-toggle scaffolding

This task makes the toggle infrastructure work without yet rendering any novice content. Default state = expert (so the user sees no change). Task 5 flips the default to novice once the novice content is built.

**Files:**
- Modify: `templates/dashboard.html`
- Create: `static/scripts/view-toggle.js`
- Modify: `static/styles.css`

- [ ] **Step 1.1: Wrap existing body content in `<section id="expert-view">`**

Open `templates/dashboard.html`. Locate the main content region — typically everything between the header and the closing `</body>`. Wrap it in:

```html
<section id="expert-view" class="view-section">
  <!-- (existing dashboard content stays exactly as-is here) -->
</section>
```

Place an empty `<section id="novice-view" class="view-section"></section>` immediately ABOVE the expert section. Task 2 will populate it. The full structure becomes:

```html
<header class="page-header">...existing header content...</header>

<section id="novice-view" class="view-section">
  <!-- populated in Task 2 -->
</section>

<section id="expert-view" class="view-section">
  <!-- existing 15-panel dashboard content untouched -->
</section>
```

- [ ] **Step 1.2: Add the view-toggle button to the header**

In the existing `<header class="page-header">...</header>` block, find a sensible spot among the existing controls (next to the refresh button is good). Add:

```html
<button id="view-toggle" class="view-toggle" aria-label="Switch to expert view">
  <i data-lucide="layout-dashboard"></i>
  <span class="view-toggle-label">Expert</span>
</button>
```

The label text and icon will swap based on current view in JS.

- [ ] **Step 1.3: Create `static/scripts/view-toggle.js`**

```javascript
/**
 * View toggle — Novice ⇄ Expert. Persists to localStorage.
 * Spec ref: section 3.1 (default novice on first load).
 */
(function () {
  const STORAGE_KEY = 'dashboard_view_mode';
  const DEFAULT_MODE = 'novice';   // spec Section 3.1: default on first load

  function getMode() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_MODE;
  }

  function applyMode(mode) {
    document.body.dataset.view = mode;
    const btn = document.getElementById('view-toggle');
    if (!btn) return;
    const otherMode = mode === 'novice' ? 'expert' : 'novice';
    btn.setAttribute('aria-label', `Switch to ${otherMode} view`);
    const labelEl = btn.querySelector('.view-toggle-label');
    if (labelEl) labelEl.textContent = otherMode.charAt(0).toUpperCase() + otherMode.slice(1);
    if (window.lucide) lucide.createIcons();
  }

  function toggle() {
    const next = getMode() === 'novice' ? 'expert' : 'novice';
    localStorage.setItem(STORAGE_KEY, next);
    applyMode(next);
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyMode(getMode());
    const btn = document.getElementById('view-toggle');
    if (btn) btn.addEventListener('click', toggle);
  });
})();
```

- [ ] **Step 1.4: Load the script in dashboard.html**

Just before the existing Lucide CDN script at the bottom of the body, add:

```html
<script src="{{ url_for('static', filename='scripts/view-toggle.js') }}"></script>
```

(Order matters: view-toggle must run before `lucide.createIcons()` so the icon-swap-on-toggle works on first paint.)

- [ ] **Step 1.5: Add CSS to hide the inactive section**

Append to `static/styles.css`:

```css
/* ===== View toggle (Novice / Expert) ===== */
body[data-view="novice"] #expert-view,
body[data-view="expert"] #novice-view {
  display: none;
}

.view-section {
  /* Both sections take the same flow when visible. */
  width: 100%;
}

.view-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  border: var(--elev-card-border);
  background: var(--color-bg-raised);
  color: var(--color-text-primary);
  font-family: var(--font-sans);
  font-size: var(--type-small);
  cursor: pointer;
  transition: var(--motion-default);
}
.view-toggle:hover {
  background: var(--color-bg-elevated);
  border-color: var(--color-border-strong);
}
.view-toggle [data-lucide] {
  width: 14px;
  height: 14px;
}
```

- [ ] **Step 1.6: Verify**

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
assert r.status_code == 200
body = r.data.decode('utf-8')
assert 'id=\"novice-view\"' in body, 'novice section missing'
assert 'id=\"expert-view\"' in body, 'expert section missing'
assert 'id=\"view-toggle\"' in body, 'toggle button missing'
assert 'view-toggle.js' in body, 'view-toggle script missing'
print('OK — novice/expert sections + toggle wired')
"
```

```bash
uv run python -m pytest tests/ -q | tail -3
```

- [ ] **Step 1.7: Commit**

```bash
git add templates/dashboard.html static/scripts/view-toggle.js static/styles.css
git commit -m "feat(novice): add view-toggle scaffolding and empty novice section"
```

After this commit, the dashboard looks identical to before because novice section is empty AND default mode is novice — so `body[data-view="novice"]` hides expert and shows the empty novice. Wait — that's a regression. **Temporarily set DEFAULT_MODE = "expert"** in the JS at this stage, and flip it back to "novice" in Task 5 (or Task 8) after novice content is in place. Update Step 1.3's `DEFAULT_MODE` line.

---

## Task 2: Create the novice shell + story-strip slot

The novice partial exists; it has empty placeholders for story headline, tiles, and "show more". Subsequent tasks fill them.

**Files:**
- Create: `templates/partials/novice.html`
- Modify: `templates/dashboard.html` (include the partial inside `#novice-view`)

- [ ] **Step 2.1: Create `templates/partials/novice.html`**

```html
{# Novice view — story headline + 4 tiles + show-more disclosure.
   Spec ref: design Section 3.2 (novice layout) and Section 5 (tile system). #}

<div id="story-strip" class="story-strip" aria-live="polite">
  {# Populated by static/scripts/story.js. Empty until first paint. #}
  <div class="story-skeleton">Loading market story…</div>
</div>

<div id="tiles-grid" class="tiles-grid">
  {# Populated by static/scripts/tiles.js. 4 tile slots. #}
  <div class="tile-skeleton" data-slot="1"></div>
  <div class="tile-skeleton" data-slot="2"></div>
  <div class="tile-skeleton" data-slot="3"></div>
  <div class="tile-skeleton" data-slot="4"></div>
</div>

<details id="show-more" class="show-more">
  <summary class="show-more-trigger">
    <i data-lucide="chevron-down"></i>
    <span>Show more detail</span>
  </summary>
  <div class="show-more-content">
    {# Populated in Task 7. Simple spot chart + day-bias breakdown + regime note. #}
    <p class="show-more-placeholder">Detail view coming in next task.</p>
  </div>
</details>
```

- [ ] **Step 2.2: Wire the partial into `dashboard.html`**

Inside `<section id="novice-view" class="view-section">…</section>`, replace the empty body with:

```html
<section id="novice-view" class="view-section">
  {% include "partials/novice.html" %}
</section>
```

- [ ] **Step 2.3: Add base CSS for the novice shell**

Append to `static/styles.css`:

```css
/* ===== Novice view — shell + skeletons ===== */
.story-strip {
  background: linear-gradient(135deg, var(--color-accent-info-soft), var(--color-accent-up-soft));
  border: var(--elev-card-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-6);
  margin: var(--space-4);
  font-family: var(--font-sans);
  font-size: var(--type-h1);
  line-height: var(--leading-loose);
  color: var(--color-text-primary);
}
.story-skeleton {
  color: var(--color-text-muted);
  font-size: var(--type-body);
  font-style: italic;
}

.tiles-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  margin: 0 var(--space-4);
}
.tile-skeleton {
  background: var(--color-bg-raised);
  border: var(--elev-card-border);
  border-radius: var(--radius-lg);
  min-height: 96px;
  animation: tile-skeleton-pulse 1.5s ease-in-out infinite;
}
@keyframes tile-skeleton-pulse {
  0%, 100% { opacity: 0.8; }
  50%      { opacity: 0.55; }
}

@media (max-width: 880px) {
  .tiles-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 480px) {
  .tiles-grid { grid-template-columns: 1fr; }
}

.show-more {
  margin: var(--space-4);
  border: var(--elev-card-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg-raised);
}
.show-more-trigger {
  cursor: pointer;
  padding: var(--space-3) var(--space-4);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--type-small);
  list-style: none;
  user-select: none;
}
.show-more-trigger::-webkit-details-marker { display: none; }
.show-more[open] > .show-more-trigger [data-lucide="chevron-down"] {
  transform: rotate(180deg);
  transition: transform var(--motion-default);
}
.show-more-content {
  padding: var(--space-4);
  border-top: var(--elev-card-border);
}
.show-more-placeholder {
  color: var(--color-text-muted);
  font-style: italic;
  font-size: var(--type-body);
}
```

- [ ] **Step 2.4: Verify**

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
body = r.data.decode('utf-8')
assert 'story-strip' in body
assert 'tiles-grid' in body
assert 'show-more' in body
assert 'data-slot=\"1\"' in body
print('OK — novice shell rendered')
"
```

- [ ] **Step 2.5: Commit**

```bash
git add templates/partials/ templates/dashboard.html static/styles.css
git commit -m "feat(novice): add novice shell partial with story strip + tile skeletons + show-more"
```

---

## Task 3: Story headline component

`static/scripts/story.js` fetches `/api/story` on load, renders the 2-3 sentences (or a warning card), and subscribes to the `story_update` SocketIO event for live updates.

**Files:**
- Create: `static/scripts/story.js`
- Modify: `templates/dashboard.html` (load the script)

- [ ] **Step 3.1: Create `static/scripts/story.js`**

```javascript
/**
 * Story headline — fetches /api/story on load, listens for SocketIO story_update.
 * Spec ref: Section 3.2 (story headline shared with expert view), Section 4.6 (warnings).
 */
(function () {
  const STORY_EL = '#story-strip';

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function renderStory(payload) {
    const el = document.querySelector(STORY_EL);
    if (!el) return;

    if (payload && payload.warning) {
      const w = payload.warning;
      const action = w.action_label && w.action_url
        ? `<a class="story-warning-action" href="${escapeHtml(w.action_url)}">${escapeHtml(w.action_label)}</a>`
        : '';
      el.innerHTML = `
        <div class="story-warning story-warning-${escapeHtml(w.severity || 'warn')}">
          <i data-lucide="alert-triangle"></i>
          <div class="story-warning-body">
            <div class="story-warning-message">${escapeHtml(w.message)}</div>
            ${action}
          </div>
        </div>
      `;
      if (window.lucide) lucide.createIcons();
      return;
    }

    if (!payload || !payload.sentences || payload.sentences.length === 0) {
      el.innerHTML = `<div class="story-skeleton">No story yet — waiting for first analysis cycle.</div>`;
      return;
    }

    el.innerHTML = payload.sentences
      .map((s) => `<span class="story-sentence">${escapeHtml(s)}</span>`)
      .join(' ');
  }

  async function fetchStory() {
    try {
      const r = await fetch('/api/story');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      renderStory(data);
    } catch (e) {
      console.error('story fetch failed', e);
      renderStory({ warning: { code: 'FETCH_FAILED', message: 'Story service unreachable.', severity: 'error' } });
    }
  }

  function subscribeSocket() {
    if (typeof io !== 'function') return;
    const socket = window._dashboardSocket || (window._dashboardSocket = io());
    socket.on('story_update', (data) => renderStory(data));
  }

  document.addEventListener('DOMContentLoaded', () => {
    fetchStory();
    subscribeSocket();
  });
})();
```

- [ ] **Step 3.2: Add CSS for sentences and warning card**

Append to `static/styles.css`:

```css
/* ===== Story strip — sentences and warning ===== */
.story-sentence {
  margin-right: var(--space-1);
}
.story-warning {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}
.story-warning [data-lucide] {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: var(--color-accent-warn);
}
.story-warning-error [data-lucide] {
  color: var(--color-accent-dn);
}
.story-warning-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.story-warning-message {
  font-size: var(--type-body);
  color: var(--color-text-primary);
}
.story-warning-action {
  display: inline-block;
  padding: var(--space-1) var(--space-3);
  background: var(--color-accent-info);
  color: var(--color-text-inverse);
  border-radius: var(--radius-md);
  text-decoration: none;
  font-size: var(--type-small);
  font-weight: var(--weight-medium);
  align-self: flex-start;
}
.story-warning-action:hover {
  filter: brightness(0.92);
}
```

- [ ] **Step 3.3: Load story.js in `dashboard.html`**

Just after the view-toggle script tag (added in Task 1.4):

```html
<script src="{{ url_for('static', filename='scripts/story.js') }}"></script>
```

- [ ] **Step 3.4: Verify**

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
body = r.data.decode('utf-8')
assert 'story.js' in body, 'story.js script tag missing'
print('OK — story.js wired')
# Confirm /api/story still responds
r = c.get('/api/story')
assert r.status_code == 200
print('  /api/story returns', r.status_code, 'with', len(r.data), 'bytes')
"
```

- [ ] **Step 3.5: Commit**

```bash
git add static/scripts/story.js static/styles.css templates/dashboard.html
git commit -m "feat(novice): story headline component fetches /api/story + subscribes to story_update"
```

---

## Task 4: Tiles component

`static/scripts/tiles.js` fetches `/api/tiles` on load, renders 4 tiles into `#tiles-grid`, subscribes to `tiles_update` and `ih_group_update` SocketIO events.

**Files:**
- Create: `static/scripts/tiles.js`
- Modify: `templates/dashboard.html` (load the script)

- [ ] **Step 4.1: Create `static/scripts/tiles.js`**

```javascript
/**
 * Tile renderer — fetches /api/tiles, listens for tiles_update + ih_group_update.
 * Spec ref: Section 5 (tile system, 4 fixed slots, adaptive content).
 */
(function () {
  const GRID_EL = '#tiles-grid';

  const SLOT_LABELS = {
    1: 'Mood',
    2: 'Trade / Signal',
    3: 'Battle Lines',
    4: 'Day Bias',
  };

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function renderTile(tile) {
    const slot = tile.slot;
    const label = SLOT_LABELS[slot] || `Slot ${slot}`;
    const accent = escapeHtml(tile.accent || 'muted');
    const rowsHtml = (tile.rows || [])
      .map(
        (r) => `
        <div class="tile-row">
          <span class="tile-row-left">${escapeHtml(r.left)}</span>
          <span class="tile-row-right">
            ${escapeHtml(r.right)}
            ${r.is_paper === true ? '<span class="tile-row-tag tile-row-paper">PAPER</span>' : ''}
            ${r.is_paper === false ? '<span class="tile-row-tag tile-row-live">LIVE</span>' : ''}
          </span>
        </div>
      `,
      )
      .join('');
    const hint = tile.hint ? `<div class="tile-hint">${escapeHtml(tile.hint)}</div>` : '';
    return `
      <div class="tile tile-${accent}" data-slot="${slot}">
        <div class="tile-label">${escapeHtml(label)}</div>
        <div class="tile-primary">${escapeHtml(tile.primary)}</div>
        ${tile.caption ? `<div class="tile-caption">${escapeHtml(tile.caption)}</div>` : ''}
        ${rowsHtml ? `<div class="tile-rows">${rowsHtml}</div>` : ''}
        ${hint}
      </div>
    `;
  }

  function renderTiles(payload) {
    const grid = document.querySelector(GRID_EL);
    if (!grid) return;
    if (!payload || !Array.isArray(payload.tiles) || payload.tiles.length === 0) {
      // Keep skeletons.
      return;
    }
    grid.innerHTML = payload.tiles.map(renderTile).join('');
    if (window.lucide) lucide.createIcons();
  }

  async function fetchTiles() {
    try {
      const r = await fetch('/api/tiles');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      renderTiles(await r.json());
    } catch (e) {
      console.error('tiles fetch failed', e);
    }
  }

  function subscribeSocket() {
    if (typeof io !== 'function') return;
    const socket = window._dashboardSocket || (window._dashboardSocket = io());
    socket.on('tiles_update', (data) => renderTiles(data));
    // ih_group_update is fired immediately on lifecycle transitions (not 3-min cycle).
    // Re-fetch /api/tiles so slot 2 reflects the new IH state right away.
    socket.on('ih_group_update', () => fetchTiles());
  }

  document.addEventListener('DOMContentLoaded', () => {
    fetchTiles();
    subscribeSocket();
  });
})();
```

- [ ] **Step 4.2: Add CSS for tiles**

Append to `static/styles.css`:

```css
/* ===== Tiles — 4 fixed slots, adaptive content ===== */
.tile {
  background: var(--color-bg-raised);
  border: var(--elev-card-border);
  box-shadow: var(--elev-card-inset);
  border-radius: var(--radius-lg);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-height: 96px;
  transition: var(--motion-default);
}
.tile-up    { border-left: 3px solid var(--color-accent-up); }
.tile-dn    { border-left: 3px solid var(--color-accent-dn); }
.tile-warn  { border-left: 3px solid var(--color-accent-warn); }
.tile-info  { border-left: 3px solid var(--color-accent-info); }
.tile-muted { border-left: 3px solid var(--color-border-strong); }

.tile-label {
  font-size: var(--type-caption);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: var(--color-text-muted);
}
.tile-primary {
  font-size: var(--type-display);
  font-weight: var(--weight-semi);
  letter-spacing: var(--tracking-display);
  color: var(--color-text-primary);
  line-height: var(--leading-tight);
}
.tile-caption {
  font-size: var(--type-small);
  color: var(--color-text-secondary);
}
.tile-rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: var(--space-1);
}
.tile-row {
  display: flex;
  justify-content: space-between;
  font-size: var(--type-small);
  font-family: var(--font-mono);
  color: var(--color-text-secondary);
}
.tile-row-tag {
  display: inline-block;
  padding: 0 var(--space-1);
  margin-left: var(--space-1);
  border-radius: var(--radius-sm);
  font-size: var(--type-caption);
  font-family: var(--font-sans);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}
.tile-row-live  { background: var(--color-accent-up-soft); color: var(--color-accent-up); }
.tile-row-paper { background: var(--color-bg-elevated); color: var(--color-text-muted); }

.tile-hint {
  margin-top: auto;
  font-size: var(--type-caption);
  font-style: italic;
  color: var(--color-text-muted);
}
```

- [ ] **Step 4.3: Load tiles.js in `dashboard.html`**

After the story.js script tag:

```html
<script src="{{ url_for('static', filename='scripts/tiles.js') }}"></script>
```

- [ ] **Step 4.4: Verify**

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
body = r.data.decode('utf-8')
assert 'tiles.js' in body
print('OK — tiles.js wired')
r = c.get('/api/tiles')
assert r.status_code == 200
import json
data = json.loads(r.data)
assert 'tiles' in data
assert len(data['tiles']) == 4
print('  /api/tiles returns 4 tiles')
"
```

- [ ] **Step 4.5: Commit**

```bash
git add static/scripts/tiles.js static/styles.css templates/dashboard.html
git commit -m "feat(novice): tile renderer with 4 fixed slots + live SocketIO updates"
```

---

## Task 5: Flip default view to novice

Now that novice has content, make it the default per spec Section 3.1.

**Files:**
- Modify: `static/scripts/view-toggle.js`

- [ ] **Step 5.1: Change `DEFAULT_MODE` from `"expert"` (set in Task 1) to `"novice"`**

Edit the line in `static/scripts/view-toggle.js`:

```javascript
  const DEFAULT_MODE = 'novice';   // spec Section 3.1: default on first load
```

- [ ] **Step 5.2: Verify by clearing localStorage and reloading**

In the browser at http://localhost:5000, open DevTools → Application → Local Storage → http://localhost:5000 → delete `dashboard_view_mode`. Hard-reload. The novice view should be the default.

Or test programmatically:

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
body = r.data.decode('utf-8')
import re
# Match the line in view-toggle.js once it's served
# Default check (Flask serves the JS file as static)
js = c.get('/static/scripts/view-toggle.js').data.decode('utf-8')
assert \"DEFAULT_MODE = 'novice'\" in js
print('OK — default is novice')
"
```

- [ ] **Step 5.3: Commit**

```bash
git add static/scripts/view-toggle.js
git commit -m "feat(novice): flip default view to novice per spec Section 3.1"
```

---

## Task 6: Show-more disclosure content

The disclosure currently shows a placeholder. Fill it with three pieces of expanded detail per spec Section 3.2: simple spot chart, day-bias breakdown, regime note.

**Files:**
- Modify: `templates/partials/novice.html`
- Create: `static/scripts/show-more.js`
- Modify: `templates/dashboard.html` (load the script)
- Modify: `static/styles.css`

- [ ] **Step 6.1: Replace the placeholder in `templates/partials/novice.html`**

Replace `<p class="show-more-placeholder">Detail view coming in next task.</p>` with:

```html
<div class="show-more-grid">
  <div class="show-more-card">
    <div class="show-more-card-label">Spot trend</div>
    <canvas id="show-more-spot-chart" height="80"></canvas>
  </div>
  <div class="show-more-card">
    <div class="show-more-card-label">Day Bias breakdown</div>
    <div id="show-more-day-bias" class="show-more-day-bias">
      <span class="show-more-bias-skeleton">Loading…</span>
    </div>
  </div>
  <div class="show-more-card">
    <div class="show-more-card-label">Current regime</div>
    <div id="show-more-regime" class="show-more-regime">
      <span class="show-more-regime-skeleton">Loading…</span>
    </div>
  </div>
</div>
```

- [ ] **Step 6.2: Create `static/scripts/show-more.js`**

```javascript
/**
 * Show-more detail — populates spot trend, day bias breakdown, and regime card.
 * Lazy-fetches when the disclosure first opens, then refreshes on every minute.
 */
(function () {
  let initialised = false;
  let refreshTimer = null;

  async function fetchAll() {
    const [latest, multi] = await Promise.all([
      fetch('/api/latest').then((r) => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/multi-index').then((r) => r.ok ? r.json() : null).catch(() => null),
    ]);
    renderSpotTrend(latest);
    renderDayBias(latest, multi);
    renderRegime(latest);
  }

  function renderSpotTrend(latest) {
    const canvas = document.getElementById('show-more-spot-chart');
    if (!canvas || !latest || !latest.chart_history) return;
    const points = latest.chart_history
      .filter((row) => row.spot_price)
      .slice(-60)
      .map((row) => row.spot_price);
    if (points.length < 2) return;
    drawSparkline(canvas, points);
  }

  function drawSparkline(canvas, values) {
    const ctx = canvas.getContext('2d');
    const w = canvas.clientWidth || canvas.width;
    const h = canvas.clientHeight || canvas.height;
    canvas.width = w;
    canvas.height = h;
    ctx.clearRect(0, 0, w, h);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = (max - min) || 1;
    const stepX = w / (values.length - 1);
    ctx.strokeStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--color-accent-info').trim() || '#7cb7ff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = i * stepX;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function renderDayBias(latest, multi) {
    const el = document.getElementById('show-more-day-bias');
    if (!el) return;
    // Day bias is in the IH state — fetch from /api/ih/group
    fetch('/api/ih/group').then((r) => r.json()).then((ih) => {
      const bias = ih.day_bias;
      const hdfc = multi && multi.HDFCBANK != null ? `${multi.HDFCBANK >= 0 ? '+' : ''}${multi.HDFCBANK.toFixed(2)}%` : '—';
      const kotak = multi && multi.KOTAKBANK != null ? `${multi.KOTAKBANK >= 0 ? '+' : ''}${multi.KOTAKBANK.toFixed(2)}%` : '—';
      el.innerHTML = `
        <div class="show-more-bias-row">
          <span class="show-more-bias-label">Score</span>
          <span class="show-more-bias-value">${bias != null ? bias.toFixed(2) : '—'}</span>
        </div>
        <div class="show-more-bias-row">
          <span class="show-more-bias-label">HDFC Bank</span>
          <span class="show-more-bias-value">${hdfc}</span>
        </div>
        <div class="show-more-bias-row">
          <span class="show-more-bias-label">Kotak Bank</span>
          <span class="show-more-bias-value">${kotak}</span>
        </div>
        <div class="show-more-bias-hint">Threshold for entry: ±0.60</div>
      `;
    }).catch(() => {
      el.innerHTML = '<span class="show-more-bias-skeleton">Day bias unavailable.</span>';
    });
  }

  function renderRegime(latest) {
    const el = document.getElementById('show-more-regime');
    if (!el || !latest) return;
    const regime = (latest.market_regime && latest.market_regime.regime) || latest.regime || 'unknown';
    const meanings = {
      TRENDING_UP:    'Sustained upward move; pullbacks tend to be bought.',
      TRENDING_DOWN:  'Sustained downward move; rallies tend to be sold.',
      HIGH_VOL_UP:    'Choppy rally — wide swings, watch for whipsaws.',
      HIGH_VOL_DOWN:  'Volatile selloff — fast moves, expect reversals.',
      NORMAL:         'Average volatility; no strong directional bias.',
      LOW_VOL:        'Quiet session; tight range, low conviction.',
    };
    const label = String(regime).replace(/_/g, ' ');
    const meaning = meanings[String(regime).toUpperCase()] || 'Regime classifier still warming up.';
    el.innerHTML = `
      <div class="show-more-regime-label">${label}</div>
      <div class="show-more-regime-meaning">${meaning}</div>
    `;
  }

  function ensureInitialised() {
    if (initialised) return;
    initialised = true;
    fetchAll();
    refreshTimer = setInterval(fetchAll, 60_000);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('show-more');
    if (!el) return;
    el.addEventListener('toggle', () => {
      if (el.open) ensureInitialised();
    });
  });
})();
```

- [ ] **Step 6.3: Add CSS for the show-more grid + cards**

Append to `static/styles.css`:

```css
/* ===== Show-more detail cards ===== */
.show-more-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-3);
}
.show-more-card {
  background: var(--color-bg);
  border: var(--elev-card-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.show-more-card-label {
  font-size: var(--type-caption);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: var(--color-text-muted);
}
.show-more-bias-row {
  display: flex;
  justify-content: space-between;
  font-size: var(--type-small);
  color: var(--color-text-secondary);
}
.show-more-bias-value {
  font-family: var(--font-mono);
  color: var(--color-text-primary);
}
.show-more-bias-hint {
  margin-top: var(--space-2);
  font-size: var(--type-caption);
  font-style: italic;
  color: var(--color-text-muted);
}
.show-more-regime-label {
  font-size: var(--type-h1);
  font-weight: var(--weight-semi);
  color: var(--color-text-primary);
  text-transform: capitalize;
}
.show-more-regime-meaning {
  font-size: var(--type-small);
  color: var(--color-text-secondary);
  line-height: var(--leading-loose);
}

@media (max-width: 880px) {
  .show-more-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 6.4: Load show-more.js**

After tiles.js script tag in `templates/dashboard.html`:

```html
<script src="{{ url_for('static', filename='scripts/show-more.js') }}"></script>
```

- [ ] **Step 6.5: Verify**

```bash
uv run python -c "
from app import app
c = app.test_client()
r = c.get('/')
body = r.data.decode('utf-8')
assert 'show-more-grid' in body
assert 'show-more.js' in body
print('OK — show-more wired')
"
```

- [ ] **Step 6.6: Commit**

```bash
git add templates/partials/novice.html static/scripts/show-more.js static/styles.css templates/dashboard.html
git commit -m "feat(novice): show-more disclosure with spot trend, day bias, regime card"
```

---

## Task 7: Integration test for novice partial + view toggle

**Files:**
- Create: `tests/test_dashboard_novice.py`

- [ ] **Step 7.1: Write tests**

```python
"""Integration tests for novice view + view toggle."""

import pytest


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_dashboard_includes_novice_section(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'id="novice-view"' in html
    assert 'id="expert-view"' in html
    assert 'id="story-strip"' in html
    assert 'id="tiles-grid"' in html
    assert 'id="show-more"' in html


def test_dashboard_includes_view_toggle_button(client):
    html = client.get("/").data.decode("utf-8")
    assert 'id="view-toggle"' in html


def test_all_novice_scripts_loaded(client):
    html = client.get("/").data.decode("utf-8")
    for script in ["view-toggle.js", "story.js", "tiles.js", "show-more.js"]:
        assert script in html, f"{script} not loaded"


def test_default_view_is_novice(client):
    """Confirm the JS default is 'novice', not 'expert'."""
    js = client.get("/static/scripts/view-toggle.js").data.decode("utf-8")
    assert "DEFAULT_MODE = 'novice'" in js


def test_tile_skeletons_render_with_4_slots(client):
    html = client.get("/").data.decode("utf-8")
    for slot in (1, 2, 3, 4):
        assert f'data-slot="{slot}"' in html
```

- [ ] **Step 7.2: Run**

```bash
uv run python -m pytest tests/test_dashboard_novice.py -v
```

Expected: 5 pass.

- [ ] **Step 7.3: Run full suite**

```bash
uv run python -m pytest tests/ -q | tail -3
```

Expected: 609 passing (604 + 5 new).

- [ ] **Step 7.4: Commit**

```bash
git add tests/test_dashboard_novice.py
git commit -m "test(novice): integration smoke for novice partial + view toggle"
```

---

## Task 8: Manual visual smoke

This is gated verification — run against a live app.

- [ ] **Step 8.1: Start the app**

```bash
uv run python app.py
```

Open http://localhost:5000 in a browser. Hard-reload (Ctrl+Shift+R) to bust cache.

- [ ] **Step 8.2: Verify novice view renders by default**

Expected at first paint:
- A story strip near the top showing 2-3 sentences (or the "Loading market story…" skeleton briefly)
- A 4-tile grid below it with Mood / Trade / Battle Lines / Day Bias content
- A "▾ Show more detail" disclosure below the tiles

If you see the old 15-panel layout instead, the toggle is set to expert. Click the **Expert** button (top-right) to verify the toggle text — it should say "Expert" while novice is showing (because the button label is the *target* mode). Click it. Now you should see the old dashboard. Click again. Back to novice.

- [ ] **Step 8.3: Verify SocketIO live updates**

Open DevTools → Console. Paste:

```javascript
const s = window._dashboardSocket || io();
s.on('story_update', d => console.log('story_update', d));
s.on('tiles_update', d => console.log('tiles_update', d));
s.on('ih_group_update', d => console.log('ih_group_update', d));
```

Trigger a refresh:

```bash
curl http://localhost:5000/api/refresh
```

Console should log `story_update` and `tiles_update`. The story strip should re-render with new sentences if the analysis changed; the tiles should update.

- [ ] **Step 8.4: Open the show-more disclosure**

Click "▾ Show more detail" near the bottom of the novice view. The chevron rotates, the panel opens with three cards: Spot trend (sparkline), Day Bias breakdown (HDFC/KOTAK rows), Current regime (label + plain-English meaning).

- [ ] **Step 8.5: Verify expert toggle preserves the existing dashboard**

Click "Expert" button. You should see the existing 15-panel layout exactly as it was before Plan 3 — no panels missing, no broken styles. The story strip stays visible above (it's shared across both views per spec Section 3.1).

Click "Novice" to return.

- [ ] **Step 8.6: Verify localStorage persistence**

In DevTools → Application → Local Storage, confirm `dashboard_view_mode` is set to whichever view you last selected. Reload the page; the same view should load.

- [ ] **Step 8.7: Test the warning render**

Force a stale-data warning to verify the warning card renders. In DevTools console:

```javascript
window._renderTestWarning = () => {
  document.getElementById('story-strip').innerHTML = `
    <div class="story-warning story-warning-warn">
      <i data-lucide="alert-triangle"></i>
      <div class="story-warning-body">
        <div class="story-warning-message">Last update 8m ago.</div>
        <a class="story-warning-action" href="#">Refresh</a>
      </div>
    </div>`;
  lucide.createIcons();
};
window._renderTestWarning();
```

Confirm: amber triangle icon, message text legible, action button visible. Refresh the page after to clear the test state.

- [ ] **Step 8.8: Mobile breakpoint check**

In DevTools → toggle device toolbar (Ctrl+Shift+M) → set to 390×844. The tile grid should collapse from 4 columns to 2 (then 1 below 480px). The show-more grid should collapse to 1 column. The story strip stays full-width and readable.

- [ ] **Step 8.9: No commit needed unless smoke uncovered fixes**

If everything works, Plan 3 is done. Move to user review.

---

## Self-Review Checklist

- [ ] Spec Section 3.1 (default novice) — Task 5 ✓
- [ ] Spec Section 3.2 (novice layout: header + story + tiles + show-more) — Tasks 2, 3, 4, 6 ✓
- [ ] Spec Section 5.1 (4 fixed slots) — Task 4 + tile_state.py from Plan 1 ✓
- [ ] Spec Section 5.2-5.3 (slot 2 + slot 4 adaptive content) — handled by Plan 1's `build_tile_state` ✓
- [ ] Spec Section 7.2 (4 new APIs consumed) — Tasks 3, 4, 6 ✓
- [ ] Spec Section 7.3 (3 new SocketIO events subscribed) — Tasks 3, 4 ✓
- [ ] localStorage persistence of toggle — Task 1 ✓
- [ ] Mobile responsive (tiles collapse to 2/1, show-more collapses to 1) — Tasks 2, 6 ✓
- [ ] Mood face emoji preserved (per Plan 2 — they come from backend tile primary text) ✓

## Definition of Done

1. Novice view loads by default (toggle in expert state only when user has explicitly switched).
2. Story strip renders 2-3 sentences (or warning card if Plan 1's narrative engine emits one).
3. 4 tile cards render in correct slots with adaptive content based on IH/RR state.
4. Toggle button switches views; state persists across reloads.
5. Show-more disclosure reveals spot trend sparkline, day-bias breakdown, regime card.
6. SocketIO live updates work for `story_update`, `tiles_update`, `ih_group_update`.
7. All existing Python tests pass; 5 new integration tests for the novice surface pass.
8. Mobile (390px) breakpoint renders without horizontal scroll.
9. No regressions in the existing dashboard panels (visible when toggled to Expert).

---

## Future Improvements (deferred)

- **Server-side rendering of first paint.** Currently the story strip and tiles show "Loading…" skeletons until JS fetches. A future plan could pre-render the latest values into the template so the first paint is fully populated.
- **Settings panel.** Density toggle (compact / comfortable), font-size override, theme toggle.
- **Keyboard shortcut for view toggle** (e.g. `n` / `e` keys).
- **Plan 4 expert restructure** consumes the same story strip and reuses its component.

---

## Done for Plan 3

- 8 tasks. ~10-15 commits. Estimated 3-5 hours focused work.
- Output: novice-default dashboard with story headline, 4 tiles, show-more disclosure, view toggle.
- Next: Plan 4 (expert view rebuild) restructures the 15 panels into 8 blocks with the consolidated strike-zone chart and IH-primary trades section.
