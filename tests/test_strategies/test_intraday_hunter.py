"""Tests for strategies/intraday_hunter.py — IntradayHunterStrategy.

Uses a real in-memory SQLite (via TradeRepository) so the strategy's
raw SQL helpers (_fetch_all on signal_group_id, consecutive losing days,
etc.) actually run instead of being mocked.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest

from config import IntradayHunterConfig
from core.events import EventBus, EventType
from db.schema import IH_TRADES_DDL, IH_TRADES_INDEXES
from db.trade_repo import TradeRepository
from strategies.intraday_hunter import IntradayHunterStrategy
from strategies.intraday_hunter_engine import Signal


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def mem_conn():
    """Single in-memory SQLite connection wrapped in a context-manager factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    @contextmanager
    def factory():
        yield conn

    yield factory
    conn.close()


@pytest.fixture
def repo(mem_conn):
    r = TradeRepository(conn_factory=mem_conn)
    r.init_table(IH_TRADES_DDL, IH_TRADES_INDEXES)
    return r


@pytest.fixture
def enabled_cfg(monkeypatch):
    """Force ENABLED=True at the module level so should_create() can pass.

    Also keeps AGENT_ENABLED=False so tests don't spawn `claude` subprocess.
    """
    cfg = IntradayHunterConfig(ENABLED=True, AGENT_ENABLED=False)
    monkeypatch.setattr("strategies.intraday_hunter._cfg", cfg)
    return cfg


@pytest.fixture(autouse=True)
def _disable_ih_agent(monkeypatch):
    """Force AGENT_ENABLED=False AND ENABLED=False in all IH tests.

    AGENT_ENABLED=False prevents accidental `claude` subprocess calls.
    ENABLED=False overrides any INTRADAY_HUNTER_ENABLED env var the
    developer may have set in their local .env (which would otherwise
    bleed into test runs).
    """
    cfg = IntradayHunterConfig(ENABLED=False, AGENT_ENABLED=False)
    monkeypatch.setattr("strategies.intraday_hunter._cfg", cfg)
    return cfg


@pytest.fixture
def strategy(repo):
    bus = EventBus()
    return IntradayHunterStrategy(trade_repo=repo, bus=bus)


def _insert_position(
    repo,
    *,
    group: str = "g1",
    label: str = "NIFTY",
    direction: str = "BUY",
    strike: int = 25000,
    option_type: str = "CE",
    qty: int = 65,
    entry: float = 100.0,
    sl: float = 80.0,
    tgt: float = 145.0,
    status: str = "ACTIVE",
    created_at=None,
    resolved_at=None,
    pnl_rs: float = 0.0,
    is_paper: int = 1,
):
    return repo.insert_trade(
        "ih_trades",
        signal_group_id=group,
        created_at=created_at or datetime.now(),
        index_label=label,
        direction=direction,
        strike=strike,
        option_type=option_type,
        qty=qty,
        entry_premium=entry,
        sl_premium=sl,
        target_premium=tgt,
        spot_at_creation=25000.0,
        iv_at_creation=0.135,
        vix_at_creation=13.5,
        trigger="E1",
        day_bias_score=0.0,
        notes="test",
        status=status,
        resolved_at=resolved_at,
        profit_loss_rs=pnl_rs,
        max_premium_reached=entry,
        min_premium_reached=entry,
        is_paper=is_paper,
    )


# ── Class meta ──────────────────────────────────────────────────────────

class TestMeta:
    def test_tracker_type_and_table(self, strategy):
        assert strategy.tracker_type == "intraday_hunter"
        assert strategy.table_name == "ih_trades"
        assert strategy.is_selling is False
        assert strategy.supports_pending is False


# ── should_create gate ──────────────────────────────────────────────────

class TestShouldCreate:
    def _analysis(self, **kw):
        d = {"spot_price": 25000.0}
        d.update(kw)
        return d

    def test_disabled_returns_false(self, strategy):
        # Default _cfg has ENABLED=False
        assert strategy.should_create(self._analysis()) is False

    def test_valid_passes(self, strategy, enabled_cfg):
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 0)  # Mon, in window
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(self._analysis()) is True

    def test_outside_time_window(self, strategy, enabled_cfg):
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 9, 0)  # before 09:35
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(self._analysis()) is False

    def test_no_spot(self, strategy, enabled_cfg):
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(self._analysis(spot_price=0)) is False

    def test_active_position_blocks(self, strategy, repo, enabled_cfg):
        _insert_position(repo)
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(self._analysis()) is False

    def test_max_groups_per_day(self, strategy, repo, enabled_cfg):
        # 3 distinct signal groups already today, all resolved
        for g in ("g1", "g2", "g3"):
            _insert_position(repo, group=g, status="WON", pnl_rs=100,
                             resolved_at=datetime.now() - timedelta(hours=2))
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(self._analysis()) is False

    def test_daily_loss_limit(self, strategy, repo, enabled_cfg):
        # Loss exceeds DAILY_LOSS_LIMIT_RS (3000)
        _insert_position(repo, group="g1", status="LOST", pnl_rs=-3500,
                         resolved_at=datetime.now() - timedelta(hours=2))
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 11, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert strategy.should_create(self._analysis()) is False

    def test_cooldown_after_win(self, strategy, repo, enabled_cfg):
        # Won 30 minutes ago, COOLDOWN_AFTER_WIN_MIN = 60 → blocked
        won_at = datetime(2025, 1, 6, 10, 0)
        _insert_position(repo, group="g1", status="WON", pnl_rs=500,
                         resolved_at=won_at)
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(self._analysis()) is False

    def test_cooldown_after_loss_short(self, strategy, repo, enabled_cfg):
        # Lost 35 minutes ago, COOLDOWN_AFTER_LOSS_MIN = 30 → allowed (not blocked)
        lost_at = datetime(2025, 1, 6, 10, 0)
        _insert_position(repo, group="g1", status="LOST", pnl_rs=-200,
                         resolved_at=lost_at)
        with patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 35)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.fromisoformat = datetime.fromisoformat
            assert strategy.should_create(self._analysis()) is True


# ── Internal helpers ────────────────────────────────────────────────────

class TestHelpers:
    def test_count_signal_groups_today_distinct(self, strategy, repo):
        # 2 positions sharing g1, 1 position in g2, all today
        _insert_position(repo, group="g1", label="NIFTY")
        _insert_position(repo, group="g1", label="BANKNIFTY")
        _insert_position(repo, group="g2", label="NIFTY")
        assert strategy._count_signal_groups_today() == 2

    def test_consecutive_losing_days_zero(self, strategy):
        assert strategy._consecutive_losing_days() == 0

    def test_consecutive_losing_days_three(self, strategy, repo):
        # Insert 3 prior days, all net losses
        for i in (1, 2, 3):
            d = datetime.now() - timedelta(days=i, hours=2)
            _insert_position(repo, group=f"g{i}", status="LOST", pnl_rs=-500,
                             created_at=d, resolved_at=d + timedelta(minutes=30))
        assert strategy._consecutive_losing_days() == 3

    def test_consecutive_streak_breaks_on_win_day(self, strategy, repo):
        # Day -3 LOSS, Day -2 WIN, Day -1 LOSS → streak from yesterday only = 1
        ymd = lambda days_ago: datetime.now() - timedelta(days=days_ago, hours=2)
        _insert_position(repo, group="g1", status="LOST", pnl_rs=-200,
                         created_at=ymd(1), resolved_at=ymd(1) + timedelta(minutes=10))
        _insert_position(repo, group="g2", status="WON", pnl_rs=300,
                         created_at=ymd(2), resolved_at=ymd(2) + timedelta(minutes=10))
        _insert_position(repo, group="g3", status="LOST", pnl_rs=-100,
                         created_at=ymd(3), resolved_at=ymd(3) + timedelta(minutes=10))
        assert strategy._consecutive_losing_days() == 1

    def test_cooldown_ok_no_history(self, strategy):
        assert strategy._cooldown_ok(datetime.now()) is True


# ── create_trade ────────────────────────────────────────────────────────

class TestCreateTrade:
    def _build_signal(self, *, skip_bn=False, direction="BUY"):
        sig = Signal(
            direction=direction, trigger="E1", minute_idx=20,
            day_bias_score=0.0, skip_bn=skip_bn, notes="unit test",
        )
        positions = [
            {"index_label": "NIFTY", "direction": direction, "strike": 25000,
             "option_type": "CE" if direction == "BUY" else "PE",
             "qty": 65, "entry_premium": 100.0, "sl_premium": 80.0,
             "target_premium": 145.0, "iv": 0.135},
            {"index_label": "SENSEX", "direction": direction, "strike": 82000,
             "option_type": "CE" if direction == "BUY" else "PE",
             "qty": 20, "entry_premium": 200.0, "sl_premium": 160.0,
             "target_premium": 290.0, "iv": 0.162},
        ]
        if not skip_bn:
            positions.insert(1, {
                "index_label": "BANKNIFTY", "direction": direction, "strike": 56000,
                "option_type": "CE" if direction == "BUY" else "PE",
                "qty": 30, "entry_premium": 150.0, "sl_premium": 120.0,
                "target_premium": 217.5, "iv": 0.175,
            })
        return {
            "signal": sig,
            "positions": positions,
            "vix": 13.5,
            "nifty_spot": 25000.0,
        }

    def test_creates_3_positions_same_group(self, strategy, repo):
        received = []
        strategy.bus.subscribe(EventType.TRADE_CREATED, lambda et, d: received.append(d))

        first_id = strategy.create_trade(self._build_signal(), {}, {})

        assert first_id is not None
        rows = repo._fetch_all("SELECT * FROM ih_trades ORDER BY id ASC")
        assert len(rows) == 3
        groups = {r["signal_group_id"] for r in rows}
        assert len(groups) == 1  # all share one group
        labels = {r["index_label"] for r in rows}
        assert labels == {"NIFTY", "BANKNIFTY", "SENSEX"}

        # Event published once
        assert len(received) == 1
        assert received[0]["direction"] == "BUY"
        assert received[0]["trade_id"] == first_id

    def test_skip_bn_creates_2_positions(self, strategy, repo):
        strategy.create_trade(self._build_signal(skip_bn=True), {}, {})
        rows = repo._fetch_all("SELECT * FROM ih_trades")
        assert len(rows) == 2
        labels = {r["index_label"] for r in rows}
        assert labels == {"NIFTY", "SENSEX"}
        assert "BANKNIFTY" not in labels

    def test_default_is_paper(self, strategy, repo):
        strategy.create_trade(self._build_signal(), {}, {})
        rows = repo._fetch_all("SELECT * FROM ih_trades")
        for r in rows:
            assert r["is_paper"] == 1

    def test_sell_signal_creates_pe(self, strategy, repo):
        strategy.create_trade(self._build_signal(direction="SELL"), {}, {})
        rows = repo._fetch_all("SELECT * FROM ih_trades")
        for r in rows:
            assert r["option_type"] == "PE"
            assert r["direction"] == "SELL"

    def test_invalid_signal_returns_none(self, strategy):
        assert strategy.create_trade("not a dict", {}, {}) is None
        assert strategy.create_trade({"signal": None}, {}, {}) is None
        assert strategy.create_trade({"signal": Signal("BUY", "E1", 0, 0.0, False),
                                       "positions": []}, {}, {}) is None


# ── check_and_update / exits ────────────────────────────────────────────

class TestCheckAndUpdate:
    def _make_position(self, repo):
        # Manually insert one ACTIVE NIFTY position with known SL/TGT
        return _insert_position(
            repo, group="g1", label="NIFTY", direction="BUY",
            strike=25000, option_type="CE", qty=65,
            entry=100.0, sl=80.0, tgt=145.0,
        )

    def _analysis(self, **kw):
        d = {"spot_price": 25000.0, "vix": 13.5}
        d.update(kw)
        return d

    def test_no_active_returns_none(self, strategy):
        result = strategy.check_and_update({})
        assert result is None

    def test_sl_hit_closes_position(self, strategy, repo):
        self._make_position(repo)
        # Force _get_current_premium to return below SL
        with patch.object(strategy, "_get_current_premium", return_value=70.0), \
             patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 30)  # before TIME_EXIT
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({}, analysis=self._analysis())
        assert result is not None
        assert result["closed"][0]["exit_reason"] == "SL_HIT"
        # Verify DB row updated
        row = repo._fetch_one("SELECT * FROM ih_trades WHERE id = 1")
        assert row["status"] == "LOST"
        assert row["exit_reason"] == "SL_HIT"

    def test_tgt_hit_closes_position(self, strategy, repo):
        self._make_position(repo)
        with patch.object(strategy, "_get_current_premium", return_value=150.0), \
             patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({}, analysis=self._analysis())
        assert result is not None
        assert result["closed"][0]["exit_reason"] == "TGT_HIT"
        row = repo._fetch_one("SELECT * FROM ih_trades WHERE id = 1")
        assert row["status"] == "WON"

    def test_time_exit_after_1230(self, strategy, repo):
        self._make_position(repo)
        # Premium between SL and TGT, but past TIME_EXIT (12:30)
        with patch.object(strategy, "_get_current_premium", return_value=110.0), \
             patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 12, 31)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({}, analysis=self._analysis())
        assert result is not None
        assert result["closed"][0]["exit_reason"] == "TIME_EXIT"

    def test_holds_when_in_range(self, strategy, repo):
        self._make_position(repo)
        with patch.object(strategy, "_get_current_premium", return_value=110.0), \
             patch("strategies.intraday_hunter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 6, 10, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strategy.check_and_update({}, analysis=self._analysis())
        # No exits
        assert result is None
        row = repo._fetch_one("SELECT * FROM ih_trades WHERE id = 1")
        assert row["status"] == "ACTIVE"
        assert row["last_premium"] == 110.0


# ── get_active / get_stats delegation ───────────────────────────────────

class TestDelegation:
    def test_get_active_delegates(self, strategy, repo):
        _insert_position(repo)
        active = strategy.get_active()
        assert active is not None
        assert active["status"] == "ACTIVE"

    def test_get_stats_no_repo(self):
        s = IntradayHunterStrategy(trade_repo=None)
        assert s.get_stats()["total"] == 0
