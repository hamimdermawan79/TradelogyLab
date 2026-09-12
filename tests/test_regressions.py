import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics import analyze
from engine import EngineConfig, run_backtest, load_ohlcv, validate_strategy_output
from test_engine import sample_data


def test_daily_ratios_and_initial_drawdown():
    levels = [900, 990, 891, 980.1]
    curve = [{"time": str(t), "equity": v} for t, v in zip(pd.date_range("2025-01-01", periods=4), levels)]
    result = analyze(curve, [], 1000)
    r = np.array([.1, -.1, .1])
    assert result["metrics"]["sharpe"] == pytest.approx(r.mean()/r.std(ddof=1)*math.sqrt(365.25), abs=1e-4)
    assert result["metrics"]["sortino"] == pytest.approx(r.mean()/np.sqrt(np.mean(np.minimum(r, 0)**2))*math.sqrt(365.25), abs=1e-4)
    assert result["metrics"]["max_drawdown_pct"] == pytest.approx(-10.9)
    assert result["monthly_returns"][0]["return_pct"] == pytest.approx(-1.99)
    json.dumps(result, allow_nan=False)


def test_cost_attribution_reconciles():
    result = run_backtest(sample_data(), pd.DataFrame({"signal": [1, -1, 0, 0]}), EngineConfig())
    assert abs(result["metrics"]["pnl_reconciliation_error"]) < 1e-5
    for t in result["trades"]:
        assert t["price_pnl_before_costs"] - t["spread_cost"] - t["slippage_cost"] - t["fees"] == pytest.approx(t["net_pnl"], abs=3e-6)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_cost(value):
    with pytest.raises(ValueError):
        EngineConfig(taker_fee_bps=value).validate()


def test_bad_data_and_index_rejected(tmp_path):
    data = sample_data()
    data.loc[1, "Close"] = np.nan
    path = tmp_path / "bad.csv"
    data.to_csv(path, index=False)
    with pytest.raises(ValueError):
        load_ohlcv(path)
    with pytest.raises(ValueError, match="Index"):
        validate_strategy_output(sample_data(), pd.DataFrame({"signal": [0]*4}, index=[3,2,1,0]))


def test_gui_results_navigation_settles():
    from app import App
    app = App()
    app.withdraw()
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    result = run_backtest(sample_data(), pd.DataFrame({"signal": [0]*4}), EngineConfig())
    app.run_data = {"run_id": "regression", "result": result, "manifest": {"config": {}}}
    app._show_page("results")
    for i in range(30):
        app._notebook.select(i % len(app._notebook.tabs()))
        app.update()
        time.sleep(.01)
    deadline = time.monotonic() + .5
    while time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    assert not errors
    assert app._render_job is None
    assert len(app._kpi_frame.winfo_children()) == 16
    app._close()


def test_worker_export_undefined_metrics_preserves_notes(tmp_path):
    from worker import main
    from export import export_to_obsidian
    data_path = tmp_path / "candles.csv"
    sample_data().to_csv(data_path, index=False)
    run_dir = tmp_path / "run"
    request = {"run_id": "test", "run_dir": str(run_dir), "dataset": str(data_path),
               "strategy_code": "def generate_signals(data, params):\n    return pd.DataFrame({'signal': 0}, index=data.index)\n",
               "parameters": {}, "config": EngineConfig().__dict__}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    assert main(str(request_path)) == 0
    first = export_to_obsidian(run_dir, tmp_path / "vault", "Flat", "hypothesis", "note", "observation")
    first.write_text("manual notes", encoding="utf-8")
    second = export_to_obsidian(run_dir, tmp_path / "vault", "Flat", "hypothesis", "note", "observation")
    assert first != second
    assert first.read_text(encoding="utf-8") == "manual notes"
    assert "N/A" in second.read_text(encoding="utf-8")


def test_flatten_zero_size():
    result = run_backtest(sample_data(), pd.DataFrame({"signal": [1]*4, "size_pct": [.1, 0, 0, 0]}), EngineConfig())
    assert result["trades"][0]["exit_time"].startswith("2026-01-01 00:02")


def test_depleted_account_stops_and_stays_json_safe():
    """Account wiped out by losses must stop the loop and never emit NaN monthly returns."""
    # 300 falling bars: short entry with full size will bleed equity to the 1e-6 floor.
    n = 300
    data = pd.DataFrame({
        "Time": pd.date_range("2026-01-01", periods=n, freq="min"),
        "Open": [1000.0 - i for i in range(n)],
        "High": [1001.0 - i for i in range(n)],
        "Low": [999.0 - i for i in range(n)],
        "Close": [1000.5 - i for i in range(n)],
        "Volume": [10.0] * n,
    })
    strategy = pd.DataFrame({"signal": [1] * n, "size_pct": [1.0] * n, "leverage": [10.0] * n})
    result = run_backtest(data, strategy, EngineConfig())
    assert result["metrics"]["final_equity"] < 1.0  # depleted
    # loop must stop before consuming all bars once equity hits the floor
    assert len(result["equity"]) < n
    json.dumps(result["metrics"], allow_nan=False)
    json.dumps(result["monthly_returns"], allow_nan=False)
    for entry in result["monthly_returns"]:
        assert entry["return_pct"] is None or math.isfinite(entry["return_pct"])
