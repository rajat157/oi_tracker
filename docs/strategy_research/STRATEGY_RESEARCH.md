# IntradayHunter Strategy Research — Consolidated Reference

> This document consolidates ~12 hours of research distilled from 79 YouTube videos
> into a single comprehensive reference. It supersedes (and replaces) the original
> findings_v1..v4 markdown files, transcripts, video frames, and grids that
> previously lived in this directory. See §10 for the cleanup history.

## Table of Contents

1. [Overview & Source Material](#1-overview--source-material)
2. [The Strategy](#2-the-strategy)
3. [Codified Rule Set](#3-codified-rule-set)
4. [Multi-Index Logic](#4-multi-index-logic)
5. [Operator Theory](#5-operator-theory)
6. [Backtest Results](#6-backtest-results)
7. [Live Runtime Implementation](#7-live-runtime-implementation)
8. [Helper Scripts (archived)](#8-helper-scripts-archived)
9. [Source Material — Video Inventory](#9-source-material--video-inventory)
10. [File Cleanup History](#10-file-cleanup-history)

---

## 1. Overview & Source Material

**Goal:** Reverse-engineer a Hindi-language Indian intraday options trader's
system from his public YouTube channel, then codify it into a backtestable +
live-tradeable strategy inside the `oi_tracker` project.

**Trader profile:**
- Hindi-language Indian intraday trader, public content from **Dec 2024 → Apr 2026** (1.5+ years)
- Trades **Bank Nifty + Sensex + Nifty options simultaneously**, **option BUYING only** (never sells)
- 1-minute charts on TradingView with broker-integrated order panel (real money)
- Lot-scaled sizing observed in videos: BN ~1050-1170 / SX ~840 / NF ~1275-1300 (ratio ~1.4 : 1 : 1.55)
- 2-3 trades per day max; 3+ losing days in a row do happen and are documented
- All trades are intraday morning trades (entries 9:30-12:00, exits before ~12:30)

**Material reviewed across 4 research rounds:**
- **R1 (17 videos):** 3 educational + 7 analysis + 7 live trade — full transcripts + 1,299 frames composed into 331 grids
- **R2 (4 videos):** 2 full educational + 2 transcript-only stubs (rest paywalled)
- **R3 (28 videos):** 10 analysis/live-trade pairs (Dec 2025–Mar 2026) + 8 educational
- **R4 (30 videos):** 13 historical analysis/live-trade pairs (Aug 2025–Dec 2024) + 6 educational
- **Total:** 79 Hindi transcripts (~860 KB cleaned) + 2,797 frames composed into 1,497 grids
- **Visual confirmation:** 30+ grids reviewed across all 4 rounds to validate annotation vocabulary

**Confidence levels at end of research:**

| Aspect | Confidence |
|---|---|
| Philosophy + workflow | 97% |
| Visual vocabulary | 97% (visually confirmed across 1.5y) |
| Entry rules | 92% (E1-E4 codified) |
| Exit rules | 92% (Phase F decision tree codified) |
| SL placement | 75% (predefined points, fuel rule clarified) |
| Multi-index logic | 95% (2-of-3 rule + capital concentration) |
| Operator theory | 95% |
| Time-of-day | 90% (12:30 PM transition confirmed) |
| Position sizing | 85% (BN evolved 1170 → 1050) |

---

## 2. The Strategy

### Core philosophy: 3-layer trap theory

The strategy operates on three nested layers:

1. **Surface (visible to retail):** "Find the trapped side, trade against it."
2. **Mid (institutional):** "Operators accumulate quietly at turning points, then send a microsecond burst to cascade-trigger retail SLs."
3. **Detection (the trader's edge):** "When MANY traders feel COMFORTABLE taking a trade because all signals agree, the operator goes the OPPOSITE way. Trade against the comfort."

The "trap" is the operator's tool. The "comfort rule" is the detection mechanism.
The "decent profit at a level" is the exit discipline that protects against operator handover.

### Visual vocabulary (confirmed across 1.5 years)

| Annotation | Meaning |
|---|---|
| Red horizontal line | Resistance level (R) |
| Green horizontal line | Support level (S) |
| Circled `B` / `[B]` | Buy zone marker |
| Circled `S` | Sell zone marker |
| `{1k}` | 1000-point SL/target budget reference |
| `[TRAP]`, `[FRIDAY]`, `[zone]` | Context tags |
| Diagonal arrows | Expected direction |
| Curved freehand line | Expected rally path |
| Standalone candle sketches in empty chart space | Teaching diagrams |
| TradingView dark theme + 1-min timeframe + bottom-left order panel | Working surface |

The trader uses **price action only** — no MACD, RSI, or moving averages. Volume Profile
appears occasionally as a secondary S/R hint on the futures chart but not as a signal.

### Workflow (3 phases)

#### Phase 1 — Pre-market analysis (recorded the evening before, 1.5–3 min)

Template (consistent across 17 analysis videos spanning Dec 2024 → Mar 2026):

```
Bank Nifty: [bias from yesterday]
  → If gap-up:    [SELL or BUY plan]
  → If flat:      [usually same as gap-up]
  → If gap-down:  [usually IGNORE or opposite]
  → R levels: X, Y    S levels: A, B    Psych number: Z

Sensex: [same template]
Nifty:  [same template]

"For your trade, please see the upcoming chart. Thank you."
```

**Regime classification (5 archetypes seen in pre-market videos):**

| Regime | He says | Default plan |
|---|---|---|
| Yesterday strong bullish | "Buyers in market, can't target them directly" | Gap-up/flat → BUY (follow); Gap-down → SELL (target buyers); Big gap-down → IGNORE |
| Yesterday strong bearish | "Sellers in profit, can't target them directly" | Gap-down/flat → SELL (follow); Gap-up → BUY (target sellers); Big gap-up → IGNORE |
| Yesterday sideways/chop | "Doubt — both sides got flushed" | Wait for first move; refuse direct trades |
| Friday / pre-holiday | "Profit booking risk; ops position to flush retail over weekend" | Reduce size or skip; expect spike-and-fail |
| Expiry day | "Treat the expiry chart SEPARATELY" | Don't blend expiry with next-day chart |

#### Phase 2 — Live execution (Phases A-F state machine)

**Phase A — Open observation (first 0–10 min)**
- Note gap size (small / medium / big / huge)
- Compare against pre-market plan
- Big gap not in plan → wait, watch first momentum

**Phase B — Wait for stability (2–5 candles)**

"Stability" is operationally:
1. First candle's momentum has happened (never enter on candle #1)
2. Either a small dip/retracement, OR a level held for 2-3 candles after initial move
3. Chart no longer making fast 200pt candles — has slowed

**[NEW v4] Quick-vs-Wait 3-question filter:**

| Question | Answer A → Quick | Answer B → Wait |
|---|---|---|
| What's existing momentum? | Slow (small wobbly candles) | Fast (200-300pt sudden) |
| What's multi-day trend? | Same direction as setup | Opposite to setup |
| Chart-vs-trade alignment? | Chart bullish + trade positive | Chart bullish but trade negative |

Need 2 of 3 "Quick" to enter immediately. Otherwise wait or skip.

**Phase C — Entry triggers (5 setups seen across 17+ live trades)**

| # | Trigger | Direction | Logic |
|---|---|---|---|
| **E0** | [NEW v6] Gap-rejection-recovery: yesterday directional, first 1-2 candles move sharply against prior close, then 2-3 recovery candles back toward yesterday's direction | OPPOSITE to first candle (= yesterday's direction) | "Buyers flushed by gap, now recovering" — trader's highest-conviction gap-day setup. Entry window 09:17-09:25, before TIME_START. Observed in 2026-04-09 live video. |
| **E1** | Small retracement after directional momentum, market holds prior level | WITH momentum | Big retracement = others enter; small = only us → operator's hand still in control |
| **E2** | Gap in opposite direction to trend, market doesn't break previous swing low/high | AGAINST gap | "Trap" — fresh sellers/buyers from gap will be squeezed |
| **E3** | Round-number breakdown without recovery, then retracement up to round number | WITH breakdown | Sellers below RN won't cut until level recrosses, retracement fails |
| **E4** | Three-index correlation: 2 indices move, 1 lags | Direction of the 2 | Lagger will catch up; entering with 2 is safe |

He does **NOT** enter on:
- Clean breakout with no retracement (too late)
- Direct momentum candle ("can't participate in 1-candle moves")
- All-3-indices ripping in one direction (no pullback = no edge)

**[NEW v4] The Fuel Rule:** It's not enough that your target side is in profit.
The OPPOSITE side must have nearby SLs to provide fuel.
*"जैसे आग के अंदर घी की आवश्यकता होती है, वैसे ही हमें यहां पे बायर के एसएल चाहिए थे"*
("Just as fire needs ghee, we needed buyer SLs here.")

**Phase D — Position build**

- BN first portion → BN second portion → SX → NF (6 separate orders)
- Total per trade: 2 BN portions + 1 SX + 1 NF = 4 positions across 3 indices
- Quantities: BN 1050-1170 / SX 840 / NF 1275-1300 (1.4 : 1 : 1.55 ratio)

**Staged entry (only if you're already a multi-lot trader):**
- 50% at entry
- 30% on small adverse move
- 20% on bigger adverse move
- Result: averaged-down entry → original target becomes a BIG profit

**Phase E — Hold and monitor**

- Watch for **chain-reaction warnings**: sudden 200-300pt counter-move = HARD WARNING (operator handover)
- Watch for **time decay**: 2-3 hours without momentum → exit at break-even
- Watch for **sideways consolidation**: small alternating candles = exit at small profit
- Allow normal noise (small green candles in a sell trade)

**Phase F — Exit decision tree**

| Market state | Nearby round number? | Action |
|---|---|---|
| Fast momentum (200-300pt sudden) | Within 30-50pt | Wait for round number, book 75%, hold 25% |
| Fast momentum | Not nearby | Book everything immediately |
| Slow momentum (small wobbly) | Within 30-50pt | Book at the round number |
| Slow momentum | Not nearby | HOLD for big target — averaging traders give liquidity |
| Either | Far from any level | Use time-based exit (3 hours) |

**Operator-zone big-target eligibility:**

| Zone type | Pattern | Big target? |
|---|---|---|
| 1-sided operator zone | Direct break (one big candle through level) | ✓ YES (operator hasn't fully accumulated) |
| 2-sided operator zone | Hold + break (multiple candles around level) | ✗ NO (already accumulated, "spent fuel") |
| Indeterminate zone | Sideways, no clear winner | ✗ NO (regular target only) |

**Time-based exit refinement:**
- Critical threshold: ~11:00 to ~12:30 IST — chart-personality transition window
- After ~12:30, morning-bias rules no longer apply

---

## 3. Codified Rule Set

### 3.1 Hard rules (45 total — these never break)

| # | Rule |
|---|---|
| R1 | 1-min chart for entries, never higher TF |
| R2 | 15-min/Day TF only for context |
| R3 | Options BUYING only |
| R4 | Trades 3 indices simultaneously when bias agrees |
| R5 | Position sizing: BN ~1170 / SX ~840 / NF ~1300 |
| R6 | Wait 2-5 candles after open before any decision |
| R7 | Don't enter on first momentum candle |
| R8 | Enter on REJECTION/retracement at a level, not breakout |
| R9 | Big gap → only follow new chart, ignore previous day |
| R10 | After fakeout above/below level, enter the reversal |
| R11 | Max 2-3 trades per day |
| R12 | Cut losses small — don't average |
| R13 | Book profit at first decent target — don't be greedy |
| R14 | Sideways days → zero/one trade max |
| R15 | Sharp ~300-400pt counter-moves are danger — exit |
| R16 | Friday/pre-holiday = special case (operators use Friday) |
| R17 | Always check all 3 indices for correlation |
| R18 | Round number priority: only 1000s and 500s |
| R19 | Predefined-loss discipline — hit your planned SL exactly |
| R20 | Time-based exit: 2-3 hours without momentum → exit |
| R21 | Comfort rule: when comfortable, market does opposite |
| R22 | Operator-detection: repeated S/R holds = operator zone |
| R23 | Operator handover: small candles around same level = exit |
| R24 | Counter-trend morning bias (uptrend → morning sells, vice versa) |
| R25 | No staged entry without prior multi-lot habit |
| R26 | Don't add risk to recover loss |
| R27 | Loss limit is a hard stop — no revenge trading |
| R28 | Don't fight the market in one day |
| R29 | Constituent stock confirmation: HDFC + KOTAK for BN |
| R30 | Expiry day chart is a separate animal |
| R31 | **The fuel rule** — opposite side must have nearby SLs |
| R32 | **Quick-vs-wait 3-question filter** |
| R33 | **Trend conflict overrides setup** — wait if trend opposes |
| R34 | **Opening zone rule** — opening at critical level → WAIT |
| R35 | **Concept vs setup** — SL hunting always works, setups can fail |
| R36 | **1-sided vs 2-sided operator zone** — big targets only in 1-sided |
| R37 | **Big profit cannot be DECIDED in advance** |
| R38 | **Fast vs slow momentum** — slow momentum is more profitable |
| R39 | **Multi-index 2-of-3 lag-rejection** — interpret as part of move |
| R40 | **Capital concentration matters more than index count** |
| R41 | **Stability under wins/losses** — maintain emotional neutrality |
| R42 | **2 days late, 2 days early** — market timing rule |
| R43 | **Capital-anxiety effect** — low capital → can't take profitable trades |
| R44 | **Newbie morning rule** — beginners shouldn't trade morning |
| R45 | **Don't fight the market in one day** — loss limit is HARD |

### 3.2 Soft rules (12 — judgment-based)

| # | Rule |
|---|---|
| S1 | "Decent target" ≈ 50pts normal, 70-100pts on big days |
| S2 | "Big gap" ≈ 200+ BN points (small <100, big >200, huge >500) |
| S3 | Round number priority: 1000s > 500s; ignore 100s, 200s, 800s |
| S4 | "Stability" = no fast 200pt candles for 2-3 candles after initial burst |
| S5 | News context filter (negative news → favor sells, vice versa) |
| S6 | Yesterday-loss → be more conservative today |
| S7 | Yesterday-profit → can take more risk today |
| S8 | Multi-day chop pattern (1up-1dn) → reduce trades |
| S9 | Yesterday-loss + today's expiry → conservative |
| S10 | 2-day weekend / pre-holiday → operator accumulation expected |
| S11 | Constituent stock confirmation: HDFC + KOTAK for BN, RIL for SX |
| S12 | "1up-1dn chop" pattern = directional system fails → reduce/skip |

### 3.3 No-trade filters (15)

1. First 5-10 minutes after open
2. Pure sideways markets
3. After missing a 1-candle directional move (no FOMO)
4. Conditions where only one side will participate
5. Big sharp opposing-direction candles during your position
6. News uncertainty / war / policy announcements
7. Round number is between you and the level you'd target
8. All 3 indices moving the same direction (no edge)
9. Multi-day chop pattern
10. After 3 consecutive losing days, take 1 day off
11. Time of day after ~12:30 PM (chart-personality transition)
12. Opening at/near a critical level
13. Multi-day trend opposes setup AND chart isn't holding
14. A 2-sided operator zone (only OK for regular target)
15. Beginner trader in morning

---

## 4. Multi-Index Logic

1. **BN is the primary instrument** — confirmed across all 17 lives. He always describes BN first, enters BN first, BN's chart is the master.

2. **Sensex and Nifty are correlation confirmation** — used to validate direction. Phrases: "ssx ne bhi yahi kiya", "nfy mein bhi same chart hai".

3. **2-of-3 rule:** If 2 of 3 indices show the same setup and 1 lags, it's STILL a valid trade. If all 3 are moving the same way → don't enter against (no edge).

4. **Capital concentration > index count:** When BN is the weak side and SX/NF are strong, the trade FAILS because his largest position drags. Weight by capital, not index count. The losing day `l6dmt7cLqKo` (21 JAN 2026) is the canonical proof.

5. **Entry order is fixed:** BN → BN second portion → SX → NF (6 orders for a 3-index trade).

6. **HDFC + KOTAK as comfort-rule detector** for BN. When HDFC + KOTAK + BN all break out together → "everyone feels comfortable buying" → REVERSAL incoming.

7. **2-of-3 lag-rejection:** The lagging index gives a small rejection BEFORE catching up. Don't exit on the lagger's rejection — wait for catch-up.

---

## 5. Operator Theory

### 5.1 What an "operator" is
- Institutional desk with 10-100x retail capital
- Splits capital into UNITS (10L, 20L, 50L) and feeds them at microsecond intervals
- This is why operator orders create cascading SL hits — single decisive order vs hundreds of fragmented retail orders
- Example: 10M retail vs 1M operator → operator wins because it's one decisive order

### 5.2 How operators enter
- At price turning points — wherever the chart visibly reverses, an operator pushed it
- Wait for retail to provide liquidity → take the position quietly → push
- Never let price come back to entry zone (so if you see a level hold for 2-3 days, that's where the operator sits)

### 5.3 How operators exit
- After breakdown of a key level (round number, swing high/low)
- After breakdown, gradually distributes via small green-red-green-red candles to retail thinking "support is forming"
- Exit signature: "small wobbly candles around the same level" after a strong move
- Once exit complete → market reverses or stalls

### 5.4 Operator vs Operator
- Multiple operators exist simultaneously
- Bigger operator can squeeze a smaller operator's positions
- "Operators don't fight retail directly — they fight smaller operators. Retail is just the fuel."

### 5.5 Retail vs Operator candle signature
- **Operator entry** → next day **gap down** (or up), market moves directly with no retracement to entry
- **Retail entry** → next day **opens at the same level**, market moves slowly with retracements

### 5.6 The "Comfort Rule"
- When MANY traders feel comfortable taking a trade (technicals + correlation all agree), the operator goes the OPPOSITE way
- Detection: ask "would the average trader feel comfortable buying here?" If yes → SELL.
- Example: HDFC + Kotak both broke out at 10:01 → "comfort for buyers" → he SOLD

### 5.7 The 3-question logical analysis
For any small move, ask:
1. Did buyers come because of support?
2. Was it sellers being squeezed (forced buying)?
3. Was it just profit booking from earlier sellers?

The answer determines the next direction. If you can't answer, don't trade.

### 5.8 Why option BUYING only
- Option buying needs **timely momentum** to make money
- "If you have technique, you can take more risk than your technique allows" — meaning he takes early entries despite risk because timing matters more than perfection
- If momentum doesn't come timely → exit at small profit/break-even, don't sit and bleed theta

### 5.9 Why Friday is special
- 2-day weekend = retail goes flat on Friday
- Operators specifically use Friday + holiday Mondays because they get less competition from other operators
- Setups before Friday close are amplified
- *"Opss apna position banake baith jata hai friday ko"* (operators build positions on Friday and sit)

### 5.10 The "2 days late, 2 days early" rule
- When you're WRONG, market moves AGAINST you 2 days early
- When you're RIGHT, market moves WITH you 2 days late
- In intraday you have no extension — you have to be right TODAY
- Why entry timing matters more than direction

### 5.11 The "5-day winrate" observation
- 4 of 5 days follow predictable patterns; 1 of 5 is chaos
- Trade the 4, sit out the 1 (matches the "skip after 3 losses" rule)

### 5.12 Concept vs Setup
- **CONCEPT:** SL hunting (always works — based on market psychology)
- **SETUP:** "Breakout → sell" or "support → buy" (can fail individually)
- When a setup fails, fall back to the concept

---

## 6. Backtest Results

### 6.1 Backtester setup

**File:** `scripts/backtest_intraday_hunter.py` (~1,300 lines, Config-driven)

**Data:** 1-min OHLC for 5 instruments × 563 trading days × 2.3 years (Jan 2024 → Apr 2026)
- NIFTY (token 256265, NSE INDICES)
- BANKNIFTY (token 260105, NSE INDICES)
- SENSEX (token 265, BSE INDICES)
- HDFCBANK (token 341249, NSE EQ) — for R29 comfort rule
- KOTAKBANK (token 492033, NSE EQ) — for R29 comfort rule
- ~210k rows per instrument, ~1.05M rows total
- Stored in generic `instrument_history` table (label, interval, timestamp, OHLCV)

**Pricing model:** Black-Scholes via `kite/iv.py` with VIX-based IV
- NIFTY IV = vix/100
- BANKNIFTY IV = vix/100 × 1.30
- SENSEX IV = vix/100 × 1.20

**Position sizing:** BN 1170 / SX 840 / NF 1300 (per `findings_v4` §1)

### 6.2 Phase B versions

| Version | Description | Align (vs 23 docs) | PF | WR | MaxDD | PnL (1-lot) |
|---|---|---|---|---|---|---|
| v1 | E1 only, no filters | low | <1.0 | — | huge | negative |
| v2 best | Sweep optimal, no entry-time fix | 17/23 | 1.10 | ~44% | — | — |
| v2 final | **entry_start = 09:35** breakthrough | 19/23 | 1.16 | 45.9% | 82,192 | 234,275 |
| v3 final | + E3 (slow drift) + R28 fix + day-bias score | 19/23 | 1.16 | 45.9% | 82,192 | 234,275 |
| v4 (walk-forward optimal) | day_bias_block 0.60, constituent_min 0.15, cooldown 60 | 19/23 | 1.19 | 46.7% | 82,192 | 266,875 |
| **v5 FINAL** | **+ R29 internal-split (BN-only skip on HDFC↔KOTAK divergence)** | **19/23** | **1.25** | **47.1%** | **58,412** | **261,654** |
| **v6 FINAL** | **V1 (E0) + V2 (multi-day regime) + V5 (NO_TRADE inertia)** | **19/23** | **1.29** | **47.8%** | **58,412** | **300,158** |

**The breakthrough finding:**
> Just changing `entry_start` from 09:30 to 09:35 jumped alignment from 73.9% to 82.6%.
> Win-rate-by-entry-minute analysis showed: 09:30 entries = 45% WR (random — entered on the very first candle); 09:35-09:50 entries = 55-83% WR (the trader's "stability window").
> **This is exactly what R6+R7 prescribe: "wait 2-5 candles, never enter on the first momentum candle." The trader's own rule is the strongest filter.**

### 6.3 Locked-in v5 final config

| Parameter | Value | Notes |
|---|---|---|
| `entry_start` | **09:35** | THE breakthrough setting |
| `entry_end` | 11:30 | Morning-only |
| `time_exit` | 12:30 | Phase F time exit |
| `e1_run_length` | 5 | |
| `e1_retracement_max_pct` | 0.4 | |
| `e2_enabled` | True | gap counter-trap |
| `e2_min_gap_pct` | 0.20 | |
| `e2_wait_minutes` | 15 | |
| `e3_enabled` | True | trend continuation / slow drift |
| `e3_min_yclose_pct` | 0.10 | yesterday's |move| ≥ 0.10% |
| `e3_min_gap_pct` | 0.10 | |
| `e3_wait_minutes` | 20 | |
| `e3_max_current_pct` | 0.60 | |
| `multi_index_min` | 2 | 2-of-3 |
| `sl_pct` | 0.20 | 20% premium SL |
| `tgt_pct` | 0.45 | 45% premium target |
| `enable_one_dir_per_day` | ON | R45 |
| `enable_cooldown` | ON | 60 min after win, 30 after loss |
| `enable_loss_circuit` | ON, threshold 4 | R28 (trader violates strict 3) |
| `enable_vix_iv` | ON | BN ×1.30, SX ×1.20 |
| `enable_constituent_confluence` | True | R29 BOTH-veto |
| `constituent_min_pct` | 0.15 | walk-forward optimal |
| `enable_constituent_internal_split` | True | NEW v5 |
| `constituent_internal_split_pct` | 0.30 | \|HDFC% - KOTAK%\| ≥ 0.30% |
| `enable_day_bias` | True | frozen score, soft veto |
| `day_bias_block_score` | 0.60 | walk-forward optimal |
| `max_trades_per_day` | 3 | |
| `daily_loss_limit` | Rs 3,000 | scaled for 1 lot |

### 6.4 v6 additions (from 2026-04-09 replay analysis)

Variant backtest over 2.3 years (563 days) comparing 5 proposed improvements
against the v5 baseline. Full report archived from `2026-04-09_replay/VARIANT_REPORT.md`.

**Accepted:**

| Variant | Description | Δ PnL | Decision |
|---|---|---|---|
| V1 — E0 detector | Gap-rejection-recovery trigger, entry 09:17-09:25, 0.10% min recovery | +Rs 11,918 | IMPLEMENT |
| V2 — Multi-day regime | Yesterday's close-position-within-range as day_bias input | +Rs 7,030 | IMPLEMENT |
| V5 — NO_TRADE inertia | 5-min cooldown per direction after agent rejection | +Rs 7,485 | IMPLEMENT |

**Rejected:**

| Variant | Description | Δ PnL | Decision |
|---|---|---|---|
| V3 — Tick-level SL in backtest | Intra-candle SL check via BS model | -Rs 25,707 | REJECT (BS false-positives; real fix is ExitMonitor in live layer) |
| V4a — Expiry skip | Skip expiring index on its expiry day | -Rs 76,462 | REJECT |
| V4b — Expiry tight | 15% SL / 30% target on expiry days | -Rs 43,669 | REJECT |

**Combined V1+V2+V5 vs baseline:** +Rs 38,581 (+14.8%), PF 1.29 (+0.04), 7 more wins, 10 fewer losses, no alignment regression.

### 6.5 Things that were tried and REJECTED

- **Trend-conflict filter (R33)** — too strict, killed alignment
- **Opening-zone filter (R34)** — no measurable benefit
- **Gap-breakout block** — alignment dropped 19→17
- **R29 EITHER-veto** (instead of BOTH) — alignment dropped to 13/23 (catastrophic)
- **Day-bias threshold < 0.50** — blocked good signals on multiple days

### 6.5 Why we can't break 19/23 with mechanical filters

Each persistent miss (4 of 23) has a distinct cause:

1. **2025-10-15** — Bullish day, backtest shorts on early dip. The "early dip" pattern triggers E1 SELL even though macro is bullish. Every veto strong enough to catch this kills 2-3 good trades on other days.
2. **2025-12-22** — BN underperforms NF/SX. Backtest direction is RIGHT but BN drags daily P&L. Could be fixed by per-instrument SL adjustments.
3. **2026-01-14** — Mild gap-down with marginal recovery. Day's net move ~+0.08%. Probably unwinnable for option BUYING regardless of direction.
4. **2026-01-21** — Backtest is correct, human was wrong. Counted as "miss" in alignment but is actually correct execution.

The trader's own win rate on these 23 days is roughly 20/23 (3 documented losses).
**19/23 is just 1 short of his ceiling and includes correctly identifying 2 of his 3 loss days.**

---

## 7. Live Runtime Implementation

### 7.1 Phase C+D status: COMPLETE

The strategy is fully wired into the live `oi_tracker` runtime as of 2026-04-09.

**Files added/modified for the runtime:**

```
strategies/
├── intraday_hunter_engine.py    # Pure signal logic — E1/E2/E3 detection,
│                                  R29 confluence, day-bias score, position sizing
├── intraday_hunter.py           # BaseTracker subclass — lifecycle, multi-position
│                                  trade creation, real broker order placement, agent
└── intraday_hunter_agent.py     # Claude subprocess — confirm_signal + monitor_position

config.py                         # IntradayHunterConfig dataclass
db/schema.py                      # IH_TRADES_DDL + indexes

monitoring/
├── candle_builder.py             # Generalized for BN/SX/HDFC/KOTAK + register_option_strike
├── scheduler.py                  # 1-min IH job + multi-imap + IH wiring
└── startup_backfill.py           # Auto-fills instrument_history at app startup

kite/
├── auth.py                       # ensure_authenticated() — auto Kite login at startup
├── instruments.py                # MultiInstrumentMap (NIFTY/BN NFO + SENSEX BFO)
├── data.py                       # fetch_token_candles() generic helper
├── broker.py                     # exchange parameter (NFO/BFO)
└── order_executor.py             # quantity/exchange/instrument_map per call

app.py                            # Auth → backfill → scheduler chain at startup

tests/
├── test_strategies/test_intraday_hunter_engine.py    # 41 tests
├── test_strategies/test_intraday_hunter.py           # 27 tests
└── test_intraday_hunter_runtime.py                   # 24 tests
```

**Test count: 518 tests passing** across the full suite.

### 7.2 Runtime architecture

1. **App startup** (`app.py:start_app`):
   - Background thread waits 2s for Flask to bind
   - Calls `ensure_authenticated()` — checks DB token, opens browser if needed, polls until token captured via `/kite/callback`
   - Calls `backfill_recent_history()` — fills missing 1-min candles for all 5 IH instruments up to yesterday (capped 14 days)
   - Calls `oi_scheduler.start()`

2. **Scheduler startup** (`monitoring/scheduler.py:start`):
   - Refreshes the NIFTY-only `InstrumentMap`
   - Registers NIFTY (256265) with `CandleBuilder` for 1-min + 3-min
   - If `INTRADAY_HUNTER_ENABLED=true`, also registers BANKNIFTY/SENSEX/HDFCBANK/KOTAKBANK for 1-min
   - Refreshes the `MultiInstrumentMap` (downloads NFO + BFO option chains)
   - Starts WebSocket via `TickHub`
   - Schedules the 3-min OI poll job (existing)
   - Schedules the **1-min IH job** at next minute :05s

3. **3-min OI cycle** (existing): Tug-of-war analysis, RR strategy. **IH does NOT run here** (moved to its own 1-min cycle).

4. **1-min IH cycle** (`OIScheduler._run_intraday_hunter_minute`):
   - Builds minimal `analysis` dict with NIFTY/BN/SX/HDFC/KOTAK 1-min candles (filtered to today's session via `_today_only`)
   - Calls `ih.check_and_update(...)` for active position monitoring
   - Calls `ih.should_create(...)` → `ih.evaluate_signal(...)` → `ih.create_trade(...)` for entries

5. **Signal flow** (`IntradayHunterStrategy.evaluate_signal`):
   - Engine runs E1/E2/E3 detection on the latest minute (using `min(buffer_lengths)` so all indices align)
   - If a signal fires, agent confirms via `agent.confirm_signal()` (Claude subprocess)
   - If agent agrees, returns position set (2-3 positions); strategy creates DB rows + places real broker orders for indices in `IH_LIVE_INDICES`

6. **Position monitoring** (`IntradayHunterStrategy.check_and_update`):
   - Per active position: re-prices via `_get_current_premium()` priority chain — CandleBuilder LTP > option chain LTP (BS fallback removed from live in v6)
   - Mechanical exits: SL/TGT/12:30 TIME_EXIT/15:15 EOD_FORCE
   - **Tick-level SL/TGT via ExitMonitor** — registered AFTER live order fills (corrected fill prices, not BS estimates)
   - **Batched agent monitoring** — single Claude subprocess call for all active positions (not 3 sequential calls), throttled to once per 180s: HOLD / TIGHTEN_SL / EXIT_NOW

### 7.3 Configuration

```bash
# .env
INTRADAY_HUNTER_ENABLED=true        # master switch — registers strategy
IH_AGENT_ENABLED=true               # Claude agent for confirmation + monitoring
IH_LIVE_INDICES=NIFTY,BANKNIFTY,SENSEX  # which indices place real orders
IH_LOTS=1                           # lot multiplier (default per-index: NF 65, BN 30, SX 20)

# Live trading master gate
LIVE_TRADING_ENABLED=true
LIVE_TRADING_STRATEGIES=intraday_hunter,rally_rider  # or empty = all
```

**Double-gate for live orders:** A position goes live ONLY when ALL of:
1. `LIVE_TRADING_ENABLED=true`
2. `intraday_hunter` is in `LIVE_TRADING_STRATEGIES` (or it's empty)
3. The position's index is in `IH_LIVE_INDICES`

### 7.4 Database schema

```sql
CREATE TABLE ih_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_group_id TEXT NOT NULL,        -- UUID grouping NF/BN/SX positions
    created_at DATETIME NOT NULL,
    index_label TEXT NOT NULL,            -- 'NIFTY' / 'BANKNIFTY' / 'SENSEX'
    direction TEXT NOT NULL,              -- 'BUY' or 'SELL'
    strike INTEGER NOT NULL,
    option_type TEXT NOT NULL,            -- 'CE' or 'PE'
    qty INTEGER NOT NULL,
    entry_premium REAL NOT NULL,
    sl_premium REAL NOT NULL,
    target_premium REAL NOT NULL,
    spot_at_creation REAL NOT NULL,
    iv_at_creation REAL,
    vix_at_creation REAL,
    trigger TEXT,                         -- 'E1' / 'E2' / 'E3'
    day_bias_score REAL,
    notes TEXT,
    status TEXT DEFAULT 'ACTIVE',         -- ACTIVE / WON / LOST
    resolved_at DATETIME,
    exit_premium REAL,
    exit_reason TEXT,                     -- SL_HIT / TGT_HIT / TIME_EXIT / EOD_FORCE / AGENT_EXIT
    profit_loss_pct REAL,
    profit_loss_rs REAL,
    max_premium_reached REAL,
    min_premium_reached REAL,
    last_checked_at DATETIME,
    last_premium REAL,
    order_id TEXT,
    gtt_trigger_id INTEGER,
    actual_fill_price REAL,
    is_paper INTEGER DEFAULT 1
)
```

### 7.5 Architecture decisions

1. **Multi-position trades via `signal_group_id`** — each "trade" is a UUID-grouped set of up to 3 positions sharing the same group id. Avoids modifying BaseTracker's single-instrument assumption.

2. **R29 internal-split** — when HDFC and KOTAK strongly disagree (|h - k| ≥ 0.30%), the BN component is skipped but NF + SX still trade.

3. **Real LTP only in live** — BS fallback removed from live path (v6). CandleBuilder real LTP is the sole pricing source. BS was 35% off on 2026-04-10 (195 vs 144), causing a race condition with ExitMonitor. BS remains available for backtesting via `live_mode=False`.

4. **Default to paper** — `is_paper=1` until BOTH live gates pass.

5. **Per-index exchange routing:** NIFTY/BANKNIFTY → NFO, SENSEX → BFO. Handled by `MultiInstrumentMap` + `OrderExecutor` per-call exchange parameter.

6. **Engine convention:** `minute_idx` is the EXCLUSIVE upper bound for the retracement slice. `minute_idx == len(candles)` is valid; `> len(candles)` is capped down.

7. **1-min cycle uses smallest buffer length** so all 3 indices are queried at the same offset (different bootstraps may produce different lengths during the morning).

---

## 8. Helper Scripts (archived)

The following scripts were used during the research phase to download and process YouTube videos. They have been **deleted** as part of the cleanup, but their purposes are documented here in case anyone needs to re-run the research process.

### 8.1 `extract_frames.sh`
- Bash script that calls `ffmpeg` to extract JPG frames from each downloaded video
- Frame intervals by category:
  - educational: 5s (every chart action matters)
  - analysis: 5s (short videos)
  - live_trade: 8s (longer with repetition)
- Output: `<video_id>/frames/frame_NNNN.jpg`
- Maintains a CATEGORY associative array of all 79 video IDs mapped to category

### 8.2 `compose_grids.py`
- Python + PIL script that composes extracted frames into 2x2 grids (1280x720, 4 cells of 640x360)
- Each cell has a yellow timestamp label in the top-left for navigation
- Output: `<video_id>/grids/grid_NNN.jpg`
- Reduces 2,797 frames into 1,497 grids (4x compression for Claude image reads)

### 8.3 `clean_transcripts.py`
- Python script that converts SRT/VTT auto-captions to clean continuous text
- Handles YouTube's rolling repetition (each cue contains 2-3 lines of overlapping text)
- Computes longest common suffix-prefix overlap and emits only the new tail
- Hard-wraps at 140 chars on word boundaries (Hindi auto-captions have no punctuation)
- Output: `clean/<video_id>.txt`
- Processes 79 transcripts to ~860 KB total cleaned text

### 8.4 `extract_pairs.py`
- Small helper to extract analysis/live-trade pairs from `channel_listing.txt`
- Used for R3/R4 round planning

### 8.5 Re-running the pipeline (for future reference)

If you need to re-do video research from scratch:

1. **Download videos:** `yt-dlp` with cookies + `--write-auto-subs --sub-lang hi` to get videos + Hindi auto-captions
2. **Clean transcripts:** Re-implement `clean_transcripts.py` from §8.3 description
3. **Extract frames:** ffmpeg with `-vf "fps=1/N" -q:v 4`
4. **Compose grids:** PIL 2x2 layout with timestamp labels
5. **Read with Claude:** Pass grids + cleaned transcripts to Claude for synthesis

The total disk usage of the original research artifacts was **~1.3 GB** (mostly video frames + grids).

---

## 9. Source Material — Video Inventory

79 videos categorized across 4 research rounds. Format: `<video_id>` — content type — date (if known) or note.

### Round 1 (17 videos — full review)

**Educational (3):**
- `ZLpWNw34zGQ` — TF guidance (1-min for entries)
- `ywHZfvKsy5Q` — base concepts
- `Jg-suh_OVo0` — option buying / sizing

**Analysis (7):**
- `6b9ONxOgw7s`, `gg-3fzqrxBM`, `lXmBKdpcyn0`, `pD8ei_TtyqU`, `Ogj8nDIPBos`, `vg4EG2ge8Ew`, `WZtg_BkH-0I`

**Live trade (7):**
- `HMq6c6KFsAA`, `aJqegbg4i4U`, `dwEXGf-NgJo`, `mhBpsps-V1Q`, `xsVlO_UD1bI`, `gI-d_QZQEnk`, `fBhRPek1t2A`

### Round 2 (4 videos — educational only)

- `lLjZXcJ-Lf4` — OPERATOR ENTRY AND EXIT (foundational for §5 operator theory)
- `mw9kXMUlwQg` — VOLUME PROFILE INDICATOR
- 2 transcript-only stubs (videos paywalled)

### Round 3 (28 videos)

**Analysis (10) — Dec 2025 → Mar 2026:**
- `T7Jg9XdPTXs` (22 DEC), `qDLSQ47-WS0` (14 JAN), `0brdSaSZfRw` (21 JAN), `R1MiGGqonMU` (28 JAN)
- `C8xnlNQUTBw` (09 FEB), `16rjR7ueMow` (12 FEB), `LYKC1AU5ceM` (16 FEB)
- `wcf47792mYE` (19 MAR), `fx1xHReAmHc` (23 MAR), `p5V9jmiG8QA` (25 MAR)

**Live trade (10) — paired with above:**
- `t5IjnQYMW2M` (22 DEC), `847dR7elGF4` (14 JAN), `l6dmt7cLqKo` (21 JAN — **LOSING DAY**, the canonical R40 capital concentration example)
- `2GKVOLagtn0` (28 JAN), `Jr9jmVkRrbc` (09 FEB), `0E7ERl3VWp8` (12 FEB), `dlNszLiqQYg` (16 FEB)
- `qMq2mbFVicE` (19 MAR), `tqIJ_TmL8IE` (23 MAR), `mr3k2HOyTfE` (25 MAR)

**Educational (8):**
- `aHtAI-1ROaE` — operator-detection rule
- `Pgcs53QuALk` — round number priority
- `8XhyyVJyFOU` — entry timing
- `jINi35gB8w4` — Friday/holiday edge + 3-question logic
- `zRCLKtB1sn8` — additional concepts

### Round 4 (30 videos)

**Analysis (13) — Aug 2025 → Dec 2024 historical depth:**
- `4tpItqzMrOI` (22 AUG), `wxA97DCODLI` (03 SEP), `TC2893bd1_c` (12 SEP)
- `aAyYYNUrr2k` (22 SEP), `Bzb51VVwfMM` (29 SEP), `4xbega5KUuw` (06 OCT)
- `MlZIQcC6oM0` (15 OCT), `FpQdfJhJBXo` (23 OCT), `kLi8VEbgMAI` (30 OCT), `WWuq3ZK-abE` (04 NOV)
- `Bzc9FJxnRqw` (13 DEC 2024 — **BIG PROFIT DAY**, +Rs 4.8L documented)
- `1ALCZ2eoZN0` (12 DEC 2024), `aWmLyqmHf1E` (11 DEC 2024)

**Live trade (13) — paired with above:**
- `qRsZ1v9WPs8`, `F6WyKRNdxSg`, `qd-_mu_bq3o`, `4gP_fx9fJZA`, `nMlPkvMPp3M`
- `PV68kpeCzZc`, `JB-iyD5GeHY`, `Us14nELxXQg` (23 OCT — documented LOSS day)
- `nQa5INK4lwg`, `--bVRDSn1DQ` (the "fuel rule" loss day)
- `_rGN5Nfv1Hs` (13 DEC 2024 — the 4.8L profit), `uN5m6oky72g`, `bdTas9f6O2s`

**Educational (12, of which 6 successfully downloaded — others paywalled):**
- `9DzRfK_WkxU` — How to make Big profit (R36 1-sided vs 2-sided operator zones)
- `HgocDNkyNR0` — How to Make Every Trade on Right Time (R32 quick-vs-wait, R44 newbie morning rule)
- `n6tI3ZOYs2o` — Stock Market Momentum (R35 concept vs setup, R42 timing rule)
- `HmCy4ae9BA4` — Build a Perfect Trading Plan
- `RSwWcMYWkf4` — Make Money in Option Trading
- `jPuXxt5bUTc` — Opening and Closing Prices (R34 opening zone rule)

### Post-research videos (used for live replay validation, not backtest scoring)

- `ebDbCMsoV0o` — analysis (08 APR 2026) — pre-market plan for 09 APR
- `JbnJUPwS_bs` — live trade (09 APR 2026) — gap-rejection-recovery BUY, PROFIT on all 3 indices. Led to E0 trigger discovery and v6 variant analysis.

### 23 documented days for backtest validation

The backtester scores against these 23 days where we have ground truth from the videos:

- **R3 (10 days):** 22 DEC 2025, 14 JAN, 21 JAN (L), 28 JAN, 09 FEB, 12 FEB, 16 FEB, 19 MAR, 23 MAR, 25 MAR 2026
- **R4 (13 days):** 22 AUG, 03 SEP, 12 SEP, 22 SEP, 29 SEP, 06 OCT, 15 OCT, 23 OCT (L), 30 OCT, 04 NOV 2025, 11 DEC, 12 DEC, 13 DEC 2024

**v5 final result: 19/23 aligned (82.6% — 1 short of trader's own 20/23 ceiling).**

---

## 10. File Cleanup History

### Before cleanup (2026-04-09)

```
docs/strategy_research/
├── 79 video subdirectories (each with frames/ and grids/) — ~1.1 GB
├── clean/ — 79 cleaned transcripts (~2.1 MB)
├── findings_v1.md (246 lines)
├── findings_v2.md (440 lines)
├── findings_v2_draft.md (124 lines)
├── findings_v3.md (507 lines)
├── findings_v4.md (463 lines) — source of truth
├── RESUME_PROMPT.md (563 lines) — mid-development snapshot
├── channel_listing.txt
├── urls.txt, urls_round2.txt, urls_round3.txt, urls_round4.txt
├── download.log, download_round2.log, download_round3.log, download_round4.log
├── clean_transcripts.py
├── compose_grids.py
├── extract_frames.sh
└── extract_pairs.py
```

**Total: ~7,981 files, ~1.3 GB**

### After cleanup

```
docs/strategy_research/
└── STRATEGY_RESEARCH.md  (this file)
```

**Total: 1 file, ~50 KB**

### What was deleted and why

| Deleted | Reason |
|---|---|
| 79 video subdirectories (frames + grids + mp4) | Intermediate research artifacts. Knowledge has been distilled here. Can be regenerated from YouTube + the helper scripts described in §8 if needed. |
| `clean/` (79 transcripts) | Same — intermediate. Quotes that mattered are in §3 (rules) and §5 (operator theory). |
| `findings_v1.md`, `v2.md`, `v2_draft.md`, `v3.md`, `v4.md` | Progressive drafts. v4 was the "superset" but riddled with version-history tags. This document is a clean rewrite. |
| `RESUME_PROMPT.md` | Mid-development snapshot, now stale (Phase C+D are complete). |
| `urls*.txt`, `channel_listing.txt` | Raw URL/channel listings. Video IDs preserved in §9. |
| `download*.log` | Operational logs from yt-dlp runs. |
| `clean_transcripts.py`, `compose_grids.py`, `extract_frames.sh`, `extract_pairs.py` | Helper scripts. Behavior documented in §8. Can be reconstructed. |

### What was kept

- **This file** — the consolidated strategy research reference.

### Second cleanup (2026-04-10)

```
docs/strategy_research/2026-04-09_replay/   (deleted)
├── analysis_ebDbCMsoV0o.mp4 + .hi.vtt + .info.json + transcript
├── live_JbnJUPwS_bs.mp4 + .hi.vtt + .info.json + transcript
├── frames/ (65 frames) + grids/ (17 grids)
├── agent_3day_run.txt (3-day agent backtest output)
├── agent_backtest_output.txt
├── REPLAY_ANALYSIS.md → findings folded into §2 (E0 trigger) + §6.4 (variant results)
└── VARIANT_REPORT.md → findings folded into §6.4
```

**What was preserved:** E0 trigger description (§2 Phase C), variant backtest results (§6.4), April 9 video IDs (§9), architecture updates (§7). Everything else was intermediate analysis artifacts.

### What lives elsewhere in the repo

- **Backtester:** `scripts/backtest_intraday_hunter.py` (the source of truth for v5 final config)
- **Live runtime engine:** `strategies/intraday_hunter_engine.py`
- **Live runtime strategy:** `strategies/intraday_hunter.py`
- **Live runtime agent:** `strategies/intraday_hunter_agent.py`
- **Backfill:** `monitoring/startup_backfill.py`
- **Historical data fetch:** `scripts/fetch_instrument_history.py`
- **Configuration:** `config.py` → `IntradayHunterConfig`
- **DB schema:** `db/schema.py` → `IH_TRADES_DDL`
- **Tests:** `tests/test_strategies/test_intraday_hunter*.py` + `tests/test_intraday_hunter_runtime.py`
