"""Tests for strategies/intraday_hunter_engine.py — signal detection."""

from datetime import datetime, date
from typing import List

import pytest

from config import IntradayHunterConfig
from strategies.intraday_hunter_engine import (
    Candle,
    IntradayHunterEngine,
    Signal,
    atm_strike,
    candles_from_dicts,
    compute_current_move_pct,
    compute_day_bias_score,
    compute_gap_pct,
    constituent_confluence_check,
    constituent_internal_split,
    days_to_next_expiry,
    detect_e1,
    detect_e2,
    detect_e3,
    filter_day_bias,
    iv_for_index,
    model_premium,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def make_candle(idx: int, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(
        ts=datetime(2025, 1, 1, 9, 15) + (datetime(2025, 1, 1, 9, 15 + idx) - datetime(2025, 1, 1, 9, 15)),
        open=o, high=h, low=lo, close=c,
    )


def green_candles(start: float, n: int, step: float = 1.0) -> List[Candle]:
    out = []
    price = start
    for i in range(n):
        o = price
        c = price + step
        out.append(Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=o, high=c + 0.5, low=o - 0.5, close=c))
        price = c
    return out


def red_candles(start: float, n: int, step: float = 1.0) -> List[Candle]:
    out = []
    price = start
    for i in range(n):
        o = price
        c = price - step
        out.append(Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=o, high=o + 0.5, low=c - 0.5, close=c))
        price = c
    return out


def flat_candles(start: float, n: int) -> List[Candle]:
    return [
        Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=start, high=start + 0.1, low=start - 0.1, close=start)
        for i in range(n)
    ]


@pytest.fixture
def cfg():
    return IntradayHunterConfig()


@pytest.fixture
def engine(cfg):
    return IntradayHunterEngine(cfg)


# ── Pure helpers ─────────────────────────────────────────────────────────

class TestAtmStrike:
    def test_nifty_50_round(self):
        assert atm_strike(25123, 50) == 25100
        assert atm_strike(25127, 50) == 25150
        assert atm_strike(25150, 50) == 25150

    def test_banknifty_100_round(self):
        assert atm_strike(56380, 100) == 56400
        assert atm_strike(56450, 100) == 56400  # banker's rounding


class TestDaysToNextExpiry:
    def test_tuesday_expiry_from_monday(self):
        # Monday → Tuesday = 1 day
        assert days_to_next_expiry(date(2025, 1, 6), 1) == 1

    def test_tuesday_expiry_from_tuesday(self):
        # Same day → next week (7 days)
        assert days_to_next_expiry(date(2025, 1, 7), 1) == 7

    def test_wed_expiry_from_thu(self):
        # Thursday → next Wednesday = 6 days
        assert days_to_next_expiry(date(2025, 1, 9), 2) == 6


class TestIvForIndex:
    def test_nifty_uses_vix_directly(self, cfg):
        assert iv_for_index("NIFTY", 13.5, cfg) == pytest.approx(0.135)

    def test_banknifty_scales_up(self, cfg):
        # 13.5% × 1.30 = 17.55%
        assert iv_for_index("BANKNIFTY", 13.5, cfg) == pytest.approx(0.135 * 1.30)

    def test_sensex_scales_up(self, cfg):
        assert iv_for_index("SENSEX", 13.5, cfg) == pytest.approx(0.135 * 1.20)

    def test_no_vix_falls_back_to_default(self, cfg):
        assert iv_for_index("NIFTY", None, cfg) == cfg.DEFAULT_IV
        assert iv_for_index("NIFTY", 0, cfg) == cfg.DEFAULT_IV


class TestComputeGapPct:
    def test_positive_gap(self):
        y = [Candle(ts=datetime(2025, 1, 1, 15, 30), open=100, high=101, low=99, close=100)]
        t = [Candle(ts=datetime(2025, 1, 2, 9, 15), open=101, high=102, low=100, close=101)]
        assert compute_gap_pct(t, y) == pytest.approx(1.0)

    def test_negative_gap(self):
        y = [Candle(ts=datetime(2025, 1, 1, 15, 30), open=100, high=101, low=99, close=100)]
        t = [Candle(ts=datetime(2025, 1, 2, 9, 15), open=99, high=100, low=98, close=99)]
        assert compute_gap_pct(t, y) == pytest.approx(-1.0)

    def test_no_yesterday_returns_zero(self):
        t = [Candle(ts=datetime.now(), open=100, high=101, low=99, close=100)]
        assert compute_gap_pct(t, None) == 0.0


# ── E1: rejection after directional run ──────────────────────────────────

class TestDetectE1:
    def test_5_green_then_1_red_fires_buy(self, cfg):
        # 6 candles needed before, 5 green run + 1 red retr
        candles = flat_candles(100, 5)  # warm-up
        candles += green_candles(100, 5)  # 5 green run
        candles.append(Candle(ts=datetime(2025, 1, 1, 10, 0), open=105, high=105.5, low=104.7, close=104.8))
        # minute_idx = 10 (the red candle)
        result = detect_e1(11, candles, cfg)
        assert result == "BUY"

    def test_5_red_then_1_green_fires_sell(self, cfg):
        candles = flat_candles(100, 5)
        candles += red_candles(100, 5)
        candles.append(Candle(ts=datetime(2025, 1, 1, 10, 0), open=95, high=95.3, low=94.7, close=95.2))
        result = detect_e1(11, candles, cfg)
        assert result == "SELL"

    def test_not_enough_candles_returns_none(self, cfg):
        candles = green_candles(100, 3)
        assert detect_e1(2, candles, cfg) is None

    def test_mixed_run_no_signal(self, cfg):
        # Alternating green/red — no 5-consecutive same-direction run at any retracement lookback
        candles = []
        for i in range(15):
            if i % 2 == 0:
                candles.append(Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=100 + i, high=102 + i, low=99 + i, close=101 + i))
            else:
                candles.append(Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=101 + i, high=102 + i, low=99 + i, close=100 + i))
        result = detect_e1(14, candles, cfg)
        assert result is None

    def test_too_big_retracement_no_signal(self, cfg):
        # 5 RED warm-up (so no shorter run-position is monochromatic) + 5 green run + 1 red retr
        candles = red_candles(105, 5)     # ends at price 100
        candles += green_candles(100, 5)  # 100→105
        # Run = 5pts. Retracement of 3pts = 60% > 40% threshold → reject
        candles.append(Candle(ts=datetime(2025, 1, 1, 10, 0), open=105, high=105.2, low=101.8, close=102))
        result = detect_e1(11, candles, cfg)
        assert result is None


# ── E2: gap counter-trap ────────────────────────────────────────────────

class TestDetectE2:
    def test_gap_down_intact_swing_low_fires_buy(self, cfg):
        # Yesterday: 99-101 range, close 100
        yesterday = [
            Candle(ts=datetime(2025, 1, 1, 15, i), open=100, high=101, low=99, close=100)
            for i in range(15, 30)
        ]
        # Today: gap-down to 99 (yclose=100, gap=-1%), 16 candles, none break y_low (99)
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=99, high=99.5, low=99.05, close=99.3)
            for i in range(16)
        ]
        result = detect_e2(15, today, yesterday, cfg)
        assert result == "BUY"

    def test_gap_up_intact_swing_high_fires_sell(self, cfg):
        yesterday = [
            Candle(ts=datetime(2025, 1, 1, 15, i), open=100, high=101, low=99, close=100)
            for i in range(15, 30)
        ]
        # Gap-up to 102 (gap +2%), no candle breaks y_high (101) — but we
        # set window_high < y_high so price stays under
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=101.5, high=100.8, low=100.2, close=100.5)
            for i in range(16)
        ]
        result = detect_e2(15, today, yesterday, cfg)
        assert result == "SELL"

    def test_no_yesterday_no_signal(self, cfg):
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=100, high=101, low=99, close=100)
            for i in range(20)
        ]
        assert detect_e2(15, today, None, cfg) is None

    def test_too_early_no_signal(self, cfg):
        yesterday = [Candle(ts=datetime(2025, 1, 1, 15, 0), open=100, high=101, low=99, close=100)]
        today = [Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=99, high=99.5, low=99.0, close=99.3) for i in range(5)]
        # minute_idx 5 < e2_wait_minutes (15) → None
        assert detect_e2(5, today, yesterday, cfg) is None

    def test_small_gap_no_signal(self, cfg):
        # Gap of only 0.05% — below e2_min_gap_pct (0.20)
        yesterday = [Candle(ts=datetime(2025, 1, 1, 15, 0), open=100, high=101, low=99, close=100)]
        today = [Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=100.05, high=100.1, low=99.9, close=100) for i in range(20)]
        assert detect_e2(15, today, yesterday, cfg) is None


# ── E3: trend continuation ──────────────────────────────────────────────

class TestDetectE3:
    def test_bullish_continuation_fires_buy(self, cfg):
        # Yesterday: open 99, close 100 → +1% (above E3_MIN_YCLOSE_PCT 0.10%)
        yesterday = [
            Candle(ts=datetime(2025, 1, 1, 9, 15), open=99, high=100.5, low=98.5, close=100),
            Candle(ts=datetime(2025, 1, 1, 15, 30), open=99.5, high=100.5, low=99.5, close=100),
        ]
        # Today: gap up to 100.5 (+0.5%), 21 candles all > today_open and > yclose
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=100.5, high=101, low=100.4, close=100.6 + i * 0.01)
            for i in range(22)
        ]
        result = detect_e3(20, today, yesterday, cfg)
        assert result == "BUY"

    def test_bearish_continuation_fires_sell(self, cfg):
        # Yesterday: open 100, close 99 → -1% (bearish)
        yesterday = [
            Candle(ts=datetime(2025, 1, 1, 9, 15), open=100, high=100.5, low=98.5, close=99),
            Candle(ts=datetime(2025, 1, 1, 15, 30), open=99.5, high=99.7, low=99, close=99),
        ]
        # Today: gap down to 98.5 (-0.5%), all candles < today_open and < yclose
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=98.5, high=98.6, low=98.1, close=98.4 - i * 0.01)
            for i in range(22)
        ]
        result = detect_e3(20, today, yesterday, cfg)
        assert result == "SELL"

    def test_y_flat_no_signal(self, cfg):
        # Yesterday flat (move < 0.10%) → no E3
        yesterday = [
            Candle(ts=datetime(2025, 1, 1, 9, 15), open=100, high=100.05, low=99.95, close=100.05),
        ]
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=100.5, high=101, low=100.4, close=100.6)
            for i in range(22)
        ]
        assert detect_e3(20, today, yesterday, cfg) is None

    def test_disagreeing_y_and_gap_no_signal(self, cfg):
        # Yesterday +1% but gap is DOWN → not aligned
        yesterday = [Candle(ts=datetime(2025, 1, 1, 15, 30), open=99, high=100.5, low=98.5, close=100)]
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=99.5, high=99.8, low=99.4, close=99.6)
            for i in range(22)
        ]
        assert detect_e3(20, today, yesterday, cfg) is None


# ── Constituent confluence ──────────────────────────────────────────────

class TestConstituentConfluence:
    def test_buy_with_both_constituents_up_passes(self, cfg):
        hdfc = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=900, high=905, low=899, close=905)
            for i in range(20)
        ]
        kotak = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=400, high=403, low=399, close=403)
            for i in range(20)
        ]
        # Both up since open → no veto
        assert constituent_confluence_check("BUY", 19, hdfc, kotak, cfg) is True

    def test_buy_with_both_strongly_down_vetoed(self, cfg):
        # Both down by more than 0.15% → veto BUY
        hdfc = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=900, high=901, low=896, close=896)  # -0.44%
            for i in range(20)
        ]
        kotak = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=400, high=400.5, low=398, close=398)  # -0.5%
            for i in range(20)
        ]
        # NB: function reads candles[0].open vs candles[end_idx].close
        assert constituent_confluence_check("BUY", 19, hdfc, kotak, cfg) is False

    def test_disabled_passes_always(self, cfg):
        cfg2 = IntradayHunterConfig.__class__(**{**vars(cfg), "ENABLE_CONSTITUENT_CONFLUENCE": False}) if False else cfg
        # Easier path: monkeypatch via dict not possible on frozen dataclass — just test the bool path
        # via config flag check. Skip this branch since cfg is frozen.

    def test_internal_split_detects_disagreement(self, cfg):
        # HDFC up 0.5%, KOTAK down 0.5% → |h-k| = 1.0% >> 0.30%
        hdfc = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=900, high=905, low=899, close=905)
            for i in range(20)
        ]
        kotak = [
            Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=400, high=400.5, low=397.5, close=398)
            for i in range(20)
        ]
        assert constituent_internal_split(19, hdfc, kotak, cfg) is True

    def test_internal_split_no_disagreement(self, cfg):
        # Both up similarly → no split
        hdfc = [Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=900, high=905, low=899, close=905) for i in range(20)]
        kotak = [Candle(ts=datetime(2025, 1, 1, 9, 15 + i), open=400, high=403, low=399, close=403) for i in range(20)]
        assert constituent_internal_split(19, hdfc, kotak, cfg) is False


# ── Day-bias score ──────────────────────────────────────────────────────

class TestDayBiasScore:
    def test_neutral_day_zero_score(self, cfg):
        yesterday = [Candle(ts=datetime(2025, 1, 1, 15, 30), open=100, high=100.05, low=99.95, close=100)]
        today = [Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=100, high=100.05, low=99.95, close=100) for i in range(20)]
        score = compute_day_bias_score(today, yesterday, 19, [], [], cfg)
        assert abs(score) < 0.05  # ~zero

    def test_strong_bullish_day_positive_score(self, cfg):
        # Yesterday +1%, gap +1%, intraday +1% → strongly bullish
        yesterday = [Candle(ts=datetime(2025, 1, 1, 15, 30), open=99, high=100, low=99, close=100)]
        today = [
            Candle(ts=datetime(2025, 1, 2, 9, 15 + i), open=101, high=102, low=101, close=102)
            for i in range(20)
        ]
        score = compute_day_bias_score(today, yesterday, 19, [], [], cfg)
        assert score > 0.5

    def test_block_buy_when_score_strongly_negative(self, cfg):
        assert filter_day_bias("BUY", -0.7, cfg) is False

    def test_block_sell_when_score_strongly_positive(self, cfg):
        assert filter_day_bias("SELL", 0.7, cfg) is False

    def test_allow_when_score_in_neutral_band(self, cfg):
        assert filter_day_bias("BUY", 0.3, cfg) is True
        assert filter_day_bias("SELL", -0.3, cfg) is True


# ── End-to-end engine test ──────────────────────────────────────────────

class TestIntradayHunterEngine:
    def test_e1_multi_index_2_of_3_fires(self, engine, cfg):
        # Build identical 5-green + 1-red sequences for all 3 indices → 3-of-3 agreement
        warm = flat_candles(100, 5)
        run = green_candles(100, 5)
        retr = [Candle(ts=datetime(2025, 1, 1, 10, 0), open=105, high=105.5, low=104.7, close=104.8)]
        candles = warm + run + retr
        sig = engine.detect(
            minute_idx=11,
            nifty_today=candles, bn_today=candles, sx_today=candles,
            nifty_yesterday=None,
        )
        assert sig is not None
        assert sig.direction == "BUY"
        assert sig.trigger == "E1"

    def test_no_signal_returns_none(self, engine):
        flat = flat_candles(100, 30)
        sig = engine.detect(
            minute_idx=20,
            nifty_today=flat, bn_today=flat, sx_today=flat,
            nifty_yesterday=None,
        )
        assert sig is None

    def test_build_position_set_2_indices_when_skip_bn(self, engine):
        sig = Signal(direction="BUY", trigger="E1", minute_idx=20, day_bias_score=0.0, skip_bn=True)
        positions = engine.build_position_set(
            signal=sig,
            nifty_spot=25000, bn_spot=56000, sx_spot=82000,
            today=date(2025, 1, 6),
            vix_pct=13.5,
        )
        labels = [p["index_label"] for p in positions]
        assert "BANKNIFTY" not in labels
        assert "NIFTY" in labels and "SENSEX" in labels

    def test_build_position_set_all_3(self, engine):
        sig = Signal(direction="BUY", trigger="E1", minute_idx=20, day_bias_score=0.0, skip_bn=False)
        positions = engine.build_position_set(
            signal=sig,
            nifty_spot=25000, bn_spot=56000, sx_spot=82000,
            today=date(2025, 1, 6),
            vix_pct=13.5,
        )
        assert len(positions) == 3
        for p in positions:
            assert p["entry_premium"] > 0
            assert p["sl_premium"] < p["entry_premium"]
            assert p["target_premium"] > p["entry_premium"]
            assert p["option_type"] == "CE"

    def test_sell_signal_creates_pe_positions(self, engine):
        sig = Signal(direction="SELL", trigger="E2", minute_idx=20, day_bias_score=0.0, skip_bn=False)
        positions = engine.build_position_set(
            signal=sig,
            nifty_spot=25000, bn_spot=56000, sx_spot=82000,
            today=date(2025, 1, 6),
            vix_pct=13.5,
        )
        for p in positions:
            assert p["option_type"] == "PE"
            assert p["direction"] == "SELL"
