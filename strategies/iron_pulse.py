"""IronPulse strategy — bread & butter 1:1 RR buying.

Fully self-contained: all logic absorbed from trade_tracker.py.
PENDING -> ACTIVE lifecycle, trailing SL after T1, extensive filtering.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from config import IronPulseConfig
from core.base_tracker import BaseTracker
from core.events import EventType
from db.schema import TRADE_SETUPS_DDL, TRADE_SETUPS_INDEXES
from logger import get_logger

log = get_logger("trade_tracker")

_cfg = IronPulseConfig()


class IronPulseStrategy(BaseTracker):
    tracker_type = "iron_pulse"
    table_name = "trade_setups"
    time_start = _cfg.TIME_START
    time_end = _cfg.TIME_END
    force_close_time = _cfg.FORCE_CLOSE_TIME
    max_trades_per_day = 1
    is_selling = False
    supports_pending = True

    def __init__(self, price_trend_fn: Callable = None, **kwargs):
        super().__init__(**kwargs)
        if self.trade_repo:
            self.trade_repo.init_table(TRADE_SETUPS_DDL, TRADE_SETUPS_INDEXES)
        # Instance state
        self.entry_tolerance = 0.02
        self.cooldown_minutes = 12
        self.move_threshold_pct = 0.8
        self.bounce_threshold_pct = 0.3
        self.direction_flip_cooldown_minutes = 15
        self.last_suggested_direction = None
        self.last_suggestion_time = None
        self.cancellation_cooldown_minutes = 30
        self.last_cancelled_time = None
        self._get_price_trend = price_trend_fn

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    def should_create(self, analysis: dict, **kwargs) -> bool:
        price_history = kwargs.get("price_history")
        return self._should_create_new_setup(analysis, price_history)

    def create_trade(self, signal: Any, analysis: dict, strikes_data: dict,
                     **kwargs) -> Optional[int]:
        now = kwargs.get("timestamp") or datetime.now()
        return self._create_setup(analysis, now)

    def check_and_update(self, strikes_data: dict, **kwargs) -> Optional[Dict]:
        now = kwargs.get("timestamp") or datetime.now()
        setup = self._get_setup()
        if not setup:
            return None

        strike = setup["strike"]
        option_type = setup["option_type"]
        current = (strikes_data.get(strike, {})
                   .get("ce_ltp" if option_type == "CE" else "pe_ltp", 0))
        if current <= 0:
            return None

        if setup["status"] == "PENDING":
            return self._check_pending_activation(setup, current, now)
        elif setup["status"] == "ACTIVE":
            return self._check_active_resolution(setup, current, now)

        return None

    def get_active(self) -> Optional[Dict]:
        if not self.trade_repo:
            return None
        return self.trade_repo.get_active(self.table_name)

    def get_stats(self, lookback_days: int = 30) -> Dict:
        if not self.trade_repo:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_win": 0, "avg_loss": 0, "total_pnl": 0}
        return self.trade_repo.get_stats(self.table_name, lookback_days)

    # ------------------------------------------------------------------
    # IronPulse-specific public interface
    # ------------------------------------------------------------------

    def get_pending(self) -> Optional[Dict]:
        if not self.trade_repo:
            return None
        return self.trade_repo.get_pending(self.table_name)

    def expire_pending(self, timestamp: datetime = None):
        """Expire stale PENDING setups near market close."""
        now = timestamp or datetime.now()
        if now.time() < _cfg.MARKET_CLOSE:
            return False
        setup = self._get_setup()
        if not setup or setup["status"] != "PENDING":
            return False
        self.trade_repo.update_trade(
            self.table_name, setup["id"],
            status="EXPIRED", resolved_at=now,
        )
        log.info("Setup EXPIRED at market close", setup_id=setup["id"])
        self._publish(EventType.TRADE_EXITED, {
            "trade_id": setup["id"], "action": "EXPIRED", "pnl": 0,
            "reason": "MARKET_CLOSE",
        })
        return True

    def cancel_on_direction_change(self, analysis: dict):
        """Cancel PENDING if direction flipped. DISABLED in new strategy."""
        pass

    def force_close(self, timestamp: datetime, strikes_data: dict) -> bool:
        """Force close ACTIVE trades at force_close_time (15:20)."""
        if timestamp.time() < self.force_close_time:
            return False
        setup = self._get_setup()
        if not setup or setup["status"] != "ACTIVE":
            return False

        current = self._get_premium_for_setup(setup, strikes_data)
        activation = setup.get("activation_premium") or setup["entry_premium"]
        pnl = ((current - activation) / activation * 100) if activation > 0 else 0
        status = "WON" if pnl > 0 else "LOST"

        self.trade_repo.update_trade(
            self.table_name, setup["id"],
            status=status, resolved_at=timestamp,
            exit_premium=current,
            profit_loss_pct=round(pnl, 2),
            profit_loss_points=round(current - activation, 2),
        )
        log.info("Setup FORCE CLOSED", setup_id=setup["id"], status=status,
                 pnl=f"{pnl:+.2f}%")

        self._publish(EventType.TRADE_EXITED, {
            "trade_id": setup["id"], "action": status,
            "pnl": round(pnl, 2), "reason": "EOD",
            "alert_message": self._format_exit_alert(setup, current, "EOD", pnl),
        })
        return True

    def get_dashboard_stats(self) -> dict:
        """Get stats dict with active setup info for dashboard."""
        stats = self.get_stats()
        active = self._get_setup()
        return {
            "stats": stats,
            "has_active_setup": active is not None,
            "active_setup": active,
        }

    def get_active_setup_with_pnl(self, strikes_data: dict) -> Optional[dict]:
        """Get active/pending setup enriched with live P&L."""
        setup = self._get_setup()
        if not setup:
            return None

        current = self._get_premium_for_setup(setup, strikes_data)

        if setup["status"] == "ACTIVE" and setup.get("activation_premium"):
            base = setup["activation_premium"]
        else:
            base = setup["entry_premium"]

        pnl_pct = ((current - base) / base * 100) if base > 0 else 0
        pnl_pts = current - base

        return {
            **setup,
            "current_premium": round(current, 2),
            "live_pnl_pct": round(pnl_pct, 2),
            "live_pnl_points": round(pnl_pts, 2),
            "support_ref": setup.get("support_at_creation"),
            "resistance_ref": setup.get("resistance_at_creation"),
            "max_pain": setup.get("max_pain_at_creation"),
        }

    # ------------------------------------------------------------------
    # Guard checks (10 total)
    # ------------------------------------------------------------------

    def _should_create_new_setup(self, analysis: dict,
                                  price_history: list = None) -> bool:
        """Orchestrate all guard checks for new setup creation."""
        # 1. One trade per day
        if self.trade_repo:
            if self.trade_repo.get_todays_trades(self.table_name):
                return False

        # 2. Strategy signal validity (time, verdict, confidence)
        if not self._is_valid_strategy_signal(analysis):
            return False

        # 3. No existing active/pending
        if self._get_setup():
            return False

        # 4. trade_setup dict exists in analysis
        trade_setup = analysis.get("trade_setup")
        if not trade_setup:
            return False

        # 5. Premium % of spot
        entry = trade_setup.get("entry_premium", 0)
        spot = analysis.get("spot_price", 0)
        if spot > 0 and entry > 0:
            if (entry / spot) * 100 < _cfg.MIN_PREMIUM_PCT:
                log.warning("Skipping: Premium too cheap",
                            premium=f"₹{entry:.2f}",
                            premium_pct=f"{(entry / spot) * 100:.3f}%")
                return False

        # 6. Post-resolution cooldown
        if self._is_in_cooldown():
            return False

        # 7. Cancellation cooldown
        if self._is_in_cancellation_cooldown():
            return False

        # 8. Direction flip cooldown
        direction = trade_setup.get("direction")
        if self._is_direction_flip_cooldown(direction):
            return False

        # 9. Move already happened
        if price_history and self._is_move_already_happened(analysis, price_history):
            log.warning("Skipping: Move already happened")
            return False

        # 10. Bounce in progress (PUT only)
        if price_history and self._is_bounce_in_progress(analysis, price_history):
            log.warning("Skipping: Bounce in progress")
            return False

        # 11. DTE momentum filter
        if self._is_dte_momentum_filtered(analysis):
            return False

        log.info("All conditions met",
                 verdict=analysis.get("verdict"),
                 confidence=f"{analysis.get('signal_confidence', 0):.0f}%",
                 direction=direction)
        return True

    def _is_valid_strategy_signal(self, analysis: dict) -> bool:
        """Check time window, verdict type, and confidence threshold."""
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        now = datetime.now()

        if not self.is_in_time_window(now):
            return False
        if "Slightly" not in verdict:
            return False
        if confidence < _cfg.MIN_CONFIDENCE:
            return False
        return True

    def _is_in_cooldown(self) -> bool:
        """Check cooldown period after last resolved trade."""
        if not self.trade_repo:
            return False
        last = self.trade_repo.get_last_resolved(self.table_name)
        if not last:
            return False
        resolved_str = last.get("resolved_at")
        if not resolved_str:
            return False
        try:
            resolved_at = datetime.fromisoformat(str(resolved_str))
        except (ValueError, TypeError):
            return False
        return (datetime.now() - resolved_at) < timedelta(minutes=self.cooldown_minutes)

    def _is_in_cancellation_cooldown(self) -> bool:
        if not self.last_cancelled_time:
            return False
        elapsed = datetime.now() - self.last_cancelled_time
        return elapsed.total_seconds() < self.cancellation_cooldown_minutes * 60

    def _is_direction_flip_cooldown(self, current_direction: str) -> bool:
        if not self.last_suggestion_time or not self.last_suggested_direction:
            return False
        if current_direction == self.last_suggested_direction:
            return False
        elapsed = datetime.now() - self.last_suggestion_time
        return elapsed.total_seconds() < self.direction_flip_cooldown_minutes * 60

    def _is_move_already_happened(self, analysis: dict,
                                   price_history: list) -> bool:
        """Check if spot already moved 0.8%+ in signal direction."""
        if not price_history or len(price_history) < 2:
            return False
        current_spot = analysis.get("spot_price", 0)
        past_spot = price_history[0].get("spot_price", 0)
        if past_spot <= 0 or current_spot <= 0:
            return False
        move_pct = ((current_spot - past_spot) / past_spot) * 100
        verdict = analysis.get("verdict", "").lower()
        is_bullish = "bull" in verdict
        if is_bullish and move_pct > self.move_threshold_pct:
            return True
        if not is_bullish and move_pct < -self.move_threshold_pct:
            return True
        return False

    def _is_bounce_in_progress(self, analysis: dict,
                                price_history: list) -> bool:
        """Check if bounce from recent low (bad for PUT trades)."""
        if not price_history or len(price_history) < 2:
            return False
        verdict = analysis.get("verdict", "").lower()
        if "bear" not in verdict:
            return False
        current_spot = analysis.get("spot_price", 0)
        if current_spot <= 0:
            return False
        prices = [p.get("spot_price", 0)
                  for p in price_history if p.get("spot_price", 0) > 0]
        if not prices:
            return False
        recent_low = min(prices)
        if recent_low <= 0:
            return False
        bounce_pct = ((current_spot - recent_low) / recent_low) * 100
        return bounce_pct > self.bounce_threshold_pct

    @staticmethod
    def _parse_expiry_date(expiry_str: str) -> Optional[datetime]:
        if not expiry_str:
            return None
        for fmt in ['%Y-%m-%d', '%d-%b-%Y', '%d-%m-%Y', '%d %b %Y']:
            try:
                return datetime.strptime(expiry_str, fmt)
            except ValueError:
                continue
        return None

    def _is_dte_momentum_filtered(self, analysis: dict) -> bool:
        """Block entry when DTE >= 3, IV > 10, and momentum misaligned."""
        trade_setup = analysis.get("trade_setup", {})
        if not trade_setup:
            return False
        expiry_dt = self._parse_expiry_date(analysis.get("expiry_date", ""))
        if not expiry_dt:
            return False
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        dte = (expiry_dt - today).days
        if dte < 3:
            return False
        iv = trade_setup.get("iv_at_strike", 0)
        if iv <= 10:
            return False
        if not self._get_price_trend:
            return False
        price_history = self._get_price_trend(30)
        if not price_history or len(price_history) < 2:
            return False
        first_spot = price_history[0].get("spot_price", 0)
        last_spot = price_history[-1].get("spot_price", 0)
        if first_spot <= 0:
            return False
        momentum_pct = ((last_spot - first_spot) / first_spot) * 100
        direction = trade_setup.get("direction", "")
        is_bullish = direction == "BUY_CALL"
        momentum_misaligned = ((is_bullish and momentum_pct < 0) or
                               (not is_bullish and momentum_pct > 0))
        if momentum_misaligned:
            log.warning("DTE filter: BLOCKED", dte=dte, iv=f"{iv:.1f}",
                        momentum=f"{momentum_pct:+.3f}%", direction=direction)
            return True
        return False

    # ------------------------------------------------------------------
    # Quality scoring
    # ------------------------------------------------------------------

    def _calculate_quality_score(self, analysis: dict) -> int:
        """Quality score (0-9) for the trade setup."""
        score = 0
        if analysis.get("confirmation_status") == "CONFIRMED":
            score += 2
        conf = analysis.get("signal_confidence", 0)
        if 60 <= conf <= 85:
            score += 2
        elif 50 <= conf < 60 or 85 < conf <= 95:
            score += 1
        verdict = analysis.get("verdict", "")
        if "Winning" in verdict:
            score += 1
        setup = analysis.get("trade_setup", {})
        if setup:
            if setup.get("moneyness") == "ITM":
                score += 1
            if setup.get("risk_pct", 20) <= 15:
                score += 1
        pm = analysis.get("premium_momentum", {})
        pm_score = pm.get("premium_momentum_score", 0) if isinstance(pm, dict) else 0
        is_bullish = "bull" in verdict.lower()
        if (is_bullish and pm_score > 10) or (not is_bullish and pm_score < -10):
            score += 1
        return score

    def _generate_trade_reasoning(self, analysis: dict,
                                   trade_setup: dict) -> str:
        """Generate human-readable trade reasoning string."""
        direction = "BUY PUT" if trade_setup["direction"] == "BUY_PUT" else "BUY CALL"
        verdict = analysis.get("verdict", "Unknown")
        confidence = analysis.get("signal_confidence", 0)
        call_change = analysis.get("call_oi_change", 0)
        put_change = analysis.get("put_oi_change", 0)
        spot = analysis.get("spot_price", 0)
        max_pain = analysis.get("max_pain", 0)
        strike = trade_setup["strike"]
        moneyness = trade_setup["moneyness"]
        risk = trade_setup["risk_pct"]
        iv = trade_setup.get("iv_at_strike", 0)
        quality = self._calculate_quality_score(analysis)
        spot_vs_mp = "below" if spot < max_pain else "above"
        reasoning = (
            f"{direction}: {verdict} ({confidence:.0f}% confidence). "
            f"Quality Score: {quality}/9. "
            f"Call OI {call_change / 100000:+.1f}L vs "
            f"Put OI {put_change / 100000:+.1f}L. "
            f"Spot {spot:.0f} {spot_vs_mp} max pain {max_pain}. "
            f"Selected {strike} {trade_setup['option_type']} "
            f"({moneyness}) with {risk:.0f}% risk."
        )
        if iv > 0:
            reasoning += f" IV: {iv:.1f}%"
        return reasoning

    # ------------------------------------------------------------------
    # Trade creation (PENDING)
    # ------------------------------------------------------------------

    def _create_setup(self, analysis: dict, timestamp: datetime) -> Optional[int]:
        trade_setup = analysis.get("trade_setup")
        if not trade_setup:
            return None

        entry = trade_setup["entry_premium"]
        sl = round(entry * (1 - _cfg.SL_PCT / 100), 2)
        target1 = round(entry * (1 + _cfg.TARGET_PCT / 100), 2)
        reasoning = self._generate_trade_reasoning(analysis, trade_setup)

        oi_clusters = analysis.get("oi_clusters", {})
        support = (oi_clusters.get("strongest_support")
                   or trade_setup.get("support_ref") or 0)
        resistance = (oi_clusters.get("strongest_resistance")
                      or trade_setup.get("resistance_ref") or 0)

        setup_id = self.trade_repo.insert_trade(
            self.table_name,
            created_at=timestamp,
            direction=trade_setup["direction"],
            strike=trade_setup["strike"],
            option_type=trade_setup["option_type"],
            moneyness=trade_setup["moneyness"],
            entry_premium=entry,
            sl_premium=sl,
            target1_premium=target1,
            target2_premium=None,
            risk_pct=_cfg.SL_PCT,
            spot_at_creation=analysis["spot_price"],
            verdict_at_creation=analysis["verdict"],
            signal_confidence=analysis["signal_confidence"],
            iv_at_creation=trade_setup.get("iv_at_strike", 0),
            expiry_date=analysis.get("expiry_date", ""),
            status="PENDING",
            call_oi_change_at_creation=analysis.get("call_oi_change", 0),
            put_oi_change_at_creation=analysis.get("put_oi_change", 0),
            pcr_at_creation=analysis.get("pcr", 0),
            max_pain_at_creation=analysis.get("max_pain", 0),
            support_at_creation=support,
            resistance_at_creation=resistance,
            trade_reasoning=reasoning,
        )

        quality = self._calculate_quality_score(analysis)
        log.info("Created PENDING setup", setup_id=setup_id,
                 direction=trade_setup["direction"],
                 strike=trade_setup["strike"], entry=entry,
                 sl=sl, target=target1, quality=quality)

        self._publish(EventType.TRADE_CREATED, {
            "trade_id": setup_id,
            "direction": trade_setup["direction"],
            "strike": trade_setup["strike"],
            "option_type": trade_setup["option_type"],
            "entry": entry, "sl": sl, "target": target1,
            "alert_message": self._format_entry_alert(
                trade_setup, entry, sl, target1, analysis),
        })

        self.last_suggested_direction = trade_setup["direction"]
        self.last_suggestion_time = timestamp
        return setup_id

    # ------------------------------------------------------------------
    # PENDING activation
    # ------------------------------------------------------------------

    def _check_pending_activation(self, setup: dict, current: float,
                                   timestamp: datetime) -> Optional[dict]:
        """Activate PENDING setup when premium is within +10% of entry."""
        entry = setup["entry_premium"]
        upper_threshold = entry * 1.10

        if current <= upper_threshold:
            self.trade_repo.update_trade(
                self.table_name, setup["id"],
                status="ACTIVE", activated_at=timestamp,
                activation_premium=current,
                max_premium_reached=current, min_premium_reached=current,
                last_checked_at=timestamp, last_premium=current,
            )
            slippage = ((current - entry) / entry) * 100
            log.info("Setup ACTIVATED", setup_id=setup["id"],
                     current=f"{current:.2f}", entry=f"{entry:.2f}",
                     slippage=f"{slippage:+.1f}%")
            return {
                "setup_id": setup["id"],
                "previous_status": "PENDING",
                "new_status": "ACTIVE",
                "activation_premium": current,
            }

        # Not activated — just track
        self.trade_repo.update_trade(
            self.table_name, setup["id"],
            last_checked_at=timestamp, last_premium=current,
        )
        return None

    # ------------------------------------------------------------------
    # ACTIVE resolution (Phase 1: SL/T1, Phase 2: Trailing SL)
    # ------------------------------------------------------------------

    def _check_active_resolution(self, setup: dict, current: float,
                                  timestamp: datetime) -> Optional[dict]:
        sl = setup["sl_premium"]
        t1 = setup["target1_premium"]
        activation = setup.get("activation_premium") or setup["entry_premium"]
        t1_already_hit = bool(setup.get("t1_hit"))

        max_reached = max(setup.get("max_premium_reached") or current, current)
        min_reached = min(setup.get("min_premium_reached") or current, current)
        peak = max(setup.get("peak_premium") or current, current)

        def _pnl():
            return ((current - activation) / activation) * 100

        def _resolve(status, reason, hit_sl=False, hit_target=False, extra=None):
            pnl = round(_pnl(), 2)
            fields = dict(
                status=status, resolved_at=timestamp,
                exit_premium=current,
                hit_sl=hit_sl, hit_target=hit_target,
                profit_loss_pct=pnl,
                profit_loss_points=round(current - activation, 2),
                max_premium_reached=max_reached,
                min_premium_reached=min_reached,
                peak_premium=peak,
                last_checked_at=timestamp, last_premium=current,
            )
            if extra:
                fields.update(extra)
            self.trade_repo.update_trade(self.table_name, setup["id"], **fields)
            log.info(f"Setup {status} ({reason})", setup_id=setup["id"],
                     exit=f"{current:.2f}", pnl=f"{pnl:.1f}%")
            self._publish(EventType.TRADE_EXITED, {
                "trade_id": setup["id"], "action": status,
                "pnl": pnl, "reason": reason,
                "alert_message": self._format_exit_alert(
                    setup, current, reason, pnl),
            })
            return {"setup_id": setup["id"], "previous_status": "ACTIVE",
                    "new_status": status, "exit_premium": current,
                    "profit_loss_pct": pnl}

        # ===== PHASE 1: Before T1 =====
        if not t1_already_hit:
            if current <= sl:
                return _resolve("LOST", "SL", hit_sl=True)

            if current >= t1:
                trailing_sl = peak * (1 - _cfg.TRAILING_SL_PCT / 100)
                pnl = round(_pnl(), 2)
                self.trade_repo.update_trade(
                    self.table_name, setup["id"],
                    t1_hit=True, t1_hit_at=timestamp,
                    t1_premium=current, peak_premium=peak,
                    trailing_sl=trailing_sl, hit_target=True,
                    max_premium_reached=max_reached,
                    min_premium_reached=min_reached,
                    last_checked_at=timestamp, last_premium=current,
                )
                log.info("T1 HIT — trailing SL active", setup_id=setup["id"],
                         t1_premium=f"{current:.2f}", pnl=f"{pnl:.1f}%",
                         trailing_sl=f"{trailing_sl:.2f}")
                self._publish(EventType.T1_HIT, {
                    "trade_id": setup["id"], "pnl": pnl,
                    "alert_message": self._format_t1_alert(
                        setup, current, pnl, trailing_sl),
                })
                return {"setup_id": setup["id"],
                        "previous_status": "ACTIVE",
                        "new_status": "T1_HIT",
                        "exit_premium": current,
                        "profit_loss_pct": pnl}

            # Just tracking
            self.trade_repo.update_trade(
                self.table_name, setup["id"],
                peak_premium=peak,
                max_premium_reached=max_reached,
                min_premium_reached=min_reached,
                last_checked_at=timestamp, last_premium=current,
            )
            return None

        # ===== PHASE 2: After T1 — Trailing SL =====
        trailing_sl = peak * (1 - _cfg.TRAILING_SL_PCT / 100)

        if current <= trailing_sl:
            pnl = round(_pnl(), 2)
            status = "WON" if pnl > 0 else "LOST"
            return _resolve(status, "TRAILING_SL", hit_target=True,
                            extra={"trailing_sl": trailing_sl})

        # Update tracking
        self.trade_repo.update_trade(
            self.table_name, setup["id"],
            peak_premium=peak, trailing_sl=trailing_sl,
            max_premium_reached=max_reached,
            min_premium_reached=min_reached,
            last_checked_at=timestamp, last_premium=current,
        )
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_setup(self) -> Optional[dict]:
        """Get the current ACTIVE or PENDING setup."""
        if not self.trade_repo:
            return None
        return self.trade_repo.get_active_or_pending(self.table_name)

    @staticmethod
    def _get_premium_for_setup(setup: dict, strikes_data: dict) -> float:
        strike = setup["strike"]
        option_type = setup.get(
            "option_type",
            "CE" if setup["direction"] == "BUY_CALL" else "PE")
        return (strikes_data.get(strike, {})
                .get("ce_ltp" if option_type == "CE" else "pe_ltp", 0))

    # ------------------------------------------------------------------
    # Alert formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_entry_alert(trade_setup, entry, sl, target, analysis) -> str:
        direction = ("BUY CALL" if trade_setup["direction"] == "BUY_CALL"
                     else "BUY PUT")
        side_emoji = ("\U0001f7e2" if trade_setup["option_type"] == "CE"
                      else "\U0001f534")
        verdict = analysis.get("verdict", "")
        confidence = analysis.get("signal_confidence", 0)
        expiry = analysis.get("expiry_date", "")
        return (
            f"<b>{side_emoji} IRON PULSE: {direction}</b>\n\n"
            f"<b>Strike:</b> <code>{trade_setup['strike']} "
            f"{trade_setup['option_type']}</code>\n"
            f"<b>Entry:</b> <code>Rs {entry:.2f}</code>\n"
            f"<b>SL:</b> <code>Rs {sl:.2f}</code> "
            f"(-{_cfg.SL_PCT:.0f}%)\n"
            f"<b>Target:</b> <code>Rs {target:.2f}</code> "
            f"(+{_cfg.TARGET_PCT:.0f}%)\n"
            f"<b>RR:</b> <code>1:1.1</code>\n\n"
            f"<b>Verdict:</b> {verdict} ({confidence:.0f}%)\n"
            f"<b>Expiry:</b> {expiry}\n\n"
            f"<i>Limit order \u2014 entry activates within "
            f"+10% of listed price.</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_exit_alert(setup, exit_premium, reason, pnl) -> str:
        emoji = "\u2705" if pnl > 0 else "\u274c"
        direction = ("BUY CALL" if setup["direction"] == "BUY_CALL"
                     else "BUY PUT")
        reason_text = {
            "SL": f"Stop Loss (-{_cfg.SL_PCT:.0f}%)",
            "TARGET": f"Target Hit (+{_cfg.TARGET_PCT:.0f}%)",
            "TRAILING_SL": "Trailing SL (15% below peak)",
            "EOD": "End of Day (15:20)",
        }.get(reason, reason)
        return (
            f"<b>{emoji} Iron Pulse "
            f"{'WON' if pnl > 0 else 'LOST'}</b>\n\n"
            f"<b>Direction:</b> <code>{direction}</code>\n"
            f"<b>Strike:</b> <code>{setup['strike']} "
            f"{setup.get('option_type', '')}</code>\n"
            f"<b>Entry:</b> <code>Rs {setup['entry_premium']:.2f}</code>\n"
            f"<b>Exit:</b> <code>Rs {exit_premium:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.1f}%</code>\n"
            f"<b>Reason:</b> {reason_text}\n\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )

    @staticmethod
    def _format_t1_alert(setup, current, pnl, trailing_sl) -> str:
        direction = ("BUY CALL" if setup["direction"] == "BUY_CALL"
                     else "BUY PUT")
        return (
            f"<b>\U0001f49a T1 HIT \u2014 Iron Pulse</b>\n\n"
            f"<b>Direction:</b> <code>{direction}</code>\n"
            f"<b>Strike:</b> <code>{setup['strike']} "
            f"{setup.get('option_type', '')}</code>\n"
            f"<b>Entry:</b> <code>Rs {setup['entry_premium']:.2f}</code>\n"
            f"<b>Current:</b> <code>Rs {current:.2f}</code>\n"
            f"<b>P&L:</b> <code>{pnl:+.1f}%</code> (1:1 RR)\n\n"
            f"<b>Trailing SL:</b> <code>Rs {trailing_sl:.2f}</code> "
            f"(15% below peak)\n\n"
            f"<i>Book at T1 now, or let it ride \u2014 "
            f"trailing SL active!</i>\n"
            f"<i>Time: {datetime.now().strftime('%H:%M:%S')}</i>"
        )
