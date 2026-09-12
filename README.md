# TradelogyLab

GUI desktop lokal untuk menjalankan strategy Python pada dataset OHLCV crypto perpetual futures, menganalisis hasil, menjalankan simulasi, dan mengekspor eksperimen ke vault Obsidian.

```text
strategy.py → target signal → next-bar execution → portfolio accounting → analytics → simulation → Obsidian
```

> Research MVP. Funding, liquidation berbasis mark price, stop/limit order, partial fill, dan intrabar simulation belum didukung.

## Menjalankan aplikasi

Persyaratan:

- Python 3.10+;
- `pandas`, `numpy`, `pytest`;
- dataset di `../Dataset`;
- vault Obsidian di `../Tradelogy`.

```powershell
python -m pip install -r requirements.txt
python app.py
```

## Memilih dataset

Tiga selector dependen di Strategy Lab: **Pair → Timeframe → Broker**. Setiap perubahan menyaring opsi di bawahnya. Kartu dataset menampilkan pair, timeframe, broker, dan ukuran file. Kombinasi tanpa dataset menampilkan peringatan.

## Panduan menulis strategy yang valid

### Kontrak aplikasi saat ini

Aplikasi **belum** memakai class `Strategy`, `Context`, atau `Decision` dari dokumen kontrak masa depan di vault. Jangan menghasilkan kode seperti:

```python
from tradelogy import Strategy, Context, Decision
```

Strategy valid = satu file Python yang mendefinisikan tepat:

```python
def generate_signals(data: pandas.DataFrame, params: dict) -> pandas.DataFrame:
    ...
```

### Satu output: kode Python saja

Strategy parameters **tidak wajib**. Semua parameter harus punya default via `params.get(key, default)`. Parameter form kosong → engine mengirim `{}` → semua default dipakai. Minta agent menghasilkan **satu code block Python saja**.

Contoh lengkap:

```python
import pandas as pd


def generate_signals(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    size_pct = float(params.get("size_pct", 0.10))
    leverage = float(params.get("leverage", 5))

    if fast < 2:
        raise ValueError("fast harus minimal 2")
    if slow <= fast:
        raise ValueError("slow harus lebih besar dari fast")
    if not 0 <= size_pct <= 1:
        raise ValueError("size_pct harus 0 sampai 1")
    if not 0 < leverage <= 10:
        raise ValueError("leverage harus lebih dari 0 dan maksimal 10")

    fast_ma = data["Close"].rolling(fast, min_periods=fast).mean()
    slow_ma = data["Close"].rolling(slow, min_periods=slow).mean()

    result = pd.DataFrame(index=data.index)
    result["signal"] = 0
    result.loc[fast_ma > slow_ma, "signal"] = 1
    result.loc[fast_ma < slow_ma, "signal"] = -1
    result["size_pct"] = size_pct
    result["leverage"] = leverage
    result["reason"] = "ema_regime"
    return result
```

### Override parameter (opsional)

Field **Strategy parameters** menerima `key = value` per baris — bukan JSON:

```text
fast = 10
slow = 30
size_pct = 0.2
```

- angka, string berkutip, `True`/`False` dikonversi otomatis;
- baris kosong dan `#` komentar diabaikan;
- kosongkan seluruhnya untuk memakai default strategy;
- JSON object yang ditempel tetap diterima sebagai fallback.

Tombol **Check strategy contract** melakukan preflight: syntax, keberadaan `generate_signals(data, params)`, dan jumlah argumen — sebelum worker berjalan.

## Input `data`

| Kolom | Isi |
|---|---|
| `Time` | timestamp candle |
| `Open` | harga open |
| `High` | harga high |
| `Low` | harga low |
| `Close` | harga close |
| `Volume` | base-asset volume |

Tidak tersedia: funding rate, mark/index price, open interest, liquidation feed, bid/ask, order book, taker-buy volume, trade count, data pair/timeframe lain.

## Output `DataFrame`

Panjang dan index **sama persis** dengan input. Gunakan `pd.DataFrame(index=data.index)`.

| Kolom | Wajib? | Nilai valid | Arti |
|---|---|---|---|
| `signal` | wajib | `-1`, `0`, `1` | target posisi short, flat, long |
| `size_pct` | opsional | `0.0`–`1.0` (default 1.0) | proporsi equity sebagai margin |
| `leverage` | opsional | `>0`, maks `10` | leverage posisi |
| `reason` | opsional | teks ≤120 char | alasan signal |

`signal` adalah **target posisi**: `1` pertahankan/buka long, `-1` short, `0` tutup dan flat. `1`→`-1` menutup long lalu membuka short pada fill berikutnya.

## Aturan timing dan look-ahead

Signal candle `t` dieksekusi pada `Open` candle `t+1` — entry dan exit, keduanya.

Valid:

```python
previous_high = data["High"].rolling(20).max().shift(1)
result.loc[data["Close"] > previous_high, "signal"] = 1
```

Tidak valid: `shift(-1)`, `center=True`, backfill masa depan, network call, random tanpa seed, menulis equity/fill/fee sendiri.

## Checklist sebelum Run

- [ ] `generate_signals(data, params)` ada, dua argumen.
- [ ] Return `pandas.DataFrame`, index sama dengan input.
- [ ] `signal` hanya `-1`/`0`/`1`, tanpa `NaN`.
- [ ] `size_pct` 0–1, `leverage` 0–10.
- [ ] Semua parameter punya default via `params.get()`.
- [ ] Tidak ada future leakage.
- [ ] Klik **Check strategy contract** — lulus.

## Prompt siap pakai untuk agent pembuat strategy

```text
Buat strategy Python yang valid untuk TradelogyLab engine v0.3.

WAJIB ikuti kontrak ini:
- Definisikan tepat: generate_signals(data: pandas.DataFrame, params: dict) -> pandas.DataFrame.
- Input hanya memiliki Time, Open, High, Low, Close, Volume.
- Output panjangnya sama dengan data dan memakai index yang sama.
- Output wajib memiliki signal bernilai hanya -1, 0, atau 1 tanpa NaN.
- Output juga isi size_pct 0..1, leverage >0 dan <=10, reason string.
- Signal adalah target posisi: 1 long, 0 flat, -1 short.
- Signal candle t dieksekusi pada Open candle t+1.
- Dilarang memakai future data, shift(-1), centered rolling, network call, data eksternal, funding, mark price, order book, open interest, atau kolom yang tidak tersedia.
- Semua parameter wajib dibaca dari params memakai params.get(key, default), memiliki default eksplisit, dikonversi tipenya, dan divalidasi.
- Jangan import atau memakai Strategy, Context, Decision, tradelogy package, backtesting framework, atau menjalankan engine sendiri.

Jawaban HANYA satu code block Python lengkap berjudul `Python Strategy`, langsung dapat ditempel tanpa edit. Tidak perlu membuat file parameter/JSON apa pun — parameter opsional ditangani lewat default di dalam kode.

Setelah code block, berikan maksimal lima bullet tentang logic, warm-up, risiko look-ahead, dan batas data.
```

## Model eksekusi dan biaya

```text
buy_fill  = next_open × (1 + half_spread_bps/10000 + slippage_bps/10000)
sell_fill = next_open × (1 − half_spread_bps/10000 − slippage_bps/10000)
fee       = abs(notional) × taker_fee_bps/10000   (per sisi)
```

Default: taker 5 bps, half-spread 1 bp, slippage 1 bp → **round-trip 14 bps of notional**.

### Batas biaya per timeframe — temuan crosscheck (ADAUSDT, EMA 20/50, leverage 5)

| TF | Trades | Return (biaya 0) | Return (biaya default) | Biaya vs gross |
|---|---:|---:|---:|---:|
| 1m | 27.357 | −94,9% | −100% | ~10× |
| 5m | 7.462 | **+19,7%** | −99,4% | ~7× |
| 15m | 2.373 | −34,8% | −87,6% | ~0,7× |
| 1h | 590 | **+36,4%** | −9,8% | ~3,7× |
| 4h | 154 | **+36,0%** | **+22,1%** | 0,6× |

Implikasi riset:

- Pergerakan rata-rata per bar 1m ≈ 8 bps < biaya round-trip 14 bps — setiap trade butuh move > 14 bps hanya untuk break even.
- Strategi di bawah 1h **wajib** punya gross edge per trade yang jauh di atas 14 bps, atau mengurangi frekuensi trade.
- 5m menarik: +19,7% gross menunjukkan ada sinyal; biaya yang membunuhnya. Arah riset yang sah: kurangi frekuensi atau cari maker fill (belum dimodelkan engine).
- Engine sudah diverifikasi bebas look-ahead: fill entry/exit selalu di Open bar berikutnya, PnL akurat terhadap perhitungan manual.

## Results dan simulation

| Tab | Isi |
|---|---|
| Overview | KPI, equity curve dan drawdown berdampingan dengan label tanggal |
| Risk | Drawdown, underwater duration, VaR 95%, Expected Shortfall |
| Trades | Sortable trade ledger (500 terakhir; lengkap di CSV) |
| Distribution | PnL histogram, skewness, kurtosis, win/loss streak |
| Costs | Fee/spread/slippage attribution, PnL reconciliation |
| Monthly returns | Heatmap return bulanan |
| Trade timing & factors | Heatmap Senin–Jumat × 4 jam, cohort side/reason, export CSV |
| Rolling risk | Rolling Sharpe 90 observasi harian |
| Simulation | Monte Carlo: bootstrap, permutation, block bootstrap — fan P05–P95 |
| Methodology / Audit | Rumus, data quality, warnings |

Simulation mengukur path uncertainty dari trade ledger — bukan bukti edge, statistical significance, atau out-of-sample performance.

## Batas engine yang diketahui

- Account depletion: equity < 1e-6 USDT menghentikan simulasi; hasil di bawah itu adalah account habis, bukan NaN.
- Monthly/annual return setelah depletion bernilai `null`, bukan NaN.
- Biaya spread+slippage dihitung terpisah di ledger, tapi fill price tetap menggabungkan keduanya.
- Worker timeout 30 menit; strategy dengan `rolling().apply()` lambat di dataset 1m (~2 menit untuk 1,58 juta bar).
- Funding, mark-price liquidation, maintenance margin, limit/stop order, partial fill belum dimodelkan.
- Regimes dan Robustness tabs ditandai belum tersedia.

## Test

```powershell
pytest -q
```

28 test: next-bar execution, fee accounting, depletion, NaN safety, daily risk formulas, cost reconciliation, params parsing, GUI navigation, simulation reproducibility.

## Struktur project

```text
TradelogyLab/
├── app.py          GUI desktop (Tkinter)
├── engine.py       Validasi data, backtest accounting, cost attribution
├── analytics.py    Metrics institusional, drawdown, heatmap data
├── simulation.py   Monte Carlo (bootstrap/permutation/block)
├── research.py     Trade cohorts (weekday × jam, side, reason)
├── catalog.py      Dataset discovery (pair/timeframe/broker)
├── export.py       Obsidian export
├── worker.py       Subprocess eksekusi strategy
├── examples/
├── tests/
└── runs/           Artefak backtest lokal
```
