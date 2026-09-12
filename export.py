from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


def export_to_obsidian(
    run_dir: Path,
    vault_root: Path,
    title: str,
    hypothesis: str,
    interpretation: str,
    status: str,
) -> Path:
    allowed = {"observation", "revise", "validate-further", "candidate", "rejected"}
    if status not in allowed:
        raise ValueError(f"Status export tidak valid: {status}")
    title = title.strip()[:100] or f"Experiment {run_dir.name}"
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = result["metrics"]
    strategy_name = re.sub(r"[^0-9A-Za-z_-]", "-", title).strip("-") or "strategy"
    target_dir = vault_root / "40 Research" / "Experiments" / strategy_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{datetime.now():%Y-%m-%d}-{run_dir.name}.md"
    if target.exists():
        target = target.with_stem(target.stem + f"-{datetime.now():%H%M%S%f}")
    warnings = "\n".join(f"- {item}" for item in manifest.get("warnings", []) + result.get("warnings", []))
    def metric(name, digits=4):
        value = metrics.get(name)
        return f"{value:.{digits}f}" if value is not None else "N/A"
    pf = metrics.get("profit_factor")
    pf_str = f"{pf:.4f}" if pf is not None else "—"
    content = f'''---
type: experiment
status: {status}
run_id: {run_dir.name}
created: {datetime.now():%Y-%m-%d}
tags: [quant, backtest, experiment]
---

# {title}

## Hipotesis

{hypothesis or "Belum diisi."}

## Interpretasi

{interpretation or "Belum diisi."}

## Hasil utama

| Metric | Value |
|---|---:|
| Initial equity | {metrics['initial_equity']:.2f} USDT |
| Final equity | {metrics['final_equity']:.2f} USDT |
| Net PnL | {metrics['net_pnl']:.2f} USDT |
| Return | {metrics['return_pct']:.2f}% |
| CAGR | {metric('cagr_pct', 2)}% |
| Max drawdown | {metrics['max_drawdown_pct']:.2f}% |
| Sharpe | {metric('sharpe')} |
| Sortino | {metric('sortino')} |
| Calmar | {metric('calmar')} |
| Volatility | {metrics.get('annual_volatility_pct', 0):.2f}% |
| Trades | {metrics['trades']} |
| Win rate | {metrics['win_rate_pct']:.2f}% |
| Profit factor | {pf_str} |
| Expectancy | {metrics['expectancy']:.4f} USDT |
| Total fees | {metrics.get('total_fees', 0):.2f} USDT |

## Konfigurasi

- Dataset: `{manifest['dataset']}`
- Engine: `{manifest['engine_version']}`
- Initial equity: 1.000 USDT
- Maximum leverage: 10x
- Execution: `{json.dumps(manifest['config'], ensure_ascii=False)}`

## Warning dan keterbatasan

{warnings or "- Tidak ada warning."}

## Artefak

- Run ID: `{run_dir.name}`
- Result: `{(run_dir / 'result.json').as_posix()}`
- Trades: `{(run_dir / 'trades.csv').as_posix()}`
- Equity: `{(run_dir / 'equity.csv').as_posix()}`

## Hubungan

- [[Spesifikasi Research Workbench]]
- [[Model Biaya dan Eksekusi Binance Futures]]
- [[Proses Riset Strategy]]

## Next experiment

- [ ] Tentukan pengujian berikutnya.
'''
    target.write_text(content, encoding="utf-8")
    return target
