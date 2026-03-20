"""Rally Rider (RR) strategy — regime-adaptive, Claude-agent-powered rally catcher.

Extends BaseTracker. Hybrid of MC (trailing stops, time exits) + Scalper
(Claude agent, premium charts). Detects 3 signal types (MC/MOM/VWAP),
adapts parameters per market regime, and uses Claude for final entry judgment.

727 trades, 60.2% WR, 1.90 PF across 300 days backtested.
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Dict, Optional

from config import RRConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from core.logger import get_logger
from db.schema import RR_TRADES_DDL

log = get_logger("rr_strategy")

_cfg = RRConfig()


class RRStrategy(BaseTracker):
    tracker_type = "rally_rider"
    table_name = "rr_trades"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = _cfg.MAX_TRADES_PER_DAY
    is_selling = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(RR_TRADES_DDL)
        self._engine = None
        self._agent = None
        self._scalper_engine = None

    @property
    def engine(self):
        if self._engine is None:
            from strategies.rr_engine import RREngine
            self._engine = RREngine()
        return self._engine

    @property
    def agent(self):
        if self._agent is None:
            from strategies.rr_agent import RRAgent
            self._agent = RRAgent()
        return self._agent

    @property
    def scalper_engine(self):
        if self._scalper_engine is None:
            from strategies.scalper_engine import ScalperEngine
            self._scalper_engine = ScalperEngine()
        return self._scalper_engine

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        """Check if conditions are met for a new RR trade."""
        now = datetime.now()

        # Get regime-specific time window
        regime = self.engine.classify_regime(_cfg)
        regime_config = self.engine.get_regime_params(regime)
        regime_start = regime_config.get("time_start", self.time_start)
        regime_end = regime_config.get("time_end", self.time_end)

        if not (regime_start <= now.time() <= regime_end):
            return False

        if self.trade_repo is None:
            return False

        if self.trade_repo.get_active(self.table_name):
            return False

        todays = self.trade_repo.get_todays_trades(self.table_name)

        # Cooldown since last trade (applies to both real and paper)
        regime_cooldown = regime_config.get("cooldown", _cfg.COOLDOWN_MINUTES)
        if todays:
            last = todays[-1]
            resolved_at = last.get("resolved_at")
            if resolved_at:
                last_resolved = (datetime.fromisoformat(resolved_at)
                                 if isinstance(resolved_at, str) else resolved_at)
                elapsed = (now - last_resolved).total_seconds()
                if elapsed < regime_cooldown * 60:
                    return False
            else:
                return False  # last trade still active

        spot = analysis.get("spot_price", 0)
        if spot <= 0:
            return False

        return True

    def _count_real_trades_today(self) -> int:
        """Count today's non-paper trades."""
        if self.trade_repo is None:
            return 0
        todays = self.trade_repo.get_todays_trades(self.table_name)
        return sum(1 for t in todays if not t.get("is_paper"))

    def evaluate_signal(self, analysis: dict, strikes_data: dict) -> Optional[Dict]:
        """Detect signals, pick best, call Claude agent for confirmation."""
        regime = self.engine.classify_regime(_cfg)
        regime_config = self.engine.get_regime_params(regime)

        # Step 1: Detect mechanical signals
        signals = self.engine.detect_signals(analysis, regime_config)
        if not signals:
            return None

        # Step 2: Pick best signal
        from strategies.rr_engine import RREngine
        best_signal = RREngine.pick_best_signal(signals)
        if not best_signal:
            return None

        option_type = best_signal["option_type"]
        spot = analysis.get("spot_price", 0)
        strike = self.engine.get_rr_strike(spot, option_type)

        # Step 3: Build premium chart
        chart = self.scalper_engine.build_premium_chart(
            spot,
            ce_strike=strike if option_type == "CE" else None,
            pe_strike=strike if option_type == "PE" else None,
        )
        if not chart:
            log.info("No chart data for RR signal")
            return None

        chart_text = self.scalper_engine.format_chart_for_prompt(chart)

        # Step 4: Call Claude agent
        todays = self.trade_repo.get_todays_trades(self.table_name) if self.trade_repo else []
        resolved = [t for t in todays if t.get("resolved_at")]

        agent_signal = self.agent.get_signal(
            chart_text, analysis, best_signal,
            regime, regime_config, resolved,
        )
        if not agent_signal:
            return None

        # Step 5: Apply tick rounding
        agent_signal["entry_premium"] = RREngine.round_to_tick(agent_signal.get("entry_premium", 0))
        agent_signal["sl_premium"] = RREngine.round_to_tick(agent_signal.get("sl_premium", 0))
        agent_signal["target_premium"] = RREngine.round_to_tick(agent_signal.get("target_premium", 0))

        # Attach metadata
        agent_signal["signal_type"] = best_signal["signal_type"]
        agent_signal["regime"] = regime
        agent_signal["signal_data"] = best_signal.get("signal_data", {})
        agent_signal["signal_data"]["regime"] = regime
        agent_signal["signal_data"]["max_hold"] = regime_config.get("max_hold", 35)
        agent_signal["signal_data"]["weekly_trend"] = self.engine.get_weekly_trend()

        log.info("RR signal evaluated",
                 regime=regime, signal_type=best_signal["signal_type"],
                 action=agent_signal.get("action"),
                 confidence=agent_signal.get("confidence"))

        return agent_signal

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        if not isinstance(signal, dict):
            return None

        strike = signal.get("strike", 0)
        entry = signal.get("entry_premium", 0)
        sl = signal.get("sl_premium", 0)
        target = signal.get("target_premium", 0)
        confidence = signal.get("confidence", 0)
        reasoning = signal.get("reasoning", "")
        option_type = signal.get("option_type", "")
        direction = signal.get("action", signal.get("direction", ""))
        signal_type = signal.get("signal_type", "")
        regime = signal.get("regime", "")
        signal_data = signal.get("signal_data", {})

        if not strike or not entry:
            log.warning("RR: invalid signal", signal=signal)
            return None

        if confidence < _cfg.MIN_AGENT_CONFIDENCE:
            log.info("RR skipped: low confidence",
                     confidence=confidence, min=_cfg.MIN_AGENT_CONFIDENCE)
            return None

        if entry < _cfg.MIN_PREMIUM or entry > _cfg.MAX_PREMIUM:
            log.info("RR skipped: premium out of range", premium=entry)
            return None

        spot = analysis.get("spot_price", 0)
        verdict = analysis.get("verdict", "")
        vix = analysis.get("vix", 0) or 0

        todays = self.trade_repo.get_todays_trades(self.table_name)
        trade_number = len(todays) + 1

        # Determine if past max real trades → paper-only
        regime_config_for_max = self.engine.get_regime_params(regime)
        regime_max = regime_config_for_max.get("max_trades", _cfg.MAX_TRADES_PER_DAY)
        real_count = self._count_real_trades_today()
        is_paper = real_count >= regime_max

        now = datetime.now()
        trade_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=now,
            direction=direction,
            strike=strike,
            option_type=option_type,
            entry_premium=entry,
            sl_premium=sl,
            target_premium=target,
            spot_at_creation=spot,
            verdict_at_creation=verdict,
            signal_type=signal_type,
            signal_data_json=json.dumps(signal_data),
            regime=regime,
            agent_reasoning=reasoning,
            agent_confidence=confidence,
            vix_at_creation=vix,
            status="ACTIVE",
            max_premium_reached=entry,
            min_premium_reached=entry,
            trail_stage=0,
            trade_number=trade_number,
            is_paper=1 if is_paper else 0,
        )

        log.info("RR trade created",
                 trade_id=trade_id, direction=direction, regime=regime,
                 signal_type=signal_type,
                 strike=f"{strike} {option_type}",
                 entry=entry, sl=sl, target=target,
                 confidence=confidence, paper=is_paper)

        # Place real order only if NOT paper
        if self.order_executor and trade_id and not is_paper:
            order_result = self.order_executor.place_entry(
                trade_id=trade_id, strike=strike, option_type=option_type,
                entry_premium=entry, sl_premium=sl, target_premium=target,
                tracker_type=self.tracker_type, table_name=self.table_name,
            )
            if order_result.actual_fill_price > 0:
                entry = order_result.actual_fill_price
            if order_result.corrected_sl > 0:
                sl = order_result.corrected_sl
            if order_result.corrected_target > 0:
                target = order_result.corrected_target

        alert_msg = self._format_entry_alert(
            direction, strike, option_type, entry, sl, target,
            spot, verdict, vix, confidence, reasoning,
            regime, signal_type, signal_data, trade_number)
        if is_paper:
            alert_msg = "\U0001f4dd PAPER | " + alert_msg

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": trade_id, "direction": direction, "strike": strike,
            "option_type": option_type, "entry": entry, "sl": sl, "target": target,
            "alert_message": alert_msg,
        })
        return trade_id

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        trade = self.get_active()
        if not trade:
            return None

        strike = trade["strike"]
        option_type = trade["option_type"]
        key = "ce_ltp" if option_type == "CE" else "pe_ltp"
        current = strikes_data.get(strike, {}).get(key, 0)
        if current <= 0:
            return None

        now = datetime.now()
        entry = trade["entry_premium"]
        max_p = max(trade.get("max_premium_reached") or entry, current)
        min_p = min(trade.get("min_premium_reached") or entry, current)
        trail_stage = trade.get("trail_stage", 0) or 0
        sl = trade["sl_premium"]

        # Trailing stop logic
        pnl_pct = ((current - entry) / entry) * 100
        new_trail_stage = trail_stage

        if pnl_pct >= _cfg.TRAIL_2_TRIGGER and trail_stage < 2:
            new_sl = RREngine_round_to_tick(entry * (1 + _cfg.TRAIL_2_LOCK / 100))
            if new_sl > sl:
                sl = new_sl
                new_trail_stage = 2
                log.info("RR trail stage 2",
                         trade_id=trade["id"], sl=sl, pnl=f"{pnl_pct:.1f}%")
        elif pnl_pct >= _cfg.TRAIL_1_TRIGGER and trail_stage < 1:
            new_sl = RREngine_round_to_tick(entry * (1 + _cfg.TRAIL_1_LOCK / 100))
            if new_sl > sl:
                sl = new_sl
                new_trail_stage = 1
                log.info("RR trail stage 1",
                         trade_id=trade["id"], sl=sl, pnl=f"{pnl_pct:.1f}%")

        updates = dict(
            last_checked_at=now, last_premium=current,
            max_premium_reached=max_p, min_premium_reached=min_p,
        )
        if new_trail_stage != trail_stage:
            updates["trail_stage"] = new_trail_stage
            updates["sl_premium"] = sl
            # Modify GTT to reflect new trailing SL
            if self.order_executor and not trade.get("is_paper"):
                self.order_executor.modify_sl(
                    trade["id"], sl, current, trade["target_premium"])

        self.trade_repo.update_trade(self.table_name, trade["id"], **updates)

        premium_monitor = kwargs.get("premium_monitor")

        def _resolve(status, reason):
            # Place market exit for time-based exits (GTT handles SL/target)
            if reason in ("EOD", "MAX_TIME", "TIME_FLAT") and self.order_executor and not trade.get("is_paper"):
                self.order_executor.place_exit(
                    trade["id"], strike, option_type, tracker_type=self.tracker_type)
            final_pnl = ((current - entry) / entry) * 100
            self.trade_repo.update_trade(
                self.table_name, trade["id"],
                status=status, resolved_at=now,
                exit_premium=current, exit_reason=reason,
                profit_loss_pct=final_pnl,
            )
            # Unregister from WebSocket monitor to prevent double exit
            if premium_monitor:
                premium_monitor.unregister_trade(trade["id"])
            log.info(f"RR {status} ({reason})", pnl=f"{final_pnl:.2f}%",
                     entry=entry, exit=current, trade_id=trade["id"])
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": trade["id"], "action": status, "pnl": final_pnl,
                "reason": reason,
                "alert_message": self._format_exit_alert(trade, current, reason, final_pnl),
            })
            return {"action": status, "pnl": final_pnl, "reason": reason}

        # SL hit
        if current <= sl:
            return _resolve("LOST", "TRAIL_SL" if trail_stage > 0 else "SL")

        # Target hit
        if current >= trade["target_premium"]:
            return _resolve("WON", "TARGET")

        # Time-based exits
        created = (datetime.fromisoformat(trade["created_at"])
                   if isinstance(trade["created_at"], str) else trade["created_at"])
        elapsed_min = (now - created).total_seconds() / 60

        # Parse max_hold from signal_data_json
        max_hold = _cfg.MAX_DURATION_MIN
        try:
            sd = json.loads(trade.get("signal_data_json") or "{}")
            max_hold = sd.get("max_hold", _cfg.MAX_DURATION_MIN)
        except (json.JSONDecodeError, TypeError):
            pass

        if elapsed_min >= _cfg.MAX_DURATION_MIN:
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "MAX_TIME")

        # Time exit for flat trades (use regime max_hold)
        if elapsed_min >= max_hold and abs(pnl_pct) < _cfg.TIME_EXIT_DEAD_PCT:
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "TIME_FLAT")

        # EOD force close
        if self.is_past_force_close(now):
            status = "WON" if pnl_pct > 0 else "LOST"
            return _resolve(status, "EOD")

        # Claude active trade monitoring
        analysis = kwargs.get("analysis", {})
        premium_monitor = kwargs.get("premium_monitor")

        if elapsed_min >= 6 and now.time() < time(14, 45) and self._agent is not None:
            monitor_result = self._call_trade_monitor(
                trade, current, analysis, premium_monitor, elapsed_min)
            if monitor_result:
                return monitor_result

        return None

    def _call_trade_monitor(
        self, trade: Dict, current: float,
        analysis: dict, premium_monitor, elapsed_min: float,
    ) -> Optional[Dict]:
        """Call Claude to monitor active RR trade. Returns exit dict or None."""
        try:
            spot = analysis.get("spot_price", 0)
            if spot <= 0:
                return None
            chart = self.scalper_engine.build_premium_chart(
                spot,
                ce_strike=trade["strike"] if trade["option_type"] == "CE" else None,
                pe_strike=trade["strike"] if trade["option_type"] == "PE" else None,
            )
            if not chart:
                return None
            chart_text = self.scalper_engine.format_chart_for_prompt(chart)

            entry = trade["entry_premium"]
            trade_context = {
                "trade_id": trade["id"],
                "entry_premium": entry,
                "current_premium": current,
                "pnl_pct": ((current - entry) / entry) * 100,
                "sl_premium": trade["sl_premium"],
                "target_premium": trade["target_premium"],
                "trail_stage": trade.get("trail_stage", 0),
                "option_type": trade["option_type"],
                "strike": trade["strike"],
                "direction": trade["direction"],
                "time_in_trade_min": elapsed_min,
                "max_premium_reached": trade.get("max_premium_reached", current),
            }

            result = self.agent.monitor_active_trade(chart_text, trade_context, analysis)
            if not result:
                return None

            action = result.get("action", "HOLD")
            now = datetime.now()

            if action == "TIGHTEN_SL":
                new_sl = RREngine_round_to_tick(result["new_sl_premium"])
                self.trade_repo.update_trade(
                    self.table_name, trade["id"], sl_premium=new_sl)
                if self.order_executor and not trade.get("is_paper"):
                    self.order_executor.modify_sl(
                        trade["id"], new_sl, current, trade["target_premium"])
                if premium_monitor:
                    premium_monitor.update_trade_sl(trade["id"], new_sl)
                log.info("Claude tightened RR SL", trade_id=trade["id"],
                         old_sl=trade["sl_premium"], new_sl=new_sl,
                         reasoning=result.get("reasoning", ""))
                return None

            if action == "EXIT_NOW":
                if self.order_executor and not trade.get("is_paper"):
                    self.order_executor.place_exit(
                        trade["id"], trade["strike"], trade["option_type"],
                        tracker_type=self.tracker_type)
                pnl = ((current - entry) / entry) * 100
                status = "WON" if pnl > 0 else "LOST"
                self.trade_repo.update_trade(
                    self.table_name, trade["id"],
                    status=status, resolved_at=now,
                    exit_premium=current, exit_reason="CLAUDE_EXIT",
                    profit_loss_pct=pnl)
                self._publish(EventType.TRADE_EXITED, {
                    "trade_id": trade["id"], "action": status, "pnl": pnl,
                    "reason": "CLAUDE_EXIT",
                    "alert_message": self._format_exit_alert(
                        trade, current, f"CLAUDE_EXIT: {result.get('reasoning', '')}", pnl),
                })
                return {"action": status, "pnl": pnl, "reason": "CLAUDE_EXIT"}

        except Exception as e:
            log.error("RR trade monitor error", error=str(e), trade_id=trade["id"])
        return None

    def get_active(self) -> Optional[Dict]:
        if self.trade_repo is None:
            return None
        return self.trade_repo.get_active(self.table_name)

    def get_stats(self, lookback_days: int = 30) -> Dict:
        if self.trade_repo is None:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_win": 0, "avg_loss": 0, "total_pnl": 0}
        return self.trade_repo.get_stats(self.table_name, lookback_days)

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_entry_alert(direction, strike, option_type, entry, sl, target,
                            spot, verdict, vix, confidence, reasoning,
                            regime, signal_type, signal_data, trade_number) -> str:
        side_emoji = "\U0001f7e2" if option_type == "CE" else "\U0001f534"
        risk = entry - sl
        reward = target - entry
        rr = f"1:{reward / risk:.1f}" if risk > 0 else "N/A"
        sl_pct = (entry - sl) / entry * 100
        target_pct = (target - entry) / entry * 100
        weekly = signal_data.get("weekly_trend", "?")
        return (
            f"<b>{side_emoji} RALLY RIDER: {direction}</b> (#{trade_number})\n\n"
            f"<b>Regime:</b> <code>{regime}</code>\n"
            f"<b>Signal:</b> <code>{signal_type}</code>\n"
            f"<b>Strike:</b> <code>{strike} {option_type}</code>\n"
            f"<b>Spot:</b> <code>{spot:.2f}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> (-{sl_pct:.1f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> (+{target_pct:.1f}%)\n"
            f"<b>RR:</b> <code>{rr}</code>\n"
            f"<b>Confidence:</b> {confidence}%\n\n"
            f"<b>Weekly Trend:</b> {weekly}\n"
            f"<b>Verdict:</b> {verdict}\n"
            f"<b>VIX:</b> {vix:.1f}\n\n"
            f"<b>Agent Reasoning:</b>\n<i>{reasoning}</i>\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(trade, exit_premium, reason, pnl) -> str:
        result_emoji = "\u2705" if pnl > 0 else "\u274c"
        reason_map = {"TARGET": "Target Hit", "SL": "Stop Loss",
                      "TRAIL_SL": "Trailing Stop", "EOD": "End of Day",
                      "TIME_FLAT": "Time Exit (flat)", "MAX_TIME": "Max Duration"}
        reason_text = reason_map.get(reason, reason)
        created = (datetime.fromisoformat(trade["created_at"])
                   if isinstance(trade["created_at"], str) else trade["created_at"])
        duration = datetime.now() - created
        duration_str = f"{int(duration.total_seconds() / 60)}m"
        regime = trade.get("regime", "?")
        return (
            f"<b>{result_emoji} RR {'WON' if pnl > 0 else 'LOST'}</b> (#{trade.get('trade_number', '?')})\n\n"
            f"<b>Regime:</b> <code>{regime}</code>\n"
            f"<b>Strike:</b> <code>{trade['strike']} {trade['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {trade['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.2f}%</code>\n"
            f"<b>Duration:</b> {duration_str}\n"
            f"<b>Reason:</b> {reason_text}\n\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )


def RREngine_round_to_tick(value: float) -> float:
    """Convenience wrapper — avoids circular import for trailing stop calc."""
    return round(round(value * 20) / 20, 2)
