"""Descriptive trade cohorts. Attribution is whole-trade PnL, never daily MTM."""
import math
from collections import defaultdict
from datetime import datetime, timedelta

DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]


def summary(trades):
    pnls = [float(t["net_pnl"]) for t in trades]
    n = len(pnls)
    wins = sum(p > 0 for p in pnls)
    profit, loss = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    return dict(trades=n, net_pnl=round(sum(pnls), 6), expectancy=round(sum(pnls)/n, 6) if n else None,
                win_rate_pct=round(100*wins/n, 2) if n else None,
                profit_factor=round(profit/loss, 4) if loss else None,
                average_holding_hours=round(sum(float(t.get("holding_hours", 0)) for t in trades)/n, 4) if n else None)


def trade_cohorts(trades, basis="entry", shift_hours=0, side="All", weekends=False, minimum=20):
    if basis not in {"entry", "exit"} or side not in {"All", "LONG", "SHORT"}:
        raise ValueError("Basis atau side tidak valid.")
    if not math.isfinite(shift_hours) or not -24 <= shift_hours <= 24 or minimum < 1:
        raise ValueError("Geser jam harus -24..24; minimum sample harus positif.")
    groups, cells, reasons, sides = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    excluded, invalid = 0, 0
    for trade in trades:
        if side != "All" and trade.get("side") != side:
            continue
        try:
            stamp = datetime.fromisoformat(str(trade[f"{basis}_time"]).replace("Z", "+00:00")) + timedelta(hours=shift_hours)
            if not math.isfinite(float(trade["net_pnl"])):
                raise ValueError("nonfinite pnl")
        except (ValueError, TypeError, KeyError):
            invalid += 1
            continue
        day = stamp.weekday()
        if not weekends and day > 4:
            excluded += 1
            continue
        groups[day].append(trade)
        cells[day, stamp.hour//4].append(trade)
        reasons[str(trade.get("entry_reason", "unknown"))].append(trade)
        sides[str(trade.get("side", "unknown"))].append(trade)
    def row(label, values):
        return dict(label=label, **summary(values), sample_status="cukup untuk eksplorasi" if len(values) >= minimum else "sampel kecil")
    days = range(7 if weekends else 5)
    return dict(basis=basis, shift_hours=shift_hours, minimum=minimum, side=side, weekends=weekends,
                excluded_weekend_trades=excluded, invalid_trades=invalid,
                weekdays=[dict(day=d, **row(DAYS[d], groups[d])) for d in days],
                cells=[dict(day=d, bucket=b, **row(f"{DAYS[d]} {b*4:02d}:00", cells[d,b])) for d in days for b in range(6)],
                factors=[dict(factor="Side", **row(k, v)) for k,v in sorted(sides.items())]
                        + [dict(factor="Entry reason", **row(k,v)) for k,v in sorted(reasons.items())],
                methodology="Whole-trade net PnL grouped by entry/exit clock plus explicit hour shift; not daily mark-to-market. Exploratory associations, not causal edge.")
