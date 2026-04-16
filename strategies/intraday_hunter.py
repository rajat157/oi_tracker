"""IntradayHunter — multi-index BN+NF+SX trap-theory strategy.

Extends BaseTracker. Unlike single-instrument strategies (RR, Iron Pulse, etc.),
each "trade" here is a SIGNAL GROUP of up to 3 positions (NIFTY + BANKNIFTY +
SENSEX) opened simultaneously. Each position has its own row in `ih_trades`,
linked by a UUID `signal_group_id`.

The R29 internal-split filter may skip the BN component, in which case the
group will only have 2 positions (NIFTY + SENSEX).

Phase B v5 backtest: 19/23 documented days aligned, PF 1.25, WR 47.1%, MDD Rs 58K
across 563 days (Jan 2024 → Apr 2026, 1-lot sizing).

ENV VARS:
    INTRADAY_HUNTER_ENABLED   : 'true' to register with scheduler
    IH_LIVE_INDICES           : comma-separated list of indices to trade live
                                (e.g., 'NIFTY,SENSEX'). Indices not listed
                                trade as paper. Empty = all paper.
    IH_LOTS                   : multiplier on per-index 1-lot quantity
"""

from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import datetime, time, date
from typing import Any, Dict, List, Optional, Tuple

from config import IntradayHunterConfig, LiveTradingConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from core.logger import get_logger
from db.schema import IH_TRADES_DDL, IH_TRADES_INDEXES

log = get_logger("intraday_hunter")

_cfg = IntradayHunterConfig()
_live = LiveTradingConfig()


class IntradayHunterStrategy(BaseTracker):
    """Multi-index trap-theory strategy.

    Lifecycle (per signal group):
        1. should_create() — checks time window, daily limits, cooldown,
           consecutive-loss circuit breaker
        2. evaluate_signal() — runs the engine on the latest 1-min candles
        3. create_trade() — opens up to 3 positions in one shot, all linked
           by a single signal_group_id
        4. check_and_update() — re-prices each open position, hits SL/TGT/TIME
           independently
    """

    tracker_type = "intraday_hunter"
    table_name = "ih_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = _cfg.MAX_TRADES_PER_DAY
    is_selling = False
    supports_pending = False

    # Per-index exchange (for broker order routing)
    INDEX_EXCHANGE = {
        "NIFTY": "NFO",
        "BANKNIFTY": "NFO",
        "SENSEX": "BFO",
    }

    def __init__(self, **kwargs):
        # Strategy-specific deps
        self._exit_monitor = kwargs.pop("exit_monitor", None)
        self._candle_builder = kwargs.pop("candle_builder", None)
        self._kite_fetcher = kwargs.pop("kite_fetcher", None)
        # Multi-index instrument map (NIFTY+BN+SX, NFO+BFO segments).
        # Used for symbol resolution + lazy option-strike registration.
        self._multi_imap = kwargs.pop("multi_instrument_map", None)
        # SocketIO instance for push updates; None in paper/standalone runs
        self.socketio = kwargs.pop("socketio", None)
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(IH_TRADES_DDL, IH_TRADES_INDEXES)
        self._engine = None
        self._agent = None  # lazy — built on first use to avoid import cycles
        # Per-position last-agent-monitor timestamp (for throttling agent calls)
        self._agent_last_check: Dict[int, datetime] = {}
        # [V5] Rolling agent decision history (most recent first) — passed
        # to agent.confirm_signal so Claude doesn't flip-flop minute-to-minute.
        self._recent_agent_decisions: deque = deque(maxlen=_cfg.AGENT_HISTORY_LENGTH)
        # [V5] Per-direction last-rejection time — short cooldown to avoid
        # burning agent calls in tight loops while a setup is invalid.
        self._agent_last_rejection: Dict[str, datetime] = {}
        # State tracking for story_state() — populated by engine during each cycle
        self._day_bias: float | None = None
        self._armed_detector: str | None = None
        self._alignment: dict[str, bool] = {}
        self._last_closed_group: dict | None = None    # {"group_id": str, "closed_at": datetime}
        self._locked_out: bool = False

    @property
    def engine(self):
        if self._engine is None:
            from strategies.intraday_hunter_engine import IntradayHunterEngine
            self._engine = IntradayHunterEngine(_cfg)
        return self._engine

    @property
    def agent(self):
        """Lazy-init the IntradayHunterAgent. None if AGENT_ENABLED=false."""
        if not _cfg.AGENT_ENABLED:
            return None
        if self._agent is None:
            from strategies.intraday_hunter_agent import IntradayHunterAgent
            self._agent = IntradayHunterAgent()
        return self._agent

    # ------------------------------------------------------------------
    # Should-create gate
    # ------------------------------------------------------------------

    def is_in_time_window(self, now: Optional[datetime] = None) -> bool:
        """Override BaseTracker.is_in_time_window to use the earlier
        E0_ENTRY_START when E0 is enabled. Outside that window, only the
        regular TIME_START applies.

        The engine's E0 detector self-gates by E0_ENTRY_START + E0_MAX_MINUTE,
        so non-E0 signals (E1/E2/E3) won't fire before TIME_START even though
        the cycle is allowed to run.
        """
        now = now or datetime.now()
        effective_start = _cfg.E0_ENTRY_START if _cfg.ENABLE_E0 else self.time_start
        return effective_start <= now.time() <= self.time_end

    def should_create(self, analysis: dict, **kwargs) -> bool:
        """Pre-signal gate: time window, daily limits, cooldown, circuit breaker."""
        if not _cfg.ENABLED:
            return False
        if self.trade_repo is None:
            return False

        now = datetime.now()
        if not self.is_in_time_window(now):
            return False

        # Don't open if any positions from the latest signal group are still active
        if self._has_open_positions():
            return False

        # Daily trade-group cap
        groups_today = self._count_signal_groups_today()
        if groups_today >= _cfg.MAX_TRADES_PER_DAY:
            return False

        # Daily loss limit
        if self._daily_pnl_rs() <= -_cfg.DAILY_LOSS_LIMIT_RS:
            return False

        # Cooldown after most recent closed trade
        if not self._cooldown_ok(now):
            return False

        # Consecutive-loss circuit breaker (R28)
        if self._consecutive_losing_days() >= _cfg.CONSECUTIVE_LOSS_SKIP:
            return False

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return False
        return True

    # ------------------------------------------------------------------
    # Signal evaluation — engine call
    # ------------------------------------------------------------------

    def evaluate_signal(self, analysis: dict) -> Optional[Dict]:
        """Run the engine and return a signal dict if conditions match.

        Reads candle buffers from `analysis`. The scheduler is responsible
        for populating these:
            analysis["nifty_1min_candles"]      → list of dict candles
            analysis["banknifty_1min_candles"]  → list of dict candles
            analysis["sensex_1min_candles"]     → list of dict candles
            analysis["nifty_yesterday_candles"] → list of dict candles
            analysis["hdfcbank_1min_candles"]   → list of dict candles  (optional)
            analysis["kotakbank_1min_candles"]  → list of dict candles  (optional)
            analysis["spot_price"]              → NIFTY spot
            analysis["banknifty_spot"]          → BANKNIFTY spot
            analysis["sensex_spot"]             → SENSEX spot
            analysis["vix"]                     → India VIX %
        """
        from strategies.intraday_hunter_engine import candles_from_dicts

        nifty_dicts = analysis.get("nifty_1min_candles") or []
        bn_dicts = analysis.get("banknifty_1min_candles") or []
        sx_dicts = analysis.get("sensex_1min_candles") or []
        y_dicts = analysis.get("nifty_yesterday_candles") or []
        h_dicts = analysis.get("hdfcbank_1min_candles") or []
        k_dicts = analysis.get("kotakbank_1min_candles") or []

        if not (nifty_dicts and bn_dicts and sx_dicts and y_dicts):
            log.debug("IH: missing candles", n=len(nifty_dicts), b=len(bn_dicts), s=len(sx_dicts), y=len(y_dicts))
            return None

        ny = candles_from_dicts(nifty_dicts)
        bn = candles_from_dicts(bn_dicts)
        sx = candles_from_dicts(sx_dicts)
        yd = candles_from_dicts(y_dicts)
        hd = candles_from_dicts(h_dicts) if h_dicts else []
        kd = candles_from_dicts(k_dicts) if k_dicts else []

        # Use the SMALLEST buffer length so all 3 indices are queried at
        # the same offset. Different indices may have different buffer
        # depths during the morning bootstrap window (NIFTY accumulates
        # live_candles across sessions; BN/SX/HDFC/KOTAK only have what
        # this session bootstrapped via Kite historical_data).
        # NB: detect_e1/e2 treat minute_idx as the EXCLUSIVE upper bound
        # of the retracement slice, so minute_idx = len() (not len()-1).
        lengths = [len(ny), len(bn), len(sx)]
        if hd:
            lengths.append(len(hd))
        if kd:
            lengths.append(len(kd))
        minute_idx = min(lengths)
        if minute_idx <= 1:
            return None

        signal = self.engine.detect(
            minute_idx=minute_idx,
            nifty_today=ny, bn_today=bn, sx_today=sx,
            nifty_yesterday=yd,
            hdfc_today=hd, kotak_today=kd,
        )
        if not signal:
            return None

        # [V1] When E0 is enabled, the time window is widened to start at
        # 09:17. Inside the early window (09:17-09:34), ONLY E0 signals are
        # eligible. E1/E2/E3 stay gated by the original 09:35 TIME_START
        # because R6+R7 backtests showed pre-09:35 entries are random.
        now_t = datetime.now().time()
        if _cfg.ENABLE_E0 and now_t < _cfg.TIME_START:
            if signal.trigger != "E0":
                return None  # only E0 allowed in the early window

        # Build the position set
        nifty_spot = float(analysis.get("spot_price", 0) or ny[-1].close)
        bn_spot = float(analysis.get("banknifty_spot", 0) or bn[-1].close)
        sx_spot = float(analysis.get("sensex_spot", 0) or sx[-1].close)
        vix = float(analysis.get("vix", 0) or 0)

        positions = self.engine.build_position_set(
            signal=signal,
            nifty_spot=nifty_spot,
            bn_spot=bn_spot,
            sx_spot=sx_spot,
            today=datetime.now().date(),
            vix_pct=vix,
            candle_builder=self._candle_builder,
            live_mode=True,  # skip BS fallback — require real LTP
        )
        if not positions:
            return None

        signal_data = {
            "signal": signal,
            "positions": positions,
            "vix": vix,
            "nifty_spot": nifty_spot,
        }

        # Optional Claude agent: confirm/reject the mechanical signal and
        # tell us which (if any) indices to skip. If the agent is disabled,
        # the mechanical signal stands as-is.
        agent = self.agent
        if agent is not None:
            # [V5] Cooldown — don't burn an agent call if we just rejected
            # this same direction within AGENT_REJECTION_COOLDOWN_SEC.
            now = datetime.now()
            last_rej = self._agent_last_rejection.get(signal.direction)
            if last_rej is not None:
                elapsed = (now - last_rej).total_seconds()
                if elapsed < _cfg.AGENT_REJECTION_COOLDOWN_SEC:
                    log.debug(
                        "IH agent call skipped (cooldown)",
                        direction=signal.direction,
                        elapsed=int(elapsed),
                        cooldown=_cfg.AGENT_REJECTION_COOLDOWN_SEC,
                    )
                    return None

            # [V5] Pass last 5 decisions so Claude can stay consistent with
            # its own recent reasoning. Convert deque → list (most recent first).
            recent_decisions = list(self._recent_agent_decisions)
            decision = agent.confirm_signal(signal_data, analysis, recent_decisions)

            # Always record the decision (whether confirmed or rejected) so
            # the next call has the history.
            now_str = now.strftime("%H:%M")
            if decision is None:
                self._agent_last_rejection[signal.direction] = now
                self._recent_agent_decisions.appendleft({
                    "minute": now_str,
                    "trigger": signal.trigger,
                    "direction": signal.direction,
                    "verdict": "NO_TRADE",
                    "confidence": 0,
                    "reasoning": "(NO_TRADE — see ih_agent log for details)",
                })
                return None

            # Confirmed
            self._recent_agent_decisions.appendleft({
                "minute": now_str,
                "trigger": signal.trigger,
                "direction": signal.direction,
                "verdict": "TRADE",
                "confidence": int(decision.get("confidence", 0) or 0),
                "reasoning": decision.get("reasoning", ""),
            })
            # Clear the rejection cooldown for this direction since we've
            # now accepted a trade in it.
            self._agent_last_rejection.pop(signal.direction, None)

            skip = set(decision.get("skip_indices") or [])
            if skip:
                signal_data["positions"] = [
                    p for p in positions if p["index_label"] not in skip
                ]
                if not signal_data["positions"]:
                    log.info("IH agent skipped all indices", skip=list(skip))
                    return None
                log.info("IH agent skipped some indices", skip=list(skip))
            signal_data["agent_confidence"] = decision.get("confidence", 0)
            signal_data["agent_reasoning"] = decision.get("reasoning", "")

        return signal_data

    # ------------------------------------------------------------------
    # Trade creation
    # ------------------------------------------------------------------

    def create_trade(
        self,
        signal: Any,
        analysis: dict,
        strikes_data: dict,
        **kwargs,
    ) -> Optional[int]:
        """Open up to 3 positions linked by a signal_group_id.

        Returns the FIRST trade id of the group (or None on failure).
        Subsequent positions in the same group share signal_group_id.
        """
        if not isinstance(signal, dict):
            return None

        sig = signal.get("signal")
        positions = signal.get("positions") or []
        vix = signal.get("vix", 0)
        nifty_spot = signal.get("nifty_spot", 0)

        if not sig or not positions:
            return None

        signal_group_id = uuid.uuid4().hex[:12]
        now = datetime.now()
        first_trade_id: Optional[int] = None

        for pos in positions:
            label = pos["index_label"]
            wants_live = _live.ENABLED and _cfg.is_index_live(label)
            is_paper_int = 0 if wants_live else 1
            pos["is_paper"] = bool(is_paper_int)

            tid = self.trade_repo.insert_trade(
                self.table_name,
                signal_group_id=signal_group_id,
                created_at=now,
                index_label=label,
                direction=pos["direction"],
                strike=pos["strike"],
                option_type=pos["option_type"],
                qty=pos["qty"],
                entry_premium=pos["entry_premium"],
                sl_premium=pos["sl_premium"],
                target_premium=pos["target_premium"],
                spot_at_creation=nifty_spot if label == "NIFTY" else 0,
                iv_at_creation=pos["iv"],
                vix_at_creation=vix,
                trigger=sig.trigger,
                day_bias_score=sig.day_bias_score,
                notes=sig.notes,
                status="ACTIVE",
                max_premium_reached=pos["entry_premium"],
                min_premium_reached=pos["entry_premium"],
                is_paper=is_paper_int,
            )
            if first_trade_id is None:
                first_trade_id = tid

            log.info(
                "IH position opened",
                trade_id=tid, group=signal_group_id, label=label,
                direction=pos["direction"],
                strike=f"{pos['strike']} {pos['option_type']}",
                entry=pos["entry_premium"],
                sl=pos["sl_premium"], tgt=pos["target_premium"],
                paper=bool(is_paper_int),
            )

            # Lazy-subscribe to this option strike via CandleBuilder so the
            # 1-min IH cycle can read real LTP for this position. Best-effort:
            # if the subscription fails, monitoring falls back to BS pricing.
            self._register_strike_for_monitoring(label, pos["strike"], pos["option_type"])

            # Place real broker order if this index is opted into live trading.
            # NOTE: ExitMonitor registration is deferred to the scheduler
            # (after create_trade returns) so it reads corrected fill prices
            # from the DB — not the BS-estimated entry premium.
            if wants_live and self.order_executor is not None:
                self._place_live_entry(tid, label, pos)
            else:
                log.info("IH PAPER position",
                         trade_id=tid, index=label,
                         strike=f"{pos['strike']} {pos['option_type']}",
                         entry=pos["entry_premium"],
                         source=pos.get("premium_source", "?"))

        # Single combined alert for the group
        alert = self._format_group_alert(signal_group_id, sig, positions, vix)
        self._publish(EventType.TRADE_CREATED, {
            "trade_id": first_trade_id,
            "signal_group_id": signal_group_id,
            "direction": sig.direction,
            "alert_message": alert,
        })
        self._emit_group_update()
        return first_trade_id

    # ------------------------------------------------------------------
    # Live order placement helpers
    # ------------------------------------------------------------------

    def _register_strike_for_monitoring(
        self, index_label: str, strike: int, option_type: str
    ) -> None:
        """Lazy-subscribe to the position's option strike on CandleBuilder.

        This makes real LTP available for _get_current_premium during the
        position's lifetime. Failure is non-fatal — the BS fallback will
        still work for monitoring.
        """
        if self._candle_builder is None or self._multi_imap is None:
            return
        try:
            child_imap = self._multi_imap.get(index_label)
            if child_imap is None:
                return
            expiry = child_imap.get_current_expiry()
            if not expiry:
                return
            self._candle_builder.register_option_strike(
                index_label=index_label,
                strike=strike,
                option_type=option_type,
                expiry=expiry,
                instrument_map=child_imap,
            )
        except Exception as e:
            log.error("register_option_strike failed",
                      index=index_label, strike=strike,
                      option_type=option_type, error=str(e))

    def _register_with_exit_monitor(
        self, trade_id: int, index_label: str, pos: dict
    ) -> None:
        """Register an open position with the ExitMonitor for tick-level SL/TGT.

        Mirrors RRStrategy._register_trade_with_monitor but built for IH's
        multi-position groups (each position registers independently with
        the same trade_id used in ih_trades).

        Without this, IH positions are only re-priced once per minute by
        the IH cycle — meaning SL/target hits can blow through significantly
        past their levels (e.g. the 2026-04-09 SENSEX -73% slippage on a
        nominal 20% SL).
        """
        if self._exit_monitor is None or self._multi_imap is None:
            return
        try:
            from monitoring.exit_monitor import ActiveTrade

            child_imap = self._multi_imap.get(index_label)
            if child_imap is None:
                log.warning("IH ExitMonitor reg skipped: no instrument_map",
                            index=index_label)
                return
            expiry = child_imap.get_current_expiry()
            if not expiry:
                log.warning("IH ExitMonitor reg skipped: no expiry",
                            index=index_label)
                return
            inst = child_imap.get_option_instrument(
                int(pos["strike"]), pos["option_type"], expiry
            )
            if not inst:
                log.warning("IH ExitMonitor reg skipped: instrument not found",
                            index=index_label,
                            strike=pos["strike"],
                            type=pos["option_type"],
                            expiry=expiry)
                return

            active = ActiveTrade(
                trade_id=trade_id,
                tracker_type=self.tracker_type,
                strike=int(pos["strike"]),
                option_type=pos["option_type"],
                instrument_token=int(inst["instrument_token"]),
                entry_premium=float(pos["entry_premium"]),
                sl_premium=float(pos["sl_premium"]),
                target_premium=float(pos["target_premium"]),
                is_selling=False,  # IH always buys options
            )
            self._exit_monitor.register_trade(active)
            log.info("IH position registered with ExitMonitor (tick-level SL)",
                     trade_id=trade_id,
                     index=index_label,
                     strike=pos["strike"],
                     type=pos["option_type"],
                     token=inst["instrument_token"])
        except Exception as e:
            log.error("IH ExitMonitor registration failed",
                      trade_id=trade_id, index=index_label, error=str(e))

    def force_exit(
        self,
        trade_id: int,
        exit_premium: float,
        reason: str,
        pnl_pct: float,
        alert_message: Optional[str] = None,
    ) -> None:
        """Override BaseTracker.force_exit to compute & persist pnl_rs.

        BaseTracker only writes pnl_pct, but IH's table has a separate
        profit_loss_rs column that the dashboard + reports rely on.
        Called by ExitMonitor → scheduler._handle_premium_exit on tick-level
        SL/TGT hits.
        """
        if self.trade_repo is None:
            log.warning("IH force_exit called but trade_repo is None")
            return

        # Fetch the row to compute rupee P&L
        row = self.trade_repo._fetch_one(
            f"SELECT entry_premium, qty, signal_group_id, index_label, is_paper "
            f"FROM {self.table_name} WHERE id = ?",
            (trade_id,),
        )
        if not row:
            log.warning("IH force_exit: trade not found", trade_id=trade_id)
            return

        entry = float(row["entry_premium"])
        qty = int(row["qty"])
        pnl_rs = (exit_premium - entry) * qty
        # Recompute pct from entry to be safe (don't trust caller)
        pnl_pct_real = ((exit_premium - entry) / entry * 100) if entry > 0 else 0.0

        now = datetime.now()
        status = "WON" if pnl_rs > 0 else "LOST"
        self.trade_repo.update_trade(
            self.table_name, trade_id,
            status=status,
            resolved_at=now,
            exit_premium=exit_premium,
            exit_reason=reason,
            profit_loss_pct=round(pnl_pct_real, 2),
            profit_loss_rs=round(pnl_rs, 2),
            last_premium=exit_premium,
        )

        log.info(
            f"IH position {status} (force_exit)",
            trade_id=trade_id,
            label=row["index_label"],
            reason=reason,
            pnl_rs=f"{pnl_rs:+.0f}",
            pnl_pct=f"{pnl_pct_real:+.2f}%",
        )

        # Build IH-specific alert (override generic scheduler message)
        pos_for_alert = {
            "index_label": row["index_label"],
            "entry_premium": entry,
            "is_paper": bool(row.get("is_paper", 1)),
        }
        ih_alert = self._format_position_exit_alert(
            pos_for_alert, exit_premium, reason, pnl_pct_real, pnl_rs)

        self._publish(EventType.TRADE_EXITED, {
            "trade_id": trade_id,
            "tracker_type": self.tracker_type,
            "signal_group_id": row["signal_group_id"],
            "label": row["index_label"],
            "action": status,
            "pnl": pnl_rs,
            "pnl_pct": pnl_pct_real,
            "reason": reason,
            "alert_message": ih_alert,
        })

        # Cancel any associated GTT/exit orders for this trade_id
        if self.order_executor is not None:
            try:
                self.order_executor.cancel_exit_orders(trade_id)
            except Exception as e:
                log.error("IH force_exit cancel_exit_orders failed",
                          trade_id=trade_id, error=str(e))

        self._emit_group_update()

    def _place_live_entry(self, trade_id: int, index_label: str, pos: dict) -> None:
        """Route a single position through OrderExecutor (entry + GTT OCO).

        Uses the per-index instrument_map and exchange so SENSEX orders go
        to BFO and BN/NIFTY go to NFO. Quantity is per-index from the
        signal's position dict.
        """
        try:
            child_imap = self._multi_imap.get(index_label) if self._multi_imap else None
            if child_imap is None:
                log.warning("No instrument_map for live entry",
                            index=index_label, trade_id=trade_id)
                return
            exchange = self.INDEX_EXCHANGE.get(index_label, "NFO")
            result = self.order_executor.place_entry(
                trade_id=trade_id,
                strike=int(pos["strike"]),
                option_type=pos["option_type"],
                entry_premium=pos["entry_premium"],
                sl_premium=pos["sl_premium"],
                target_premium=pos["target_premium"],
                order_type="MARKET",
                tracker_type=self.tracker_type,
                table_name=self.table_name,
                quantity=int(pos["qty"]),
                exchange=exchange,
                instrument_map=child_imap,
            )
            if result.success and not result.is_paper:
                log.info("IH LIVE entry filled",
                         trade_id=trade_id, index=index_label,
                         exchange=exchange,
                         order_id=result.order_id,
                         gtt_trigger_id=result.gtt_trigger_id,
                         fill_price=result.actual_fill_price,
                         corrected_sl=result.corrected_sl or pos["sl_premium"],
                         corrected_target=result.corrected_target or pos["target_premium"])
            elif not result.success:
                log.error("IH live entry FAILED — position remains paper",
                          trade_id=trade_id, index=index_label,
                          error=result.error)
                # Mark the row paper since real order failed
                try:
                    self.trade_repo.update_trade(
                        self.table_name, trade_id, is_paper=1)
                except Exception:
                    pass
        except Exception as e:
            log.error("IH _place_live_entry exception",
                      trade_id=trade_id, error=str(e))

    # ------------------------------------------------------------------
    # Position monitoring + exits
    # ------------------------------------------------------------------

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        """Re-price every active position and exit those that hit SL/TGT/TIME."""
        if self.trade_repo is None:
            return None

        active = self._fetch_active_positions()
        if not active:
            return None

        analysis = kwargs.get("analysis", {})
        now = datetime.now()
        results = []

        # Phase 1: Mechanical checks — update premiums and exit where needed
        positions_for_agent: list = []
        current_premiums: Dict[int, float] = {}

        for pos in active:
            current = self._get_current_premium(pos, analysis)
            if current is None or current <= 0:
                continue

            entry = pos["entry_premium"]
            max_p = max(pos.get("max_premium_reached") or entry, current)
            min_p = min(pos.get("min_premium_reached") or entry, current)
            self.trade_repo.update_trade(
                self.table_name, pos["id"],
                last_checked_at=now,
                last_premium=current,
                max_premium_reached=max_p,
                min_premium_reached=min_p,
            )

            exit_reason = self._check_exit_conditions(pos, current, now)
            if exit_reason:
                pnl_rs = (current - entry) * pos["qty"]
                pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
                self._resolve_position(pos, current, exit_reason, pnl_pct, pnl_rs, now)
                self._agent_last_check.pop(pos["id"], None)
                results.append({
                    "trade_id": pos["id"],
                    "signal_group_id": pos.get("signal_group_id"),
                    "label": pos["index_label"],
                    "exit_reason": exit_reason,
                    "pnl_rs": pnl_rs,
                })
            else:
                positions_for_agent.append(pos)
                current_premiums[pos["id"]] = current

        # Phase 2: Batched agent monitoring — single Claude call for all
        # surviving positions (instead of 3 separate subprocess calls).
        if positions_for_agent:
            agent_decisions = self._batch_ask_agent_monitor(
                positions_for_agent, current_premiums, analysis, now)
            for pos in positions_for_agent:
                decision = agent_decisions.get(pos["id"])
                if decision is None:
                    continue
                action = decision.get("action")
                current = current_premiums[pos["id"]]
                entry = pos["entry_premium"]
                if action == "EXIT_NOW":
                    pnl_rs = (current - entry) * pos["qty"]
                    pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
                    self._resolve_position(pos, current, "AGENT_EXIT", pnl_pct, pnl_rs, now)
                    self._agent_last_check.pop(pos["id"], None)
                    results.append({
                        "trade_id": pos["id"],
                        "signal_group_id": pos.get("signal_group_id"),
                        "label": pos["index_label"],
                        "exit_reason": "AGENT_EXIT",
                        "pnl_rs": pnl_rs,
                    })
                elif action == "TIGHTEN_SL":
                    old_sl = pos["sl_premium"]
                    new_sl = float(decision["new_sl_premium"])
                    self.trade_repo.update_trade(
                        self.table_name, pos["id"], sl_premium=new_sl)
                    pos["sl_premium"] = new_sl
                    log.info("IH agent tightened SL",
                             trade_id=pos["id"], index=pos["index_label"],
                             old_sl=old_sl, new_sl=new_sl,
                             reasoning=decision.get("reasoning", ""))

        # If any positions closed, check if the entire group is now resolved
        # and send a group summary alert.
        if results:
            closed_groups = {r["signal_group_id"] for r in results
                             if r.get("signal_group_id")}
            for gid in closed_groups:
                summary = self._format_group_exit_alert(gid)
                if summary:
                    self._publish(EventType.TRADE_EXITED, {
                        "signal_group_id": gid,
                        "alert_message": summary,
                    })

        return {"closed": results} if results else None

    def _is_throttled(self, trade_id: int, now: datetime) -> bool:
        """Check if this position's agent call is still in cooldown."""
        last = self._agent_last_check.get(trade_id)
        return last is not None and (now - last).total_seconds() < _cfg.AGENT_MONITOR_THROTTLE_SEC

    def _batch_ask_agent_monitor(
        self,
        positions: list,
        current_premiums: Dict[int, float],
        analysis: dict,
        now: datetime,
    ) -> Dict[int, Optional[Dict]]:
        """Single batched agent call for all positions. Respects throttle."""
        agent = self.agent
        if agent is None:
            return {}

        # Only include positions whose throttle has expired
        due = [p for p in positions if not self._is_throttled(p["id"], now)]
        if not due:
            return {}

        # Update throttle timestamps for all due positions
        for p in due:
            self._agent_last_check[p["id"]] = now

        try:
            return agent.monitor_positions_batch(due, current_premiums, analysis)
        except Exception as e:
            log.error("IH batch agent monitor exception", error=str(e))
            return {}

    def _maybe_ask_agent_monitor(
        self, pos: dict, current: float, analysis: dict, now: datetime
    ) -> Optional[Dict]:
        """Throttled call to agent.monitor_position (single-position, for backtesting).

        Returns the agent decision dict (HOLD is dropped → returns None),
        or None if the agent is disabled / unavailable / throttled.
        """
        agent = self.agent
        if agent is None:
            return None
        if self._is_throttled(pos["id"], now):
            return None
        self._agent_last_check[pos["id"]] = now
        try:
            return agent.monitor_position(pos, analysis, current)
        except Exception as e:
            log.error("IH agent monitor exception", trade_id=pos["id"], error=str(e))
            return None

    def _check_exit_conditions(self, pos: dict, current: float, now: datetime) -> Optional[str]:
        if current <= pos["sl_premium"]:
            return "SL_HIT"
        if current >= pos["target_premium"]:
            return "TGT_HIT"
        if now.time() >= _cfg.TIME_EXIT:
            return "TIME_EXIT"
        if self.is_past_force_close(now):
            return "EOD_FORCE"
        return None

    def _resolve_position(
        self,
        pos: dict,
        current: float,
        reason: str,
        pnl_pct: float,
        pnl_rs: float,
        now: datetime,
    ) -> None:
        status = "WON" if pnl_rs > 0 else "LOST"
        self.trade_repo.update_trade(
            self.table_name, pos["id"],
            status=status,
            resolved_at=now,
            exit_premium=current,
            exit_reason=reason,
            profit_loss_pct=round(pnl_pct, 2),
            profit_loss_rs=round(pnl_rs, 2),
        )
        # Unregister from ExitMonitor (prevents double-exit)
        if self._exit_monitor is not None:
            try:
                self._exit_monitor.unregister_trade(pos["id"])
            except Exception:
                pass
        log.info(
            f"IH position {status}",
            trade_id=pos["id"],
            label=pos["index_label"],
            reason=reason,
            pnl_rs=f"{pnl_rs:+.0f}",
            pnl_pct=f"{pnl_pct:+.2f}%",
        )
        alert = self._format_position_exit_alert(pos, current, reason, pnl_pct, pnl_rs)
        self._publish(EventType.TRADE_EXITED, {
            "trade_id": pos["id"],
            "signal_group_id": pos.get("signal_group_id"),
            "label": pos["index_label"],
            "action": status,
            "pnl": pnl_rs,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "alert_message": alert,
        })
        self._emit_group_update()

    def _get_current_premium(self, pos: dict, analysis: dict) -> Optional[float]:
        """Re-price the position from the latest source available.

        Priority order:
            1. Real option LTP from CandleBuilder (last 1-min candle close
               for the position's specific strike). Requires that the
               strike was registered via _register_strike_for_monitoring
               at trade-open time.
            2. NIFTY-only fast path: option_chain LTP via the strikes_data
               dict already in scope (only relevant for NIFTY since BN/SX
               aren't fetched live).
            3. Black-Scholes fallback (mirrors the backtester so behavior
               is consistent when ticks haven't arrived yet).
        """
        from strategies.intraday_hunter_engine import (
            iv_for_index, model_premium, days_to_next_expiry, EXPIRY_DOW,
        )

        label = pos["index_label"]
        strike = int(pos["strike"])
        option_type = pos["option_type"]

        # ---- 1. CandleBuilder real LTP ----
        if self._candle_builder is not None:
            try:
                strike_label = f"{label}_{strike}_{option_type}"
                candles = self._candle_builder.get_candles(strike_label, "1min")
                if candles:
                    last_close = float(candles[-1].get("close", 0) or 0)
                    if last_close > 0:
                        return round(last_close, 2)
            except Exception as e:
                log.debug("CandleBuilder LTP lookup failed",
                          label=label, strike=strike, error=str(e))

        # ---- 2. NIFTY option_chain LTP fallback (existing 3-min snapshot) ----
        if label == "NIFTY":
            strikes_data = analysis.get("strikes_data") if isinstance(analysis, dict) else None
            if strikes_data:
                row = strikes_data.get(strike) or strikes_data.get(str(strike))
                if row:
                    key = "ce_ltp" if option_type == "CE" else "pe_ltp"
                    ltp = float(row.get(key, 0) or 0)
                    if ltp > 0:
                        return round(ltp, 2)

        # ---- 3. No real LTP available — skip this cycle ----
        # BS fallback removed from live: estimates can be 35%+ off actual
        # market (e.g. 195 vs 144 on 2026-04-10) which triggers false exits.
        log.debug("No LTP for position, skipping",
                  label=label, strike=strike, option_type=option_type)
        return None

    # ------------------------------------------------------------------
    # BaseTracker required methods
    # ------------------------------------------------------------------

    def get_active(self) -> Optional[Dict]:
        """Returns the most recent ACTIVE position (any index) or None."""
        if self.trade_repo is None:
            return None
        return self.trade_repo.get_active(self.table_name)

    def get_stats(self, lookback_days: int = 30) -> Dict:
        if self.trade_repo is None:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_win": 0, "avg_loss": 0, "total_pnl": 0}
        return self.trade_repo.get_stats(self.table_name, lookback_days)

    def story_state(self):
        """Return an IHStoryState snapshot for the narrative engine.

        State precedence:
          1. LOCKED_OUT — 2-day circuit breaker is active
          2. LIVE       — any position is currently open
          3. FORMING    — an E-detector is armed but no position yet
          4. RECENTLY_CLOSED — a group closed within the last 10 minutes
          5. WAITING    — default
        """
        from analysis.narrative import IHGroupState, IHStoryState
        from datetime import datetime

        cfg = getattr(self, "_cfg", _cfg)

        if self._locked_out:
            return IHStoryState(
                state=IHGroupState.LOCKED_OUT,
                day_bias=self._day_bias,
                groups_today=self._count_signal_groups_today(),
                max_groups_today=cfg.MAX_GROUPS_PER_DAY,
            )

        if self._has_open_positions():
            positions = self._fetch_active_positions()
            formatted = [
                {
                    "index": p.get("index_label"),
                    "strike": p.get("strike"),
                    "option_type": p.get("option_type"),
                    "entry_premium": p.get("entry_premium", 0),
                    "current_premium": p.get("current_premium", p.get("entry_premium", 0)),
                    "quantity": p.get("qty", 1),
                    "is_paper": bool(p.get("is_paper", 1)),
                    "time_left_minutes": p.get("time_left_minutes", 0),
                }
                for p in positions
            ]
            group_id = positions[0].get("signal_group_id", "") if positions else ""
            return IHStoryState(
                state=IHGroupState.LIVE,
                group_id=group_id[:5] if group_id else None,
                positions=formatted,
                agent_verdict=getattr(self, "_last_agent_verdict", "HOLD"),
                day_bias=self._day_bias,
                groups_today=self._count_signal_groups_today(),
                max_groups_today=cfg.MAX_GROUPS_PER_DAY,
            )

        if self._armed_detector is not None:
            return IHStoryState(
                state=IHGroupState.FORMING,
                detector_armed=self._armed_detector,
                alignment=dict(self._alignment),
                day_bias=self._day_bias,
                groups_today=self._count_signal_groups_today(),
                max_groups_today=cfg.MAX_GROUPS_PER_DAY,
            )

        if self._last_closed_group:
            delta = datetime.now() - self._last_closed_group["closed_at"]
            ago = int(delta.total_seconds() // 60)
            if ago <= 10:
                return IHStoryState(
                    state=IHGroupState.RECENTLY_CLOSED,
                    group_id=self._last_closed_group["group_id"],
                    ago_minutes=ago,
                    day_bias=self._day_bias,
                    groups_today=self._count_signal_groups_today(),
                    max_groups_today=cfg.MAX_GROUPS_PER_DAY,
                )

        return IHStoryState(
            state=IHGroupState.WAITING,
            day_bias=self._day_bias,
            groups_today=self._count_signal_groups_today(),
            max_groups_today=cfg.MAX_GROUPS_PER_DAY,
        )

    def _emit_group_update(self) -> None:
        """Push the current IH group state to dashboard clients.

        No-op when socketio is None (paper / standalone / test runs).
        Called after every lifecycle transition: group open, position close,
        detector arming.
        """
        sio = getattr(self, "socketio", None)
        if sio is None:
            return
        state = self.story_state()
        sio.emit("ih_group_update", {
            "state": state.state.value,
            "group_id": state.group_id,
            "positions": state.positions,
            "agent_verdict": state.agent_verdict,
            "day_bias": state.day_bias,
        })

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _has_open_positions(self) -> bool:
        return self.trade_repo.get_active(self.table_name) is not None

    def _fetch_active_positions(self) -> List[dict]:
        """All ACTIVE positions across any signal group (max 3)."""
        return self.trade_repo._fetch_all(
            f"SELECT * FROM {self.table_name} WHERE status = 'ACTIVE' ORDER BY id ASC"
        )

    def _count_signal_groups_today(self) -> int:
        """Count distinct signal_group_ids opened today."""
        rows = self.trade_repo._fetch_all(
            f"SELECT DISTINCT signal_group_id FROM {self.table_name} "
            f"WHERE DATE(created_at) = DATE('now', 'localtime')"
        )
        return len(rows)

    def _daily_pnl_rs(self) -> float:
        rows = self.trade_repo._fetch_all(
            f"SELECT profit_loss_rs FROM {self.table_name} "
            f"WHERE DATE(created_at) = DATE('now', 'localtime') "
            f"AND status IN ('WON', 'LOST')"
        )
        return float(sum(r["profit_loss_rs"] or 0 for r in rows))

    def _cooldown_ok(self, now: datetime) -> bool:
        last = self.trade_repo.get_last_resolved(self.table_name)
        if not last or not last.get("resolved_at"):
            return True
        last_resolved = (
            datetime.fromisoformat(last["resolved_at"])
            if isinstance(last["resolved_at"], str)
            else last["resolved_at"]
        )
        elapsed_min = (now - last_resolved).total_seconds() / 60
        last_pnl = float(last.get("profit_loss_rs") or 0)
        if last_pnl > 0:
            return elapsed_min >= _cfg.COOLDOWN_AFTER_WIN_MIN
        if last_pnl < 0:
            return elapsed_min >= _cfg.COOLDOWN_AFTER_LOSS_MIN
        return True

    def _consecutive_losing_days(self) -> int:
        """Count consecutive losing days ending yesterday."""
        rows = self.trade_repo._fetch_all(
            f"SELECT DATE(created_at) as d, SUM(profit_loss_rs) as total "
            f"FROM {self.table_name} "
            f"WHERE status IN ('WON', 'LOST') "
            f"AND DATE(created_at) < DATE('now', 'localtime') "
            f"GROUP BY d ORDER BY d DESC LIMIT ?",
            (_cfg.CONSECUTIVE_LOSS_SKIP + 1,),
        )
        streak = 0
        for r in rows:
            if (r["total"] or 0) < 0:
                streak += 1
            else:
                break
        return streak

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_group_alert(group_id: str, sig, positions: List[dict], vix: float) -> str:
        emoji = "\U0001f7e2" if sig.direction == "BUY" else "\U0001f534"
        lines = [
            f"<b>{emoji} INTRADAY HUNTER: {sig.direction}</b>",
            f"<b>Trigger:</b> <code>{sig.trigger}</code>  "
            f"<b>Bias:</b> <code>{sig.day_bias_score:+.2f}</code>  "
            f"<b>VIX:</b> {vix:.1f}",
            "",
        ]
        for i, p in enumerate(positions):
            tag = "LIVE" if not p.get("is_paper", True) else "PAPER"
            prefix = "\u250c\u2500" if i == 0 else ("\u2514\u2500" if i == len(positions) - 1 else "\u251c\u2500")
            cont = "\u2502 " if i < len(positions) - 1 else "  "
            lines.append(
                f"{prefix} <b>{p['index_label']}</b> {p['strike']} {p['option_type']}"
                f" \u00d7 {p['qty']} <code>[{tag}]</code>"
            )
            lines.append(
                f"{cont} Entry \u20b9{p['entry_premium']:.2f}  "
                f"SL \u20b9{p['sl_premium']:.2f}  "
                f"Tgt \u20b9{p['target_premium']:.2f}"
            )
        lines.append("")
        lines.append(f"<i>{datetime.now().strftime('%H:%M:%S')} | Group {group_id}</i>")
        return "\n".join(lines)

    @staticmethod
    def _format_position_exit_alert(pos: dict, exit_premium: float,
                                     reason: str, pnl_pct: float, pnl_rs: float) -> str:
        """Format a single-position exit alert."""
        status = "WON" if pnl_rs > 0 else "LOST"
        emoji = "\u2705" if pnl_rs > 0 else "\u274c"
        tag = "LIVE" if not pos.get("is_paper", True) else "PAPER"
        return (
            f"<b>{emoji} IH {pos['index_label']} {status}</b> ({reason})\n"
            f"Entry \u20b9{pos['entry_premium']:.2f} \u2192 "
            f"Exit \u20b9{exit_premium:.2f}\n"
            f"P&L: {pnl_pct:+.1f}%  Rs {pnl_rs:+,.0f} <code>[{tag}]</code>"
        )

    def _format_group_exit_alert(self, signal_group_id: str) -> Optional[str]:
        """Format a summary exit alert for the entire signal group.

        Returns None if the group still has active positions.
        """
        if self.trade_repo is None:
            return None
        rows = self.trade_repo._fetch_all(
            f"SELECT * FROM {self.table_name} WHERE signal_group_id = ?",
            (signal_group_id,),
        )
        if not rows:
            return None
        # Only send summary when all positions in the group are resolved
        if any(r["status"] == "ACTIVE" for r in rows):
            return None

        total_rs = sum(float(r.get("profit_loss_rs") or 0) for r in rows)
        emoji = "\u2705" if total_rs > 0 else "\u274c"
        lines = [f"<b>{emoji} INTRADAY HUNTER: CLOSED</b>", ""]

        for i, r in enumerate(rows):
            status = r["status"]
            tag = "LIVE" if not r.get("is_paper", 1) else "PAPER"
            pnl_pct = float(r.get("profit_loss_pct") or 0)
            pnl_rs = float(r.get("profit_loss_rs") or 0)
            prefix = "\u250c\u2500" if i == 0 else ("\u2514\u2500" if i == len(rows) - 1 else "\u251c\u2500")
            cont = "\u2502 " if i < len(rows) - 1 else "  "
            status_emoji = "\u2705" if pnl_rs > 0 else "\u274c"
            lines.append(
                f"{prefix} <b>{r['index_label']}</b> {r['strike']} {r['option_type']}"
                f" <code>[{tag}]</code> \u2014 <b>{status_emoji} {status}</b>"
            )
            lines.append(
                f"{cont} Entry \u20b9{r['entry_premium']:.2f} \u2192 "
                f"Exit \u20b9{float(r.get('exit_premium') or 0):.2f}"
                f" ({r.get('exit_reason', '')})"
            )
            lines.append(f"{cont} P&L: <b>{pnl_pct:+.1f}%</b>  Rs <b>{pnl_rs:+,.0f}</b>")

        lines.append("")
        lines.append(f"<b>Total: Rs {total_rs:+,.0f}</b>")
        lines.append(f"<i>{datetime.now().strftime('%H:%M:%S')}</i>")
        return "\n".join(lines)
