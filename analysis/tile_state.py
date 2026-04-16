"""Tile state classifier — converts raw market + strategy state into
tile-ready payloads. Frontend renders these directly without further
adaptation logic.

See docs/superpowers/specs/2026-04-15-dashboard-legibility-design.md (Section 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from analysis.narrative import (
    IHGroupState, IHStoryState, RRStoryState, classify_mood,
)


@dataclass
class TileState:
    slot: int                                        # 1..4
    primary: str                                     # big headline text
    caption: str                                     # secondary line
    hint: str = ""                                    # italic footer
    accent: str = "muted"                             # "up" | "dn" | "warn" | "info" | "muted"
    rows: list[dict] = field(default_factory=list)   # optional mini-rows


def _build_mood_tile(verdict_score: float, verdict_ema: float) -> TileState:
    mood = classify_mood(verdict_score)
    ema_arrow = "↗" if verdict_ema > verdict_score else ("↘" if verdict_ema < verdict_score else "→")
    return TileState(
        slot=1,
        primary=f"{mood.emoji} {mood.label}",
        caption=f"Score {int(verdict_score)} / 100 · EMA {ema_arrow}",
        hint=_mood_hint(mood.label),
        accent=mood.accent,
    )


def _mood_hint(label: str) -> str:
    return {
        "Bullish": "Strong upside lean",
        "Mildly Bullish": "Leaning up, not racing",
        "Neutral": "No clear direction",
        "Mildly Bearish": "Leaning down, not panicked",
        "Bearish": "Strong downside lean",
    }.get(label, "")


def _build_trade_tile(ih: IHStoryState) -> TileState:
    if ih.state == IHGroupState.WAITING:
        return TileState(
            slot=2,
            primary="⏸ Waiting",
            caption=f"IH opens 09:35 · no group yet",
            hint=f"Today: {ih.groups_today}/{ih.max_groups_today} groups",
            accent="info",
        )

    if ih.state == IHGroupState.FORMING:
        rows = [
            {"left": idx, "right": "✓ aligned" if aligned else "◦ lagging"}
            for idx, aligned in ih.alignment.items()
        ] or [{"left": "NIFTY", "right": "◦ waiting"}]
        return TileState(
            slot=2,
            primary=f"{ih.detector_armed or 'E?'} armed · R29 pending",
            caption="Trap forming across indices",
            hint="Needs R29 confirm",
            accent="warn",
            rows=rows,
        )

    if ih.state == IHGroupState.LIVE:
        total_pnl = sum(
            (p.get("current_premium", 0) - p.get("entry_premium", 0)) * p.get("quantity", 1)
            for p in ih.positions
        )
        entry_sum = sum(p.get("entry_premium", 0) * p.get("quantity", 1) for p in ih.positions)
        pnl_pct = (total_pnl / entry_sum * 100) if entry_sum else 0.0
        rows = [
            {
                "left": f"{p['index']} {p['strike']}{p['option_type']}",
                "right": f"{'+' if p['current_premium'] >= p['entry_premium'] else ''}"
                         f"₹{(p['current_premium'] - p['entry_premium']) * p.get('quantity', 1):,.0f}"
                         f" [{'LIVE' if not p.get('is_paper') else 'PAPER'}]",
            }
            for p in ih.positions
        ]
        sign = "+" if total_pnl >= 0 else ""
        return TileState(
            slot=2,
            primary=f"{sign}₹{total_pnl:,.0f} · {sign}{pnl_pct:.1f}%",
            caption=f"Agent: {ih.agent_verdict or 'HOLD'}",
            hint="TSL arming at +10%",
            accent="up" if total_pnl >= 0 else "dn",
            rows=rows,
        )

    if ih.state == IHGroupState.RECENTLY_CLOSED:
        return TileState(
            slot=2,
            primary=f"Closed {ih.ago_minutes or 0}m ago",
            caption=f"Group #{ih.group_id or '?'}",
            hint="",
            accent="muted",
        )

    # LOCKED_OUT
    return TileState(
        slot=2,
        primary="Paused today",
        caption="2 losing days in a row",
        hint="IH circuit breaker active",
        accent="dn",
    )


def _build_battle_lines_tile(spot: float, support: int, resistance: int) -> TileState:
    # Which wall is spot closer to?
    if spot is None or support is None or resistance is None:
        return TileState(slot=3, primary="— ← ? → —", caption="Levels unavailable", accent="muted")
    ds = spot - support
    dr = resistance - spot
    if ds < dr * 0.5:
        hint = f"{int(ds)} pts above support"
    elif dr < ds * 0.5:
        hint = f"{int(dr)} pts below resistance"
    else:
        hint = "Spot centred between defences"
    return TileState(
        slot=3,
        primary=f"{support} ← {int(spot)} → {resistance}",
        caption="Support / Spot / Resistance",
        hint=hint,
        accent="muted",
    )


def _build_slot_four_tile(ih: IHStoryState, momentum_9m: float) -> TileState:
    if ih.state == IHGroupState.LIVE and ih.positions:
        times = " · ".join(
            f"{p['index']} {p.get('time_left_minutes', 0)}m" for p in ih.positions
        )
        return TileState(
            slot=4,
            primary=times,
            caption="Per-position time left",
            hint="EOD exit 15:15",
            accent="info",
        )

    bias = ih.day_bias
    if bias is None:
        return TileState(
            slot=4, primary="—", caption="Day bias unavailable",
            hint="Waiting for HDFC/KOTAK data", accent="muted",
        )
    arrow = "↗" if bias > 0 else ("↘" if bias < 0 else "→")
    above = bias >= 0.60
    return TileState(
        slot=4,
        primary=f"{'+' if bias >= 0 else ''}{bias:.2f} {arrow}",
        caption="Above threshold · favours calls" if above else "Below threshold",
        hint="Hover for HDFC/KOTAK breakdown",
        accent="warn" if above else "muted",
    )


def build_tile_state(
    verdict_score: float,
    verdict_ema: float,
    spot: float,
    support: int,
    resistance: int,
    momentum_9m: float,
    ih_state: IHStoryState,
    rr_state: RRStoryState,
) -> list[TileState]:
    """Produce the four tile payloads in slot order (1..4)."""
    return [
        _build_mood_tile(verdict_score, verdict_ema),
        _build_trade_tile(ih_state),
        _build_battle_lines_tile(spot, support, resistance),
        _build_slot_four_tile(ih_state, momentum_9m),
    ]
