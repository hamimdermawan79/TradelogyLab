from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from analytics import analyze


ENGINE_VERSION = "0.3.0"


@dataclass(frozen=True)
class EngineConfig:
    initial_equity: float = 1000.0
    max_leverage: float = 10.0
    default_leverage: float = 10.0
    taker_fee_bps: float = 5.0
    half_spread_bps: float = 1.0
    slippage_bps: float = 1.0

    def validate(self) -> None:
        if self.initial_equity != 1000.0:
            raise ValueError("Initial equity dikunci pada 1000 USDT.")
        if self.max_leverage != 10.0:
            raise ValueError("Maximum leverage dikunci pada 10x.")
        if not 0 < self.default_leverage <= self.max_leverage:
            raise ValueError("Default leverage harus lebih dari 0 dan maksimal 10x.")
        for name in ("taker_fee_bps", "half_spread_bps", "slippage_bps"):
            if not math.isfinite(getattr(self, name)) or not 0 <= getattr(self, name) < 10000:
                raise ValueError(f"{name} harus finite, nonnegatif, dan kurang dari 10000 bps.")
        if self.half_spread_bps + self.slippage_bps >= 10000:
            raise ValueError("Gabungan spread dan slippage harus kurang dari 10000 bps.")


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    aliases = {column.lower(): column for column in frame.columns}
    time_name = aliases.get("time") or aliases.get("opentime")
    required = {name: aliases.get(name.lower()) for name in ("Open", "High", "Low", "Close", "Volume")}
    missing = [name for name, source in required.items() if source is None]
    if time_name is None or missing:
        raise ValueError(f"Dataset membutuhkan Time/OpenTime dan OHLCV. Kolom hilang: {missing}")

    selected = pd.DataFrame({"Time": pd.to_datetime(frame[time_name], errors="raise")})
    for target, source in required.items():
        selected[target] = pd.to_numeric(frame[source], errors="raise")
    validate_market_data(selected)
    return selected.reset_index(drop=True)


def validate_market_data(selected: pd.DataFrame) -> None:
    if len(selected) < 3:
        raise ValueError("Dataset membutuhkan minimal 3 candle.")
    invalid = (
        ~np.isfinite(selected[["Open", "High", "Low", "Close", "Volume"]]).all(axis=1)
        | (selected["Volume"] < 0)
        | (selected[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        | (selected["High"] < selected[["Open", "Close"]].max(axis=1))
        | (selected["Low"] > selected[["Open", "Close"]].min(axis=1))
    )
    if invalid.any():
        raise ValueError(f"Ditemukan {int(invalid.sum())} candle OHLC tidak valid.")
    times = pd.to_datetime(selected["Time"], errors="raise")
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("Timestamp harus valid, unik, dan berurutan; dataset tidak diperbaiki diam-diam.")


def validate_strategy_output(data: pd.DataFrame, output: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(output, pd.DataFrame):
        raise TypeError("generate_signals harus mengembalikan pandas.DataFrame.")
    if len(output) != len(data):
        raise ValueError("Jumlah bar output strategy harus sama dengan dataset input.")
    if not output.index.equals(data.index):
        raise ValueError("Index output strategy harus sama dan sejajar dengan input.")
    if "signal" not in output.columns:
        raise ValueError("Output strategy wajib memiliki kolom 'signal'.")

    result = output.copy().reset_index(drop=True)
    signal = pd.to_numeric(result["signal"], errors="coerce")
    if signal.isna().any() or not signal.isin([-1, 0, 1]).all():
        raise ValueError("signal hanya boleh berisi -1, 0, atau 1 tanpa NaN.")
    result["signal"] = signal.astype(int)

    if "size_pct" not in result:
        result["size_pct"] = 1.0
    size = pd.to_numeric(result["size_pct"], errors="coerce")
    if size.isna().any() or ((size < 0) | (size > 1)).any():
        raise ValueError("size_pct harus berada pada rentang 0 sampai 1.")
    result["size_pct"] = size.astype(float)

    if "leverage" not in result:
        result["leverage"] = np.nan
    leverage = pd.to_numeric(result["leverage"], errors="coerce")
    if (result["leverage"].notna() & leverage.isna()).any():
        raise ValueError("leverage harus numerik atau NaN untuk default.")
    if ((leverage.dropna() <= 0) | (leverage.dropna() > 10)).any():
        raise ValueError("leverage strategy harus lebih dari 0 dan maksimal 10.")
    result["leverage"] = leverage

    if "reason" not in result:
        result["reason"] = "signal"
    result["reason"] = result["reason"].fillna("signal").astype(str).str.slice(0, 120)
    return result


def _fill_price(open_price: float, side: int, config: EngineConfig) -> float:
    friction = (config.half_spread_bps + config.slippage_bps) / 10_000
    return open_price * (1 + friction * side)


def run_backtest(data: pd.DataFrame, strategy: pd.DataFrame, config: EngineConfig) -> dict[str, Any]:
    config.validate()
    validate_market_data(data)
    signals = validate_strategy_output(data, strategy)
    data = data.reset_index(drop=True)
    equity = config.initial_equity
    position = 0
    quantity = 0.0
    entry_price = 0.0
    entry_time: Any = None
    entry_fee = 0.0
    entry_reason = ""
    entry_index = 0
    entry_notional = 0.0
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    total_fees = 0.0

    for index, row in enumerate(data.itertuples(index=False)):
        bar = row._asdict()
        if index > 0:
            desired = int(signals.at[index - 1, "signal"])
            size_pct = float(signals.at[index - 1, "size_pct"])
            lev_value = signals.at[index - 1, "leverage"]
            leverage = config.default_leverage if pd.isna(lev_value) else float(lev_value)
            reason = str(signals.at[index - 1, "reason"])

            if size_pct == 0:
                desired = 0
            if desired != position:
                if position != 0:
                    exit_price = _fill_price(float(bar["Open"]), -position, config)
                    gross_pnl = quantity * (exit_price - entry_price) * position
                    exit_notional = abs(quantity * exit_price)
                    exit_fee = exit_notional * config.taker_fee_bps / 10_000
                    net_pnl = gross_pnl - entry_fee - exit_fee
                    equity += gross_pnl - exit_fee
                    total_fees += exit_fee
                    trades.append(
                        {
                            "entry_time": str(entry_time),
                            "exit_time": str(bar["Time"]),
                            "side": "LONG" if position == 1 else "SHORT",
                            "entry_price": round(entry_price, 10),
                            "exit_price": round(exit_price, 10),
                            "quantity": round(quantity, 10),
                            "entry_notional": round(entry_notional, 6),
                            "exit_notional": round(exit_notional, 6),
                            "holding_bars": index - entry_index,
                            "holding_hours": round((pd.Timestamp(bar["Time"]) - pd.Timestamp(entry_time)).total_seconds() / 3600, 4),
                            "gross_pnl": round(gross_pnl, 6),
                            "fees": round(entry_fee + exit_fee, 6),
                            "net_pnl": round(net_pnl, 6),
                            "return_pct": round(net_pnl / max(1e-12, equity - gross_pnl + exit_fee) * 100, 6),
                            "entry_reason": entry_reason,
                            "exit_reason": reason,
                        }
                    )
                    position = 0
                    quantity = 0.0
                    entry_price = 0.0
                    entry_fee = 0.0

                if desired != 0 and equity > 0 and size_pct > 0:
                    position = desired
                    entry_price = _fill_price(float(bar["Open"]), position, config)
                    margin = equity * size_pct
                    notional = margin * leverage
                    quantity = notional / entry_price
                    entry_fee = notional * config.taker_fee_bps / 10_000
                    equity -= entry_fee
                    total_fees += entry_fee
                    entry_time = bar["Time"]
                    entry_index = index
                    entry_notional = notional
                    entry_reason = reason

        unrealized = 0.0 if position == 0 else quantity * (float(bar["Close"]) - entry_price) * position
        marked_equity = equity + unrealized
        curve.append({"time": str(bar["Time"]), "equity": round(marked_equity, 6)})
        # ponytail: sub-cent equity is functionally depleted; without this floor the loop
        # appends zero-equity bars for the whole dataset and monthly returns become NaN.
        if marked_equity < 1e-6:
            break

    if position != 0:
        last = data.iloc[len(curve) - 1]
        exit_price = _fill_price(float(last["Close"]), -position, config)
        gross_pnl = quantity * (exit_price - entry_price) * position
        exit_fee = abs(quantity * exit_price) * config.taker_fee_bps / 10_000
        net_pnl = gross_pnl - entry_fee - exit_fee
        equity += gross_pnl - exit_fee
        total_fees += exit_fee
        trades.append(
            {
                "entry_time": str(entry_time), "exit_time": str(last["Time"]),
                "side": "LONG" if position == 1 else "SHORT", "entry_price": round(entry_price, 10),
                "exit_price": round(exit_price, 10), "quantity": round(quantity, 10),
                "entry_notional": round(entry_notional, 6),
                "exit_notional": round(abs(quantity * exit_price), 6),
                "holding_bars": len(curve) - 1 - entry_index,
                "holding_hours": round((pd.Timestamp(last["Time"]) - pd.Timestamp(entry_time)).total_seconds() / 3600, 4),
                "gross_pnl": round(gross_pnl, 6), "fees": round(entry_fee + exit_fee, 6),
                "net_pnl": round(net_pnl, 6), "return_pct": round(net_pnl / max(1e-12, equity - gross_pnl + exit_fee) * 100, 6),
                "entry_reason": entry_reason, "exit_reason": "end_of_data",
            }
        )
        curve[-1]["equity"] = round(equity, 6)

    analytics = analyze(curve, trades, config.initial_equity)
    opens = data["Open"].to_numpy(dtype=float)
    closes = data["Close"].to_numpy(dtype=float)
    # ponytail: positional lookup by stored bar index avoids Timestamp-string mismatch
    # (astype(str) drops H:M:S for day-resolution datetimes; str(Timestamp) keeps it).
    entry_time_to_index = {str(t): i for i, t in enumerate(data["Time"])}
    running_capital = config.initial_equity
    for trade in trades:
        entry_ref = float(opens[entry_time_to_index[trade["entry_time"]]])
        exit_idx = entry_time_to_index[trade["exit_time"]]
        exit_ref = float(closes[exit_idx] if trade["exit_reason"] == "end_of_data" else opens[exit_idx])
        reference_turnover = trade["quantity"] * (entry_ref + exit_ref)
        trade["spread_cost"] = round(reference_turnover * config.half_spread_bps / 10000, 6)
        trade["slippage_cost"] = round(reference_turnover * config.slippage_bps / 10000, 6)
        trade["price_pnl_before_costs"] = round(trade["gross_pnl"] + trade["spread_cost"] + trade["slippage_cost"], 6)
        trade["return_pct"] = round(trade["net_pnl"] / running_capital * 100, 6) if running_capital > 0 else None
        running_capital += trade["net_pnl"]
    analytics["metrics"]["total_fees"] = round(total_fees, 6)
    analytics["metrics"]["total_spread_cost"] = round(sum(t["spread_cost"] for t in trades), 6)
    analytics["metrics"]["total_slippage_cost"] = round(sum(t["slippage_cost"] for t in trades), 6)
    analytics["metrics"]["pnl_reconciliation_error"] = round(equity - config.initial_equity - sum(t["net_pnl"] for t in trades), 6)
    analytics["metrics"]["benchmark_return_pct"] = round((float(data.iloc[len(curve)-1]["Close"]) / float(data.iloc[0]["Close"]) - 1) * 100, 4)
    analytics["methodology"]["benchmark"] = "1x buy-and-hold contract close return, no fees/funding; descriptive only"
    intervals = pd.to_datetime(data["Time"]).diff().dropna()
    analytics["data_quality"] = {"rows": len(data), "zero_volume_bars": int((data["Volume"] == 0).sum()),
                                 "irregular_intervals": int((intervals != intervals.median()).sum()),
                                 "timezone": "as supplied; naive timestamps require source timezone verification"}
    analytics["warnings"].append("Funding/liquidation nonaktif: hasil belum mereplikasi kontrak futures riil.")
    return {**analytics, "equity": curve, "trades": trades}

def build_manifest(dataset: Path, strategy_code: str, config: EngineConfig, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "dataset": str(dataset.resolve()),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "strategy_sha256": hashlib.sha256(strategy_code.encode("utf-8")).hexdigest(),
        "config": asdict(config),
        "funding_mode": "excluded",
        "mark_price_mode": "contract_ohlc_proxy",
        "intrabar_mode": "not_implemented_signal_only",
        "warnings": [
            "Funding belum disertakan.",
            "Mark price memakai contract OHLC sebagai proxy.",
            "Target signal dieksekusi pada open candle berikutnya; perubahan size/leverage pada sisi yang sama tidak rebalance.",
            "Liquidation, maintenance margin, queue priority dan market impact belum dimodelkan.",
            "Python menerima seluruh data; pengguna wajib menghindari lookahead dan memvalidasi out-of-sample.",
        ],
    }


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
