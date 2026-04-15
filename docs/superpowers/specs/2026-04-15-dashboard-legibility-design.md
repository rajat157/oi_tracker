# Dashboard Legibility Redesign — Design Spec

**Date:** 2026-04-15
**Status:** Approved for implementation planning
**Approach:** Balanced (rule-based story engine, adaptive tiles, full expert-view restructure, Variant A visual language)
**Future consideration:** Approach C — Claude-generated story + fully adaptive tile slots — documented in Section 9.

---

## 1. Goals and Non-Goals

### Goals

1. A first-time viewer with no options-trading background can open the dashboard and, within five seconds, understand *what the market is doing right now* — in prose.
2. Every visible number carries a plain-English caption explaining what it means when it has the current value.
3. The dashboard works at two depths: a novice default (story + four tiles) and an expert view (full analytics). State of the toggle persists across sessions.
4. The IntradayHunter (IH) strategy — the production strategy — is the centre of gravity for trade-facing UI. Rally Rider (RR) is surfaced but secondary.
5. Visual polish: a single, cohesive design language (typography, spacing, colour, motion) replacing the current mix of glassmorphism, emoji icons, and ad-hoc spacing.
6. Every failure mode surfaces a clear cause and a single action the user can take to fix it.

### Non-goals

- No new analytics features. Everything must render from data the system already computes.
- No backend rewrite. Story generation is server-side Python on existing inputs.
- No Claude API calls inside the story engine (deferred to future Approach C).
- No mobile-first redesign. Current layout is desktop-primary; responsive behaviour preserved but not expanded.

---

## 2. Audience and Use Cases

**Primary user:** the owner, who runs the dashboard daily during market hours while IH scans and RR trades run.

**Secondary audience:** anyone shown the screen — friends, family, non-traders — who should be able to follow along without explanation.

**Daily flow:**
- Morning: open dashboard → novice view loads by default → story + tiles give instant read of market state → click into expert view only if a trade is live or a signal is forming and deeper inspection is wanted.
- The owner is expected to use novice view as the daily default. Expert view is a deliberate click-through, not a context switch.

---

## 3. Information Architecture

### 3.1 Two views, one shell

Both views share: **header** (logo, market status, last refresh, settings) and **story headline** (2–3 sentence narrative).

The "Novice / Expert" toggle lives in the header. Current value persists to `localStorage` as `dashboard_view_mode`. Default on first load is `novice`.

### 3.2 Novice view layout (default)

```
┌────────────────────────────────────────────────┐
│  HEADER (shared)                    [Expert ▸] │
├────────────────────────────────────────────────┤
│  STORY HEADLINE (shared, 2–3 sentences)        │
├────────────────────────────────────────────────┤
│  WHAT TO WATCH — 4 tiles (adaptive content)    │
│  [Mood]  [Trade/Signal]  [Battle Lines]  [Day  │
│                                         Bias ↔ │
│                                         Time]  │
├────────────────────────────────────────────────┤
│  ▾ SHOW MORE DETAIL                            │
│     • Simple spot line chart (no annotations)  │
│     • Day Bias breakdown (HDFC/KOTAK)          │
│     • Regime note (current regime + meaning)   │
└────────────────────────────────────────────────┘
```

Nothing below the "Show more" fold renders until the user expands it.

### 3.3 Expert view layout (click-through)

```
┌────────────────────────────────────────────────┐
│  HEADER (shared)                     [◂ Novice]│
├────────────────────────────────────────────────┤
│  STORY HEADLINE (shared)                       │
├────────────────────────────────────────────────┤
│  HERO: Verdict block + Strategy strip          │
│    • Big gauge · EMA · 2-candle confirm        │
│    • Spot · Momentum 9m                        │
│    • Regime capsule (RR) + Day Bias chip (IH)  │
│    • Multi-index mini-row: NF/BN/SX/HDFC/KOTAK │
├────────────────────────────────────────────────┤
│  V-SHAPE ALERT (conditional banner)            │
├────────────────────────────────────────────────┤
│  MARKET STATE (merged)                         │
│    PCR · IV Skew · Support · Resistance ·      │
│    Max Pain · ATM · Expiry · Vol PCR ·         │
│    Conviction — each with plain-English cap    │
│  SCORE BREAKDOWN (collapsible)                 │
├────────────────────────────────────────────────┤
│  FORCES (merged block)                         │
│    • Tug-of-war bar + zone totals              │
│    • Directional force ratios (inline)         │
│    • Flow classification (hover legend)        │
│    • Tabbed strike tables (Zone/OTM/ITM)       │
│    • Trap Warning inline header (conditional)  │
├────────────────────────────────────────────────┤
│  TRADES (IH-primary)                           │
│    • IH Signal Group card (hero)               │
│        per-index rows · LIVE/PAPER · agent     │
│        verdict · per-position SL/TGT/time-left │
│    • RR active-trade card (secondary)          │
│    • 30-day Stats (tabbed: IH / RR)            │
├────────────────────────────────────────────────┤
│  CHARTS (consolidated)                         │
│    • OI Score chart (primary, period toggle)   │
│    • Strike-zone chart + selector              │
│        (OTM Puts / OTM Calls / ITM Puts /      │
│         ITM Calls / Cumulative)                │
│    • Futures Basis chart                       │
└────────────────────────────────────────────────┘
```

### 3.4 Panel mapping — current → new

| Current panel | Fate | Destination |
|---|---|---|
| Verdict card | Kept, upgraded | Expert hero |
| 5 hero metrics (Spot / PCR / IV Skew / Trend / Momentum 9m) | Partially merged | Spot + Momentum stay in hero; PCR + IV Skew move to Market State; Trend removed (redundant with Verdict) |
| Regime capsule | Kept | Expert hero (RR-only) |
| V-Shape alert | Kept | Expert, conditional banner |
| Score Breakdown | Kept, collapsed | Below Market State |
| Trade Dock | Replaced | IH Signal Group card + RR card |
| Analysis Details (7 metrics) | Merged | Market State |
| Futures Data + sparkline | Kept | Market State adjacent |
| Trade Carousel | Removed | (dead code after strategy removals) |
| Win Rate card | Kept, tabbed | 30-day Stats with IH/RR tabs |
| CALL Entry Signal Alert | Removed | Replaced by Signal Forming tile state |
| Trap Warning card | Folded inline | Forces block header |
| Zone Force Comparison | Merged | Forces block |
| Directional Force Analysis | Merged | Forces block |
| OI Flow Classification | Merged | Forces block (hover legend) |
| Tabbed Tables | Kept | Forces block bottom |
| OI Score chart | Kept | Charts |
| Cumulative OI chart | Merged | Strike-zone chart (selector option) |
| OTM chart | Merged | Strike-zone chart (selector option) |
| ITM chart | Merged | Strike-zone chart (selector option) |
| Futures Basis chart | Kept | Charts |

Net: **15 panels → 8 blocks. 4 OI sub-charts → 1 chart with selector.** No data loss.

---

## 4. Story Engine

### 4.1 Module and contract

**New module:** `analysis/narrative.py`

```python
def build_story(
    analysis: AnalysisResult,
    regime: str,              # RR regime label
    day_bias: float | None,   # IH day bias
    ih_state: IHStoryState,   # see Section 4.3
    rr_state: RRStoryState,
    warnings: list[Warning],  # see Section 4.5
) -> Story:
    ...
```

Return value:

```python
@dataclass
class Story:
    sentences: list[str]   # 2 or 3 sentences
    warning: Warning | None  # if any input failed, this is set instead of sentences
```

### 4.2 Sentence structure — fixed 3-slot composition

| Slot | Purpose | Always present? |
|---|---|---|
| State | What is happening right now | Yes |
| Pressure | Where the battle lines are, who is winning | Yes |
| Outlook | What to expect next | Only when `abs(verdict) >= 30` AND `regime != LOW_VOL` |

### 4.3 Inputs consumed

| Input | Source |
|---|---|
| Regime label | `RR_REGIME_PARAMS` classifier |
| Spot vs prev close (direction + pct) | `analysis.spot`, previous close from `nifty_history` |
| Support (highest Put OI below spot) | `analysis.support` |
| Resistance (highest Call OI above spot) | `analysis.resistance` |
| Verdict score, EMA | `analysis.verdict_score`, `verdict_ema` |
| Momentum 9m | `analysis.momentum_9m` |
| PCR, IV Skew, Max Pain drift | from `analysis` |
| Day Bias score | `IntradayHunterEngine.day_bias` |
| IH signal group state | `none` / `forming` / `live` / `recently_closed` |
| IH agent verdict | `HOLD` / `TIGHTEN_SL` / `EXIT_NOW` / `null` |
| IH alignment flags | `{nifty, bn, sx}` booleans |
| IH detector armed | `E1` / `E2` / `E3` / `null` |
| RR active trade (symbol, entry, P&L%) | `rr_trades` latest open |

### 4.4 Template catalogue — per regime, per slot, 3–5 variants

Templates stored as Python dicts in `analysis/narrative.py`. Example subset:

```python
STATE_TEMPLATES = {
    ("TRENDING_UP", "strong"): [
        "Market is rallying — up {pct}% from open.",
        "NIFTY is pushing higher, now +{pct}% on the day.",
        "Bulls in control; spot at {spot}, +{pct}% from open.",
    ],
    ("TRENDING_UP", "mild"): [
        "Market is drifting up from {open_price}.",     # {open_price} = today's 09:15 open
        "NIFTY edging higher — slow but steady move up.",
    ],
    ("TRENDING_DOWN", "strong"): [...],
    ("HIGH_VOL_UP", "any"): [...],
    ("HIGH_VOL_DOWN", "any"): [...],
    ("NORMAL", "small"): [...],
    ("LOW_VOL", "any"): [...],
}

PRESSURE_TEMPLATES = {
    ("spot_near_support", "force_bullish"): [
        "Put sellers confident below {support} and absorbing pressure.",
        ...
    ],
    ...
}

IH_STATE_TEMPLATES = {
    "forming": [
        "Trap forming on {aligned}; {lagging} lagging. Watching {detector} for confirm.",
        ...
    ],
    "live": [
        "IH holding {n} position{s}; net {pnl_signed} unrealised. Agent says {verdict_plain}.",
        ...
    ],
    "recently_closed": [
        "IH closed group #{id_short} {ago_minutes}m ago: {net_result}.",
    ],
    "locked_out": [
        "IH paused for today — 2 losing days in a row.",
    ],
}
```

### 4.5 Variant selection — deterministic rules

The generator does **not** choose variants randomly. Variant choice is driven by data ranges, so the same input always produces the same story.

```
magnitude_bucket := pct in [-inf,-0.5) → "strong_dn"
                         [-0.5,-0.1) → "mild_dn"
                         [-0.1, 0.1] → "small"
                         ( 0.1, 0.5] → "mild"
                         ( 0.5, inf) → "strong"

spot_location := distance_to_support < 0.3 × distance_to_resistance → "near_support"
                 distance_to_resistance < 0.3 × distance_to_support → "near_resistance"
                 else → "centred"

spot_minute_bucket := floor(minute_of_day / 15)    # 0..25 across the session
variant_index      := hash((regime, state, spot_minute_bucket)) % len(variants)
```

The 15-minute bucket rotation prevents the story from reading identically on every 3-minute refresh within the same regime/state. It is still deterministic for a given minute and data set — same inputs always produce the same output.

### 4.6 Failure modes and actions

Each input has a failure mode. When any required input is missing or stale, `Story.warning` is set and `Story.sentences` is empty. The UI renders the warning card instead of the story.

| Failure | Message | Action |
|---|---|---|
| Kite token expired | *"Kite login expired."* | Button: **Re-authenticate** (opens `/auth/kite`) |
| NSE fetch timeout | *"NSE fetch failed this cycle."* | Button: **Retry now** (triggers `/api/refresh`) |
| Data stale >6 min | *"Last update 8m ago."* | Countdown: *"Next refresh in {s}s"* |
| Regime unknown | *"Still gathering data…"* | Progress: *"{n}/{min_required} candles ready"* |
| BN/SX tick feed stalled | *"BankNifty/Sensex feed not flowing."* | Button: **Reconnect ticks** |
| Backfill incomplete | *"Loading yesterday's NIFTY history…"* | Progress bar |
| IH agent timeout | *"IH agent not responding."* | Button: **Restart agent** |
| Day bias unavailable | *"Day bias pending — HDFC/KOTAK warming up."* | Countdown |
| Unhandled exception | *"Something went wrong."* | Buttons: **Copy error** · **View logs** |

### 4.7 Persistence

Each generated story is persisted to `analysis_history.story_text` (new column, nullable VARCHAR). This lets replay/backtest tooling reconstruct the same narrative a user would have seen.

### 4.8 Testing

- Unit tests in `tests/test_narrative.py`. Frozen inputs → expected sentence lists.
- One test per regime × magnitude bucket × IH state combination at minimum.
- Snapshot tests for the full `Story` object rendering across representative scenarios.

---

## 5. Tile System (Novice View)

### 5.1 Four fixed slots

| Slot | Label | Constant across states? |
|---|---|---|
| 1 | Mood | Yes — always NIFTY OI verdict mood |
| 2 | Trade / Signal | No — cycles based on IH state |
| 3 | Battle Lines | Yes — always NIFTY support / spot / resistance |
| 4 | Day Bias ↔ Time Left | Reshapes by state |

**Mood values (slot 1):** derived from `analysis.verdict_score` thresholds:

| Score range | Emoji | Label |
|---|---|---|
| `>= 60` | 🚀 | Bullish |
| `20 to 60` | 😊 | Mildly Bullish |
| `-20 to 20` | 😐 | Neutral |
| `-60 to -20` | 😬 | Mildly Bearish |
| `<= -60` | 😱 | Bearish |

### 5.2 Slot 2 content per state

| IH group state | Tile content | Accent colour |
|---|---|---|
| `waiting` (pre-09:35 or no trigger close) | *"⏸ Waiting"* · *"IH opens 09:35 · no group yet"* · *"Today: 0/1 groups"* | Blue (info) |
| `forming` (any E-detector armed, R29 not yet fired) | *"E{n} armed · R29 pending"* · per-index rows [NIFTY/BN/SX] with `✓ aligned` or `◦ lagging` · *"Needs R29 confirm · est {m} min"* | Amber (warn) |
| `live` (1–3 positions open) | *"{pnl_signed} · {pnl_pct}%"* · per-position mini-rows with LIVE/PAPER tag · *"Agent: {verdict} · TSL arming {pct}%"* | Green or red by P&L sign |
| `recently_closed` | *"Closed {ago}m ago"* · *"Group #{id_short}: {net_result}"* | Muted |
| `locked_out` | *"Paused today"* · *"2 losing days in a row"* | Red |

### 5.3 Slot 4 content per state

| State | Content |
|---|---|
| `waiting` / `forming` | **Day Bias** score + arrow + threshold note + HDFC/KOTAK breakdown on hover |
| `live` | **Time Left** per position: *"NIFTY 28m · BN 32m"* + *"EOD exit 15:15"* |
| `recently_closed` | **P&L summary** for last group |

### 5.4 Tile contract

Each tile is a vanilla-JS template rendered from a plain object (no framework). The object shape:

```
{
  slot: 1 | 2 | 3 | 4,
  primary: string,             // big number / label
  caption: string,              // sub-line
  hint: string,                 // italic footer
  accent: "up" | "dn" | "warn" | "info" | "muted",
  rows?: Array<{left, right}>   // optional mini-rows (slot 2 forming/live)
}
```

The adaptation logic lives in a dedicated layer (`buildTileState(ihState, rrState, analysis)` in `static/scripts/tiles.js`), not inside the render templates. This keeps state derivation unit-testable independent of the DOM.

---

## 6. Visual Design Language (Variant A — Cool Professional)

### 6.1 Tokens

**Palette:**

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0c0e13` | Page background |
| `bg-raised` | `#131721` | Card surface |
| `border` | `#1e2430` | Default border |
| `border-strong` | `#253145` | Pills, interactive borders |
| `text-primary` | `#eef1f8` | Body text |
| `text-secondary` | `#8e94ac` | Captions |
| `text-muted` | `#6b7289` | Labels |
| `accent-up` | `#60e0a8` | Bullish / positive |
| `accent-dn` | `#ff7a82` | Bearish / negative |
| `accent-warn` | `#f5b963` | Caution / forming signal |
| `accent-info` | `#7cb7ff` | Neutral info |

**Typography:**

- Family: `"Inter", "Segoe UI", sans-serif`
- Scale: 22 / 15 / 13 / 11 / 9
- Weights: 400 (body), 500 (labels), 600 (metrics)
- Tracking: `-0.02em` on display sizes (≥18px); normal otherwise.
- Monospace ticks: `"JetBrains Mono", "SF Mono", "Consolas"` reserved for raw numbers in compact tables (strike tables, flow tables) where alignment matters.

**Spacing:**

4-pt grid: `4 · 8 · 12 · 16 · 24 · 32 · 48`. No spacing value outside this scale is permitted.

**Radius:**

`4 · 8 · 10 · 14`. Cards use `10`. Pills use `10` (full-round). Inputs use `8`.

**Elevation:**

Replace heavy glassmorphism blur with subtle inset highlights:
- Card: `1px solid border` + `0 1px 0 rgba(255,255,255,0.03) inset`
- Hover: `border-strong` + background tint `rgba(255,255,255,0.02)`
- No `backdrop-filter: blur(…)` on any surface.

**Motion:**

- Default: `150ms ease-out`.
- Value-change flash: `500ms` (highlight the tile briefly when its primary value changes by more than a threshold).
- Never animate on every tick; flash only on state change.

**Iconography:**

- Single family: **Lucide** (SVG, tree-shakable).
- Emoji retained only in two places: Mood tile face (😊/🚀/😬/😱) and the V-Shape alert banner.

### 6.2 Accessibility

- All text/bg combinations pass WCAG AA (4.5:1 body, 3:1 for ≥18px).
- Focus rings: `2px solid accent-info` with `2px offset`.
- No information conveyed by colour alone — up/dn arrows, ± signs, or words always accompany.

---

## 7. Data Flow and API Changes

### 7.1 Backend additions

- `analysis/narrative.py` — new module (see Section 4).
- `db/schema.py` — add `story_text TEXT` column to `analysis_history`.
- `db/legacy.py` — update `save_analysis()` to persist `story_text`.
- `strategies/intraday_hunter.py` — expose a `story_state()` method returning `IHStoryState` for the narrative engine.
- `strategies/rr_strategy.py` — expose a `story_state()` method returning `RRStoryState`.

### 7.2 New API endpoints

| Endpoint | Method | Returns |
|---|---|---|
| `/api/story` | GET | Latest `Story` object (sentences + optional warning) |
| `/api/tiles` | GET | Computed tile states for all 4 slots |
| `/api/ih/group` | GET | Current IH signal group (active or null) with per-index positions and agent verdict |
| `/api/multi-index` | GET | NIFTY/BN/SX/HDFC/KOTAK % change since open |

Existing `/api/latest` continues to serve verdict/analysis data. `story_text` is added to its payload so the novice view can render from a single call.

### 7.3 SocketIO events

- `story_update` — emitted on every 3-min analysis cycle (or immediately on IH state change).
- `tiles_update` — emitted whenever any tile's computed state changes.
- `ih_group_update` — emitted on IH signal group transitions (formed / position opened / position closed / group closed).

### 7.4 Frontend structure

`templates/dashboard.html` today is a single 45 KB file. Split it:

```
templates/
  dashboard.html            # shell only (header, story, view toggle, sections)
  partials/
    novice.html             # 4 tiles + show-more
    expert_hero.html
    expert_market_state.html
    expert_forces.html
    expert_trades.html
    expert_charts.html
static/
  styles/
    tokens.css              # Section 6.1 tokens (CSS vars)
    base.css                # typography, resets
    components.css          # card, tile, pill, table
    layout.css              # grid, spacing
  scripts/
    story.js                # fetches + renders /api/story
    tiles.js                # tile rendering + adaptation
    charts.js               # split from current chart.js — one concern per file
    view-toggle.js
```

Chart.js and SocketIO stay; no new frontend framework introduced.

---

## 8. Testing Strategy

| Layer | Tests |
|---|---|
| Narrative engine | Unit tests per regime × magnitude × IH state combination; snapshot tests for full `Story` rendering |
| Tile state builder | Unit tests — for each IH state, assert expected tile shape for all 4 slots |
| API endpoints | Integration tests for `/api/story`, `/api/tiles`, `/api/ih/group`, `/api/multi-index` |
| Failure modes | One test per failure row in Section 4.6, asserting warning + action surface correctly |
| Visual regression | Manual + screenshot checks; no Playwright suite |

Target: ≥95% coverage on `analysis/narrative.py` and tile state builder. Existing test suite must continue to pass.

---

## 9. Future Improvements (explicitly out of scope)

These are tracked here for when the balanced scope proves insufficient.

- **Approach C — Claude-generated story**: replace rule-based templates with a Claude prompt fed all metrics, run per 3-min cycle. Richer language, better edge cases. Estimated ₹20–50/day at current pricing, ~2–5s latency per refresh.
- **Fully adaptive tile slots**: the *set* of slots changes by state (e.g., during active trade the Mood slot becomes Exit Plan). Current design keeps slot identities fixed for familiarity.
- **Light-mode toggle** for novice view (for sharing / screenshots).
- **Regime timeline**: a horizontal strip showing how regime evolved today — useful for post-mortem review.
- **"Why the agent isn't trading" panel**: when IH or RR agents are idle, explain the gating condition in plain English.
- **Mobile-first layout**: currently responsive but not optimised.

---

## 10. Definition of Done

1. Novice view ships and loads by default; toggling to Expert persists.
2. Story engine produces deterministic, regime-aware, IH-aware narratives; covered by tests at ≥95%.
3. All four tiles render correctly across all five IH states (`waiting`, `forming`, `live`, `recently_closed`, `locked_out`).
4. Expert view is the restructured 8-block layout with the consolidated strike-zone chart.
5. Every metric on both views has a plain-English caption.
6. Visual tokens from Section 6.1 are applied consistently — no hard-coded colours, spacings, or radii outside the token set.
7. Every failure mode in Section 4.6 has been manually triggered (or simulated) and renders the specified warning + action.
8. Existing test suite passes unchanged.
9. Dashboard loads and reaches first meaningful paint in ≤1.5 s on a warm cache.
