from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

_SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60


def analyze(equity: list[dict[str, Any]], trades: list[dict[str, Any]], initial_equity: float) -> dict[str, Any]:
    values = pd.Series([point["equity"] for point in equity], dtype=float)
    times = pd.to_datetime([point["time"] for point in equity], utc=True)
    if values.empty or not np.isfinite(values).all() or initial_equity <= 0:
        raise ValueError("Equity harus berisi angka finite dan initial equity positif.")
    series = pd.Series(values.to_numpy(), index=times)
    # Daily close-to-close observations; omit the first incomplete day from risk ratios.
    daily = series.resample("D").last().dropna()
    returns = daily.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    intervals = pd.Series(times).diff().dt.total_seconds().dropna()
    median_seconds = float(intervals.median()) if not intervals.empty else 0.0
    periods_per_year = 365.25
    years = max((times[-1] - times[0]).total_seconds() / _SECONDS_PER_YEAR, 0.0) if len(times) > 1 else 0.0

    peaks = values.cummax().clip(lower=initial_equity)
    drawdowns = values.div(peaks).sub(1.0).fillna(0.0)
    active_underwater = longest_underwater = current_underwater = 0
    for value in drawdowns:
        current_underwater = current_underwater + 1 if value < 0 else 0
        longest_underwater = max(longest_underwater, current_underwater)
    if len(drawdowns) and drawdowns.iloc[-1] < 0:
        active_underwater = current_underwater

    standard_deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside_deviation = float(np.sqrt(np.mean(np.square(np.minimum(returns, 0))))) if len(returns) else 0.0
    mean_return = float(returns.mean()) if len(returns) else 0.0
    volatility = standard_deviation * math.sqrt(periods_per_year) if standard_deviation > 0 else 0.0
    sharpe = mean_return / standard_deviation * math.sqrt(periods_per_year) if standard_deviation > 0 else None
    sortino = mean_return / downside_deviation * math.sqrt(periods_per_year) if downside_deviation > 0 else None
    final_equity = float(values.iloc[-1])
    total_return = final_equity / initial_equity - 1.0
    cagr = None
    if years >= 1 / 365.25 and final_equity > 0:
        exponent = math.log(final_equity / initial_equity) / years
        cagr = math.expm1(exponent) if exponent < 700 else None
    max_drawdown = abs(float(drawdowns.min()))
    calmar = cagr / max_drawdown if max_drawdown > 0 and cagr is not None else None

    trade_pnls = pd.Series([trade["net_pnl"] for trade in trades], dtype=float)
    winners = trade_pnls[trade_pnls > 0]
    losers = trade_pnls[trade_pnls < 0]
    gross_profit = float(winners.sum())
    gross_loss = abs(float(losers.sum()))
    payoff = float(winners.mean() / abs(losers.mean())) if len(winners) and len(losers) else None
    var_95 = float(returns.quantile(0.05)) if len(returns) else 0.0
    tail = returns[returns <= var_95]

    month_ends = series.resample(pd.offsets.MonthEnd()).last().dropna()
    monthly = month_ends / month_ends.shift(1, fill_value=initial_equity) - 1
    # ponytail: after account depletion a previous month-end of 0 makes pct undefined; emit None, not NaN.
    monthly_returns = [
        {"year": int(index.year), "month": int(index.month),
         "return_pct": round(float(value) * 100, 4) if np.isfinite(value) else None}
        for index, value in monthly.items()
    ]

    exposure_bars = sum(max(0, int(trade.get("holding_bars", 0))) for trade in trades)
    turnover = sum(abs(float(trade.get("entry_notional", 0))) + abs(float(trade.get("exit_notional", 0))) for trade in trades)
    holding = [float(trade.get("holding_hours", 0)) for trade in trades]
    rolling_std = returns.rolling(90, min_periods=90).std(ddof=1)
    rolling_sharpe = (returns.rolling(90, min_periods=90).mean() / rolling_std.replace(0, np.nan) * math.sqrt(365.25)).dropna()
    annual_ends = series.resample(pd.offsets.YearEnd()).last().dropna()
    annual_returns = annual_ends / annual_ends.shift(1, fill_value=initial_equity) - 1
    annual_returns = annual_returns.where(np.isfinite(annual_returns), None)

    return {
        "metrics": {
            "initial_equity": initial_equity,
            "final_equity": round(final_equity, 6),
            "net_pnl": round(final_equity - initial_equity, 6),
            "return_pct": round(total_return * 100, 4),
            "cagr_pct": round(cagr * 100, 4) if cagr is not None else None,
            "annual_volatility_pct": round(volatility * 100, 4),
            "max_drawdown_pct": round(float(drawdowns.min()) * 100, 4),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "sortino": round(sortino, 4) if sortino is not None else None,
            "calmar": round(calmar, 4) if calmar is not None else None,
            "daily_return_observations": len(returns),
            "sample_days": round(years * 365.25, 4),
            "longest_underwater_hours": round(longest_underwater * median_seconds / 3600, 4),
            "gross_profit": round(gross_profit, 6),
            "gross_loss": round(gross_loss, 6),
            "best_trade": float(trade_pnls.max()) if trades else None,
            "worst_trade": float(trade_pnls.min()) if trades else None,
            "trades": len(trades),
            "win_rate_pct": round(len(winners) / len(trades) * 100, 2) if trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
            "payoff_ratio": round(payoff, 4) if payoff is not None else None,
            "expectancy": round(float(trade_pnls.mean()), 6) if trades else 0.0,
            "exposure_pct": round(exposure_bars / max(1, len(equity) - 1) * 100, 4),
            "turnover_equity_x": round(turnover / initial_equity, 4),
            "average_holding_hours": round(float(np.mean(holding)), 4) if holding else 0.0,
            "longest_underwater_bars": longest_underwater,
            "active_underwater_bars": active_underwater,
            "skewness": round(float(returns.skew()), 4) if len(returns) > 2 and standard_deviation > 0 else None,
            "kurtosis": round(float(returns.kurt()), 4) if len(returns) > 3 and standard_deviation > 0 else None,
            "var_95_pct": round(var_95 * 100, 4),
            "expected_shortfall_95_pct": round(float(tail.mean()) * 100, 4) if len(tail) else 0.0,
        },
        "drawdown": [
            {"time": str(point["time"]), "drawdown_pct": round(float(value) * 100, 6)}
            for point, value in zip(equity, drawdowns)
        ],
        "monthly_returns": monthly_returns,
        "annual_returns": [{"year": int(t.year), "return_pct": round(float(v)*100, 4) if v is not None and np.isfinite(v) else None} for t, v in annual_returns.items()],
        "rolling_sharpe_90d": [{"time": str(t), "value": round(float(v), 6)} for t, v in rolling_sharpe.items()],
        "methodology": {"risk_frequency": "daily", "annualization": 365.25,
                        "risk_free_rate": 0.0, "minimum_acceptable_return": 0.0,
                        "tail_risk": "historical daily returns, signed lower tail",
                        "undefined_values": "null", "first_partial_day_excluded_from_ratios": True},
        "warnings": (["Kurang dari 30 daily returns; estimasi risiko belum stabil."] if len(returns) < 30 else [])
                    + (["Equity nonpositif: asumsi return dan solvabilitas tidak valid."] if (values <= 0).any() else []),
    }
