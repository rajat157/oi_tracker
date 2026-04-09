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
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(IH_TRADES_DDL, IH_TRADES_INDEXES)
        self._engine = None
        self._agent = None  # lazy — built on first use to avoid import cycles
        # Per-position last-agent-monitor timestamp (for throttling agent calls)
        self._agent_last_check: Dict[int, datetime] = {}

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
            decision = agent.confirm_signal(signal_data, analysis)
            if decision is None:
                # Either NO_TRADE, low confidence, or agent failure → skip.
                return None
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
            if wants_live and self.order_executor is not None:
                self._place_live_entry(tid, label, pos)

        # Single combined alert for the group
        alert = self._format_group_alert(signal_group_id, sig, positions, vix)
        self._publish(EventType.TRADE_CREATED, {
            "trade_id": first_trade_id,
            "signal_group_id": signal_group_id,
            "direction": sig.direction,
            "alert_message": alert,
        })
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
                log.info("IH live entry placed",
                         trade_id=trade_id, index=index_label,
                         order_id=result.order_id,
                         gtt_id=result.gtt_trigger_id,
                         fill=result.actual_fill_price)
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

            # Active monitoring via the Claude agent (throttled).
            # Only ask the agent if no mechanical exit fired AND we haven't
            # asked recently for this position.
            if not exit_reason:
                agent_decision = self._maybe_ask_agent_monitor(pos, current, analysis, now)
                if agent_decision is not None:
                    action = agent_decision.get("action")
                    if action == "EXIT_NOW":
                        exit_reason = "AGENT_EXIT"
                    elif action == "TIGHTEN_SL":
                        new_sl = float(agent_decision["new_sl_premium"])
                        # Persist the new SL on the row so the next cycle's
                        # mechanical SL check uses it.
                        self.trade_repo.update_trade(
                            self.table_name, pos["id"], sl_premium=new_sl)
                        pos["sl_premium"] = new_sl
                        log.info("IH agent tightened SL",
                                 trade_id=pos["id"], new_sl=new_sl,
                                 reasoning=agent_decision.get("reasoning", ""))

            if exit_reason:
                pnl_rs = (current - entry) * pos["qty"]
                pnl_pct = ((current - entry) / entry) * 100 if entry > 0 else 0
                self._resolve_position(pos, current, exit_reason, pnl_pct, pnl_rs, now)
                self._agent_last_check.pop(pos["id"], None)
                results.append({
                    "trade_id": pos["id"],
                    "label": pos["index_label"],
                    "exit_reason": exit_reason,
                    "pnl_rs": pnl_rs,
                })
        return {"closed": results} if results else None

    def _maybe_ask_agent_monitor(
        self, pos: dict, current: float, analysis: dict, now: datetime
    ) -> Optional[Dict]:
        """Throttled call to agent.monitor_position.

        Returns the agent decision dict (HOLD is dropped → returns None),
        or None if the agent is disabled / unavailable / throttled.
        """
        agent = self.agent
        if agent is None:
            return None
        last = self._agent_last_check.get(pos["id"])
        if last and (now - last).total_seconds() < _cfg.AGENT_MONITOR_THROTTLE_SEC:
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
        log.info(
            f"IH position {status}",
            trade_id=pos["id"],
            label=pos["index_label"],
            reason=reason,
            pnl_rs=f"{pnl_rs:+.0f}",
            pnl_pct=f"{pnl_pct:+.2f}%",
        )
        self._publish(EventType.TRADE_EXITED, {
            "trade_id": pos["id"],
            "signal_group_id": pos.get("signal_group_id"),
            "label": pos["index_label"],
            "action": status,
            "pnl": pnl_rs,
            "pnl_pct": pnl_pct,
            "reason": reason,
        })

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

        # ---- 3. Black-Scholes fallback ----
        if label == "NIFTY":
            spot = float(analysis.get("spot_price", 0) or 0)
        elif label == "BANKNIFTY":
            spot = float(analysis.get("banknifty_spot", 0) or 0)
        elif label == "SENSEX":
            spot = float(analysis.get("sensex_spot", 0) or 0)
        else:
            return None
        if spot <= 0:
            return None

        vix = float(analysis.get("vix", 0) or 0)
        iv = iv_for_index(label, vix, _cfg)
        dte = days_to_next_expiry(datetime.now().date(), EXPIRY_DOW[label])
        return round(model_premium(spot, strike, option_type, dte, iv, _cfg), 2)

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
        emoji = "🟢" if sig.direction == "BUY" else "🔴"
        lines = [
            f"<b>{emoji} INTRADAY HUNTER: {sig.direction}</b>",
            f"<b>Trigger:</b> <code>{sig.trigger}</code> | "
            f"<b>Bias score:</b> <code>{sig.day_bias_score:+.2f}</code>",
            f"<b>VIX:</b> {vix:.1f} | <b>Group:</b> <code>{group_id}</code>",
            "",
        ]
        for p in positions:
            paper_tag = " <i>(paper)</i>"  # always paper for now
            lines.append(
                f"<b>{p['index_label']}:</b> {p['strike']} {p['option_type']}"
                f"  qty={p['qty']}  entry=Rs {p['entry_premium']:.2f}  "
                f"sl=Rs {p['sl_premium']:.2f}  tgt=Rs {p['target_premium']:.2f}{paper_tag}"
            )
        lines.append("")
        lines.append(f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>")
        return "\n".join(lines)
