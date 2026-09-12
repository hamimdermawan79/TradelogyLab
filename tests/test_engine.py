import pandas as pd
import pytest

from engine import EngineConfig, run_backtest, validate_strategy_output


def sample_data():
    return pd.DataFrame({
        "Time": pd.date_range("2026-01-01", periods=4, freq="min"),
        "Open": [100.0, 100.0, 110.0, 120.0],
        "High": [101.0, 111.0, 121.0, 121.0],
        "Low": [99.0, 99.0, 109.0, 119.0],
        "Close": [100.0, 110.0, 120.0, 120.0],
        "Volume": [10.0] * 4,
    })


ZERO_COST = EngineConfig(taker_fee_bps=0, half_spread_bps=0, slippage_bps=0, default_leverage=1)


def test_signal_executes_on_next_bar_open():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    trade = result["trades"][0]
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 120.0
    assert trade["entry_time"].startswith("2026-01-01 00:01:00")


def test_round_trip_fees_reduce_equity():
    data = sample_data().assign(Open=100.0, High=100.0, Low=100.0, Close=100.0)
    strategy = pd.DataFrame({"signal": [1, 0, 0, 0], "size_pct": [1.0] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, EngineConfig(taker_fee_bps=10, half_spread_bps=0, slippage_bps=0, default_leverage=1))
    assert result["metrics"]["final_equity"] == pytest.approx(998.0)
    assert result["metrics"]["total_fees"] == pytest.approx(2.0)


def test_rejects_leverage_above_ten():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [0] * 4, "leverage": [11] * 4})
    with pytest.raises(ValueError, match="maksimal 10"):
        validate_strategy_output(data, strategy)


def test_rejects_invalid_signal():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [0, 2, 0, 0]})
    with pytest.raises(ValueError, match="-1, 0, atau 1"):
        validate_strategy_output(data, strategy)


# --- New analytics tests ---

def test_metrics_include_institutional_fields():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    m = result["metrics"]
    for key in ("cagr_pct", "annual_volatility_pct", "sharpe", "sortino", "calmar",
                "payoff_ratio", "exposure_pct", "turnover_equity_x", "average_holding_hours",
                "longest_underwater_bars", "skewness", "kurtosis", "var_95_pct",
                "expected_shortfall_95_pct"):
        assert key in m, f"Missing metric: {key}"


def test_cagr_undefined_for_tiny_period():
    """Insufficient duration must not appear as a measured zero return."""
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    assert result["metrics"]["cagr_pct"] is None


def test_drawdown_series_returned():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    assert "drawdown" in result
    assert len(result["drawdown"]) == len(result["equity"])
    assert all("drawdown_pct" in point for point in result["drawdown"])


def test_flat_equity_no_nan():
    """All signals flat → zero trades, no NaN/Infinity in metrics."""
    data = sample_data()
    strategy = pd.DataFrame({"signal": [0, 0, 0, 0]})
    result = run_backtest(data, strategy, ZERO_COST)
    m = result["metrics"]
    assert m["trades"] == 0
    assert m["sharpe"] is None
    assert m["sortino"] is None
    assert m["calmar"] is None
    for value in m.values():
        if isinstance(value, float):
            assert not (value != value), f"NaN found in metrics"  # NaN check


def test_trade_has_holding_fields():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    trade = result["trades"][0]
    assert "holding_bars" in trade
    assert "holding_hours" in trade
    assert "entry_notional" in trade
    assert "exit_notional" in trade
    assert trade["holding_bars"] == 2
    assert trade["holding_hours"] == pytest.approx(2 / 60, abs=0.01)


def test_monthly_returns_structure():
    data = sample_data()
    strategy = pd.DataFrame({"signal": [1, 1, 0, 0], "size_pct": [0.1] * 4, "leverage": [1.0] * 4})
    result = run_backtest(data, strategy, ZERO_COST)
    assert "monthly_returns" in result
    for entry in result["monthly_returns"]:
        assert "year" in entry
        assert "month" in entry
        assert "return_pct" in entry
