from __future__ import annotations

from typing import Any

import numpy as np

MAX_PATHS = 2000
MAX_TRADES = 10000


def _validate(trade_pnls: list[float], paths: int, block_length: int, method: str) -> None:
    if not trade_pnls:
        raise ValueError("Simulation membutuhkan minimal satu completed trade.")
    if len(trade_pnls) > MAX_TRADES:
        raise ValueError(f"Maksimal {MAX_TRADES} trade per simulasi.")
    if not 10 <= paths <= MAX_PATHS:
        raise ValueError(f"Jumlah paths harus 10 sampai {MAX_PATHS}.")
    if method == "block_bootstrap" and not 1 <= block_length <= len(trade_pnls):
        raise ValueError("Block length tidak valid.")


def _stats(paths: np.ndarray, initial_equity: float, threshold: float) -> dict[str, Any]:
    terminal = paths[:, -1]
    peaks = np.maximum.accumulate(paths, axis=1)
    drawdowns = paths / np.maximum(peaks, 1e-12) - 1
    max_dd = drawdowns.min(axis=1)
    q = lambda x: [round(float(v), 6) for v in np.percentile(x, [5, 25, 50, 75, 95])]
    return {
        "terminal_equity_percentiles": q(terminal),
        "max_drawdown_percentiles": q(max_dd * 100),
        "probability_of_loss_pct": round(float(np.mean(terminal < initial_equity) * 100), 4),
        "probability_drawdown_breach_pct": round(float(np.mean(max_dd <= -abs(threshold) / 100) * 100), 4),
        "probability_equity_below_zero_pct": round(float(np.mean(paths.min(axis=1) <= 0) * 100), 4),
        "terminal_equity": [round(float(v), 6) for v in terminal],
        "max_drawdown_pct": [round(float(v) * 100, 6) for v in max_dd],
        "path_percentiles": {
            "p05": [round(float(v), 6) for v in np.percentile(paths, 5, axis=0)],
            "p50": [round(float(v), 6) for v in np.percentile(paths, 50, axis=0)],
            "p95": [round(float(v), 6) for v in np.percentile(paths, 95, axis=0)],
        },
    }


def run_simulation(result: dict[str, Any], method: str = "bootstrap", paths: int = 500,
                   seed: int = 42, block_length: int = 5, drawdown_threshold_pct: float = 20.0,
                   extra_cost_bps: float = 0.0) -> dict[str, Any]:
    trades = result.get("trades", [])
    pnls = np.asarray([float(t["net_pnl"]) for t in trades], dtype=float)
    if not np.isfinite(extra_cost_bps) or not 0 <= extra_cost_bps <= 1000:
        raise ValueError("Extra cost harus 0..1000 bps per side.")
    if extra_cost_bps:
        if any("entry_notional" not in t or "exit_notional" not in t for t in trades):
            raise ValueError("Cost stress membutuhkan entry/exit notional pada semua trade.")
        pnls -= np.asarray([abs(float(t["entry_notional"])) + abs(float(t["exit_notional"])) for t in trades]) * extra_cost_bps / 10000
    initial = float(result["metrics"]["initial_equity"])
    if not np.isfinite(pnls).all() or not np.isfinite(initial) or initial <= 0:
        raise ValueError("PnL harus finite dan initial equity harus positif.")
    if not np.isfinite(drawdown_threshold_pct) or not 0 < drawdown_threshold_pct <= 100:
        raise ValueError("Drawdown threshold harus lebih dari 0 sampai 100.")
    if paths * (len(pnls) + 1) > 2_000_000:
        raise ValueError("Simulation melebihi budget 2 juta path points; kurangi jumlah paths.")
    if method not in {"bootstrap", "permutation", "block_bootstrap"}:
        raise ValueError("Method simulation tidak dikenal.")
    _validate(pnls.tolist(), paths, block_length, method)
    rng = np.random.default_rng(seed)
    n = len(pnls)
    sampled = np.empty((paths, n), dtype=float)
    for i in range(paths):
        if method == "permutation":
            sampled[i] = rng.permutation(pnls)
        elif method == "block_bootstrap":
            row = []
            while len(row) < n:
                start = int(rng.integers(0, n - block_length + 1))
                row.extend(pnls[start:start + block_length])
            sampled[i] = row[:n]
        else:
            sampled[i] = rng.choice(pnls, size=n, replace=True)
    paths_equity = initial + np.cumsum(sampled, axis=1)
    paths_equity = np.column_stack([np.full(paths, initial), paths_equity])
    return {
        "source": "completed trade ledger",
        "method": method,
        "seed": int(seed),
        "paths": int(paths),
        "trade_count": n,
        "block_length": int(block_length),
        "drawdown_threshold_pct": float(drawdown_threshold_pct),
        "extra_cost_bps": float(extra_cost_bps),
        "observed_terminal_equity": float(initial + pnls.sum()),
        "warnings": ["Simulation mengukur path uncertainty, bukan bukti out-of-sample atau proof of edge.",
                     "Fixed absolute PnL resampling; tidak menghitung ulang sizing, margin atau liquidation. Paths dapat melewati nol."],
        **_stats(paths_equity, initial, drawdown_threshold_pct),
    }
