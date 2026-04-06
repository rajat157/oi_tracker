"""Tests for strategies/rr_agent.py — prompt construction + NIFTY chart sections."""

import pytest

from strategies.rr_agent import RRAgent


@pytest.fixture
def agent():
    return RRAgent()


@pytest.fixture
def minimal_signal():
    return {
        "signal_type": "PMOM",
        "direction": "BUY_CE",
        "option_type": "CE",
        "signal_data": {"consecutive_higher": 4, "premium_momentum": 20.5},
    }


@pytest.fixture
def minimal_analysis():
    return {
        "spot_price": 24000.0,
        "vix": 15.0,
        "verdict": "BOTH",
        "signal_confidence": 65,
    }


@pytest.fixture
def minimal_regime_config():
    return {
        "direction": "BOTH",
        "sl_pts": 40,
        "tgt_pts": 20,
        "max_hold": 35,
    }


class TestBuildPrompt:
    def test_includes_nifty_section_when_provided(
        self, agent, minimal_signal, minimal_analysis, minimal_regime_config
    ):
        """Prompt contains NIFTY PRICE ACTION section when charts are non-empty."""
        prompt = agent.build_prompt(
            chart_text="PREMIUM_CHART_TEXT",
            analysis_context=minimal_analysis,
            signal=minimal_signal,
            regime="NORMAL",
            regime_config=minimal_regime_config,
            trade_history_today=[],
            nifty_1min_chart="NIFTY_1MIN_TABLE",
            nifty_3min_chart="NIFTY_3MIN_TABLE",
        )
        assert "## NIFTY PRICE ACTION" in prompt
        assert "NIFTY_1MIN_TABLE" in prompt
        assert "NIFTY_3MIN_TABLE" in prompt
        assert "## OPTION PREMIUM CHART" in prompt
        assert "PREMIUM_CHART_TEXT" in prompt

    def test_no_nifty_section_when_empty(
        self, agent, minimal_signal, minimal_analysis, minimal_regime_config
    ):
        """Without NIFTY charts, the NIFTY header should not appear."""
        prompt = agent.build_prompt(
            chart_text="PREMIUM_CHART_TEXT",
            analysis_context=minimal_analysis,
            signal=minimal_signal,
            regime="NORMAL",
            regime_config=minimal_regime_config,
            trade_history_today=[],
            nifty_1min_chart="",
            nifty_3min_chart="",
        )
        assert "## NIFTY PRICE ACTION" not in prompt
        # Premium chart still present
        assert "PREMIUM_CHART_TEXT" in prompt

    def test_only_3min_provided(
        self, agent, minimal_signal, minimal_analysis, minimal_regime_config
    ):
        """Providing only 3-min still surfaces the NIFTY section."""
        prompt = agent.build_prompt(
            chart_text="PREMIUM_CHART_TEXT",
            analysis_context=minimal_analysis,
            signal=minimal_signal,
            regime="NORMAL",
            regime_config=minimal_regime_config,
            trade_history_today=[],
            nifty_3min_chart="NIFTY_3MIN_ONLY",
        )
        assert "## NIFTY PRICE ACTION" in prompt
        assert "NIFTY_3MIN_ONLY" in prompt

    def test_system_prompt_mentions_three_timeframes(
        self, agent, minimal_signal, minimal_analysis, minimal_regime_config
    ):
        """System prompt describes all three data views."""
        prompt = agent.build_prompt(
            chart_text="",
            analysis_context=minimal_analysis,
            signal=minimal_signal,
            regime="NORMAL",
            regime_config=minimal_regime_config,
            trade_history_today=[],
        )
        assert "NIFTY 3-min" in prompt
        assert "NIFTY 1-min" in prompt
        assert "premium 3-min" in prompt.lower() or "Option premium 3-min" in prompt

    def test_regime_context_in_prompt(
        self, agent, minimal_signal, minimal_analysis, minimal_regime_config
    ):
        """Regime and backtested params appear in the prompt."""
        prompt = agent.build_prompt(
            chart_text="",
            analysis_context=minimal_analysis,
            signal=minimal_signal,
            regime="HIGH_VOL_DOWN",
            regime_config=minimal_regime_config,
            trade_history_today=[],
        )
        assert "HIGH_VOL_DOWN" in prompt
        assert "SL=40 pts" in prompt
        assert "TGT=20 pts" in prompt
