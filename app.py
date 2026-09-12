"""TradelogyLab — Institutional-grade desktop research workbench for crypto futures backtesting."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tkinter as tk
import uuid
import traceback
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

from engine import EngineConfig, write_json
from export import export_to_obsidian
from simulation import run_simulation
from catalog import discover, select, timeframe_key
from research import trade_cohorts

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
DATASET_ROOT = PROJECT_ROOT / "Dataset"
VAULT_ROOT = PROJECT_ROOT / "Tradelogy"
RUNS_ROOT = APP_ROOT / "runs"

# --- Palette ---
BG = "#FFFFFF"
SURFACE = "#F4F5F2"
BORDER = "#D8DDD8"
TEXT = "#252C29"
TEXT2 = "#58645D"
GOOD = "#27634B"
BAD = "#A54038"
ACCENT = "#264E3F"  # Deep forest green — institutional accent.
NAVY = "#252C29"

EXAMPLE = '''import pandas as pd

def generate_signals(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    if fast < 2 or slow <= fast:
        raise ValueError("Gunakan slow > fast >= 2")
    result = pd.DataFrame(index=data.index)
    fast_ma = data["Close"].rolling(fast).mean()
    slow_ma = data["Close"].rolling(slow).mean()
    result["signal"] = 0
    result.loc[fast_ma > slow_ma, "signal"] = 1
    result.loc[fast_ma < slow_ma, "signal"] = -1
    result["size_pct"] = float(params.get("size_pct", 0.10))
    result["leverage"] = float(params.get("leverage", 5))
    result["reason"] = "ema_regime"
    return result
'''


def list_datasets() -> list[dict[str, Any]]:
    return discover(DATASET_ROOT)


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def parse_params(text: str) -> dict[str, Any]:
    """Parse 'key = value' lines (ast.literal_eval per value); accept a JSON object fallback. Empty -> {}."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("Parameters JSON harus object.")
        return value
    params: dict[str, Any] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Baris {number}: format harus 'key = value'.")
        key, raw = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Baris {number}: key kosong.")
        try:
            params[key] = ast.literal_eval(raw.strip())
        except (ValueError, SyntaxError):
            params[key] = raw.strip()  # bare string fallback
    return params


# ─── Canvas chart helpers ───

def draw_line_chart(canvas: tk.Canvas, values: list[float], width: int, height: int,
                    color: str = ACCENT, fill_below: str = "", labels=None) -> None:
    canvas.delete("all")
    if len(values) < 2:
        canvas.create_text(width // 2, height // 2, text="Tidak cukup data", fill=TEXT2, font=("Segoe UI", 10))
        return
    pad_l, pad_r, pad_t, pad_b = 55, 12, 12, 24
    pw = width - pad_l - pad_r
    ph = height - pad_t - pad_b
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    points = []
    step = max(1, len(values) // max(1, pw // 2))
    indices = {0, len(values) - 1}
    for start in range(0, len(values), step):
        stop = min(len(values), start + step)
        indices.add(min(range(start, stop), key=values.__getitem__))
        indices.add(max(range(start, stop), key=values.__getitem__))
    for i in sorted(indices):
        v = values[i]
        x = pad_l + i / max(1, len(values) - 1) * pw
        y = pad_t + (hi - v) / span * ph
        points.append((x, y))
    # grid
    for j in range(5):
        gy = pad_t + j * ph / 4
        canvas.create_line(pad_l, gy, width - pad_r, gy, fill=BORDER, dash=(2, 4))
        label_val = hi - j * span / 4
        canvas.create_text(pad_l - 4, gy, text=fmt(label_val), anchor="e", fill=TEXT2, font=("Consolas", 8))
    if fill_below and len(points) > 1:
        fill_pts = list(points) + [(points[-1][0], pad_t + ph), (points[0][0], pad_t + ph)]
        canvas.create_polygon(fill_pts, fill=fill_below, outline="")
    if len(points) > 1:
        canvas.create_line(points, fill=color, width=1.5, smooth=False)
    if labels:
        canvas.create_text(pad_l, height-5, text=str(labels[0])[:10], anchor="w", fill=TEXT2, font=("Consolas", 8))
        canvas.create_text(width-pad_r, height-5, text=str(labels[-1])[:10], anchor="e", fill=TEXT2, font=("Consolas", 8))


def draw_fan(canvas, output):
    canvas.delete("all")
    bands = output["path_percentiles"]
    width, height = max(260, canvas.winfo_width()), max(180, canvas.winfo_height())
    low, high = min(bands["p05"]), max(bands["p95"])
    span = high-low or 1
    n = len(bands["p50"])
    indices = sorted(set(range(0, n, max(1, n//500))) | {n-1})
    def points(key):
        return [(65+i/max(1,n-1)*(width-85), 30+(high-bands[key][i])/span*(height-70)) for i in indices]
    canvas.create_polygon(points("p05")+list(reversed(points("p95"))), fill="#DEE9E1", outline="")
    for key, color, dash in [("p05", TEXT2, (3,3)), ("p95", TEXT2, (3,3)), ("p50", ACCENT, ())]:
        canvas.create_line(points(key), fill=color, width=2 if key == "p50" else 1, dash=dash)
    for j in range(5):
        y=30+j*(height-70)/4
        canvas.create_text(59,y,anchor="e",text=fmt(high-j*span/4,0),fill=TEXT2,font=("Consolas",8))
    canvas.create_text(65,12,anchor="w",text="Equity USDT  ·  median P50  ·  shaded P05–P95",fill=TEXT2,font=("Segoe UI",9))
    canvas.create_text(65,height-12,anchor="w",text="0",fill=TEXT2)
    canvas.create_text(width-20,height-12,anchor="e",text=f"{n-1:,} completed trades",fill=TEXT2)


def draw_histogram(canvas: tk.Canvas, values: list[float], width: int, height: int) -> None:
    canvas.delete("all")
    if not values:
        canvas.create_text(width // 2, height // 2, text="Tidak ada data", fill=TEXT2, font=("Segoe UI", 10))
        return
    pad_l, pad_r, pad_t, pad_b = 45, 12, 12, 24
    pw = width - pad_l - pad_r
    ph = height - pad_t - pad_b
    bins = min(24, max(4, len(values) // 5))
    lo, hi = min(values), max(values)
    rng = hi - lo or 1.0
    counts = [0] * bins
    for v in values:
        idx = min(bins - 1, int((v - lo) / rng * bins))
        counts[idx] += 1
    peak = max(counts) or 1
    bw = pw / bins
    for i, c in enumerate(counts):
        mid = lo + (i + 0.5) * rng / bins
        color = GOOD if mid >= 0 else BAD
        bh = c / peak * ph
        x0 = pad_l + i * bw + 1
        x1 = x0 + bw - 2
        canvas.create_rectangle(x0, pad_t + ph - bh, x1, pad_t + ph, fill=color, outline=BG)
    canvas.create_text(pad_l, height - 4, text=fmt(lo), anchor="w", fill=TEXT2, font=("Consolas", 8))
    canvas.create_text(width - pad_r, height - 4, text=fmt(hi), anchor="e", fill=TEXT2, font=("Consolas", 8))


def draw_heatmap(canvas: tk.Canvas, monthly: list[dict], width: int, height: int) -> None:
    canvas.delete("all")
    if not monthly:
        canvas.create_text(width // 2, height // 2, text="Tidak cukup data untuk heatmap", fill=TEXT2, font=("Segoe UI", 10))
        return
    years = sorted(set(m["year"] for m in monthly))
    pad_l, pad_t = 55, 24
    cell_w = max(28, (width - pad_l - 10) // 12)
    cell_h = max(22, (height - pad_t - 10) // max(1, len(years)))
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for j, label in enumerate(months):
        canvas.create_text(pad_l + j * cell_w + cell_w // 2, pad_t - 8, text=label, fill=TEXT2, font=("Consolas", 8))
    lookup = {(m["year"], m["month"]): m["return_pct"] for m in monthly}
    vals = [abs(m["return_pct"]) for m in monthly if m["return_pct"] != 0]
    max_abs = max(vals) if vals else 1.0
    for i, year in enumerate(years):
        canvas.create_text(pad_l - 6, pad_t + i * cell_h + cell_h // 2, text=str(year), anchor="e", fill=TEXT, font=("Consolas", 9))
        for j in range(12):
            x0 = pad_l + j * cell_w
            y0 = pad_t + i * cell_h
            val = lookup.get((year, j + 1))
            if val is None:
                canvas.create_rectangle(x0, y0, x0 + cell_w - 1, y0 + cell_h - 1, fill=SURFACE, outline=BG)
            else:
                intensity = min(1.0, abs(val) / max_abs) if max_abs else 0.0
                if val >= 0:
                    r, g, b = 25, 135, 84
                else:
                    r, g, b = 220, 53, 69
                fr = int(248 + (r - 248) * intensity)
                fg = int(249 + (g - 249) * intensity)
                fb = int(250 + (b - 250) * intensity)
                color = f"#{fr:02x}{fg:02x}{fb:02x}"
                canvas.create_rectangle(x0, y0, x0 + cell_w - 1, y0 + cell_h - 1, fill=color, outline=BG)
                txt_color = BG if intensity > 0.6 else TEXT
                canvas.create_text(x0 + cell_w // 2, y0 + cell_h // 2, text=f"{val:.1f}%", fill=txt_color, font=("Consolas", 7))


# ─── Main application ───

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TradelogyLab — Research Workbench")
        self.geometry("1360x900")
        self.configure(bg=BG)
        self.minsize(1040, 740)
        self.datasets = list_datasets()
        self.run_data: dict[str, Any] | None = None
        self.run_dir: Path | None = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._busy = False
        self._render_job = None
        self._rendering = False
        self._rendered_run = None
        self._build_style()
        self._build_sidebar()
        self._build_pages()
        self.bind_all("<Control-Return>", lambda e: self._run_backtest())
        self._show_page("lab")
        self.bind("<Configure>", lambda e: self._schedule_result_render() if e.widget is self else None)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self):
        process = getattr(self, "_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    def report_callback_exception(self, exc, value, tb):
        RUNS_ROOT.mkdir(exist_ok=True)
        with (RUNS_ROOT / "gui_errors.log").open("a", encoding="utf-8") as stream:
            stream.write("".join(traceback.format_exception(exc, value, tb)))
        self._diag(f"GUI error: {value}. Detail: runs/gui_errors.log", error=True)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TLabelframe", background=BG, foreground=TEXT)
        style.configure("TLabelframe.Label", background=BG, foreground=NAVY, font=("Segoe UI Semibold", 10))
        style.configure("TButton", padding=(12, 6))
        style.configure("Primary.TButton", background=ACCENT, foreground=BG, font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("disabled", BORDER), ("active", "#183C2E")], foreground=[("disabled", TEXT2)])
        style.configure("Nav.TButton", background=SURFACE, foreground=TEXT2, font=("Segoe UI", 10), padding=(14, 10), anchor="w")
        style.map("Nav.TButton", background=[("active", BORDER)])
        style.configure("NavActive.TButton", background=ACCENT, foreground=BG, font=("Segoe UI Semibold", 10), padding=(14, 10), anchor="w")
        style.configure("Metric.TLabel", font=("Consolas", 13, "bold"), foreground=TEXT)
        style.configure("MetricName.TLabel", font=("Segoe UI", 9), foreground=TEXT2)
        style.configure("Good.TLabel", foreground=GOOD, font=("Consolas", 13, "bold"))
        style.configure("Bad.TLabel", foreground=BAD, font=("Consolas", 13, "bold"))
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 16), foreground=NAVY)
        style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground=TEXT2)
        style.configure("Treeview", rowheight=24, font=("Consolas", 9))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", BG)])
        style.map("TCombobox", fieldbackground=[("readonly", BG)], selectbackground=[("readonly", ACCENT)])
        style.configure("TNotebook", borderwidth=0)
        style.layout("Results.TNotebook.Tab", [])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE)

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg="#F1F3F5", width=200, bd=0, highlightthickness=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg="#F1F3F5")
        brand.pack(fill="x", padx=14, pady=(18, 20))
        tk.Label(brand, text="TL", bg=ACCENT, fg=BG, font=("Consolas", 11, "bold"), width=3, height=1).pack(side="left")
        bf = tk.Frame(brand, bg="#F1F3F5")
        bf.pack(side="left", padx=(8, 0))
        tk.Label(bf, text="TradelogyLab", bg="#F1F3F5", fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(bf, text="Research Workbench", bg="#F1F3F5", fg=TEXT2, font=("Segoe UI", 8)).pack(anchor="w")
        self._nav_buttons: dict[str, ttk.Button] = {}
        for key, label in [("lab", "Strategy Lab"), ("results", "Results"), ("data", "Data Catalog"), ("history", "Run History")]:
            btn = ttk.Button(sidebar, text=f"  {label}", style="Nav.TButton", command=lambda k=key: self._show_page(k))
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_buttons[key] = btn
        tk.Frame(sidebar, bg="#F1F3F5").pack(fill="both", expand=True)
        self._status_label = tk.Label(sidebar, text="● Engine ready", bg="#F1F3F5", fg=GOOD, font=("Segoe UI", 8), anchor="w")
        self._status_label.pack(fill="x", padx=14, pady=(0, 12))

    def _build_pages(self) -> None:
        self._container = tk.Frame(self, bg=BG)
        self._container.pack(side="left", fill="both", expand=True)
        self._pages: dict[str, tk.Frame] = {}
        self._build_lab_page()
        self._build_results_page()
        self._build_data_page()
        self._build_history_page()

    def _show_page(self, name: str) -> None:
        for key, btn in self._nav_buttons.items():
            btn.configure(style="NavActive.TButton" if key == name else "Nav.TButton")
        for page in self._pages.values():
            page.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        if name == "results":
            self._schedule_result_render()
        if name == "history":
            self._refresh_history()

    # ─── Strategy Lab ───

    def _build_lab_page(self) -> None:
        page = tk.Frame(self._container, bg=BG)
        self._pages["lab"] = page
        # header
        hdr = tk.Frame(page, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(16, 8))
        ttk.Label(hdr, text="LOCAL RESEARCH ENVIRONMENT", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(hdr, text="Strategy Lab", style="Header.TLabel").pack(anchor="w")
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        # left: config
        left = ttk.LabelFrame(body, text="01 / INPUT — Run configuration", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for label, key in [("Pair", "pair"), ("Timeframe", "timeframe"), ("Broker / exchange", "broker")]:
            ttk.Label(left, text=label).pack(anchor="w", pady=(4, 2))
            variable = tk.StringVar()
            combo = ttk.Combobox(left, textvariable=variable, state="readonly", width=29)
            setattr(self, f"_{key}_var", variable)
            setattr(self, f"_{key}_combo", combo)
            combo.pack(fill="x")
            combo.bind("<<ComboboxSelected>>", lambda e: self._sync_catalog())
        self._dataset_info = tk.Label(left, bg=SURFACE, fg=TEXT2, font=("Segoe UI", 9), justify="left", anchor="w", wraplength=265, padx=8, pady=8)
        self._dataset_info.pack(fill="x", pady=8)
        self._sync_catalog()
        row1 = tk.Frame(left, bg=BG)
        row1.pack(fill="x", pady=2)
        for col, (label, var_name, default) in enumerate([
            ("Default leverage", "_leverage_var", "10"),
            ("Taker fee (bps)", "_fee_var", "5"),
        ]):
            f = tk.Frame(row1, bg=BG)
            f.pack(side="left", expand=True, fill="x", padx=(0, 4))
            ttk.Label(f, text=label).pack(anchor="w")
            v = tk.StringVar(value=default)
            setattr(self, var_name, v)
            ttk.Entry(f, textvariable=v, width=10).pack(fill="x")
        row2 = tk.Frame(left, bg=BG)
        row2.pack(fill="x", pady=2)
        for col, (label, var_name, default) in enumerate([
            ("Half-spread (bps)", "_spread_var", "1"),
            ("Slippage (bps)", "_slip_var", "1"),
        ]):
            f = tk.Frame(row2, bg=BG)
            f.pack(side="left", expand=True, fill="x", padx=(0, 4))
            ttk.Label(f, text=label).pack(anchor="w")
            v = tk.StringVar(value=default)
            setattr(self, var_name, v)
            ttk.Entry(f, textvariable=v, width=10).pack(fill="x")
        ttk.Label(left, text="Strategy parameters (opsional — key = value per baris)").pack(anchor="w", pady=(8, 2))
        self._params_text = tk.Text(left, height=5, font=("Consolas", 10), bg=SURFACE, bd=1, relief="solid",
                                     highlightthickness=0, wrap="word")
        ttk.Button(left, text="Use strategy defaults", command=lambda: self._params_text.delete("1.0", "end")).pack(anchor="w", pady=(0, 4))
        self._params_text.pack(fill="x", pady=(0, 8))
        # preflight
        ttk.Label(left, text="Capital 1,000 USDT  /  Max 10×\nFunding & liquidation belum dimodelkan.", style="Sub.TLabel").pack(anchor="w", pady=4)
        self._run_button = ttk.Button(left, text="Run Backtest    Ctrl ↵", style="Primary.TButton", command=self._run_backtest)
        self._run_button.pack(fill="x", pady=(8, 4))
        ttk.Button(left, text="Cancel worker", command=self._cancel_worker).pack(fill="x")
        self._progress = ttk.Progressbar(left, mode="indeterminate")
        self._progress.pack(fill="x", pady=4)
        self._diag_label = tk.Label(left, text="Ready. Kode belum dijalankan.", bg=BG, fg=TEXT2,
                                     font=("Consolas", 9), anchor="w", wraplength=280)
        self._diag_label.pack(fill="x")
        # center: code editor
        mid = ttk.LabelFrame(body, text="02 / STRATEGY — Python editor", padding=0)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, 0))
        btn_frame = tk.Frame(mid, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Button(btn_frame, text="Reset example", command=self._reset_example).pack(side="right")
        ttk.Button(btn_frame, text="Check strategy contract", command=self._check_contract).pack(side="left")
        ttk.Button(btn_frame, text="Open .py", command=self._load_code).pack(side="left")
        ttk.Button(btn_frame, text="Save .py", command=self._save_code).pack(side="left", padx=4)
        self._code_text = scrolledtext.ScrolledText(mid, font=("Consolas", 10), bg="#FAFBFC", fg=TEXT,
                                                      insertbackground=TEXT, bd=0, highlightthickness=0,
                                                      wrap="none", undo=True, padx=12, pady=8)
        self._code_text.insert("1.0", EXAMPLE)
        self._code_text.pack(fill="both", expand=True, padx=1, pady=(4, 1))

    def _reset_example(self) -> None:
        self._code_text.delete("1.0", "end")
        self._code_text.insert("1.0", EXAMPLE)

    def _check_contract(self) -> None:
        """Preflight: syntax + generate_signals presence, before spending a worker run."""
        code = self._code_text.get("1.0", "end").strip()
        if len(code) < 20:
            self._diag("Kode strategy terlalu pendek.", error=True)
            return
        try:
            compiled = compile(code, "strategy.py", "exec")
        except SyntaxError as exc:
            self._diag(f"Syntax error baris {exc.lineno}: {exc.msg}", error=True)
            return
        namespace: dict[str, Any] = {}
        try:
            exec(compiled, namespace)  # ponytail: exec-only preflight; full run stays in subprocess
        except Exception as exc:
            self._diag(f"Import/definisi gagal: {exc}", error=True)
            return
        function = namespace.get("generate_signals")
        if not callable(function):
            self._diag("Strategy wajib mendefinisikan generate_signals(data, params).", error=True)
            return
        import inspect
        try:
            signature = inspect.signature(function)
            if len(signature.parameters) != 2:
                self._diag("generate_signals harus menerima tepat dua argumen: (data, params).", error=True)
                return
        except (TypeError, ValueError):
            pass
        self._diag("Kontrak valid: generate_signals(data, params) ditemukan. Output wajib DataFrame dengan kolom signal (-1/0/1).", success=True)

    def _load_code(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Python", "*.py")])
        if path:
            try:
                code = Path(path).read_text(encoding="utf-8")
                self._code_text.delete("1.0", "end")
                self._code_text.insert("1.0", code)
            except Exception as exc:
                self._diag(str(exc), error=True)

    def _save_code(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".py", filetypes=[("Python", "*.py")])
        if path:
            try:
                Path(path).write_text(self._code_text.get("1.0", "end-1c"), encoding="utf-8")
                self._diag(f"Strategy tersimpan: {Path(path).name}", success=True)
            except Exception as exc:
                self._diag(str(exc), error=True)

    def _sync_catalog(self):
        subset = self.datasets
        for key in ("pair", "timeframe", "broker"):
            options = sorted({d[key] for d in subset}, key=timeframe_key if key == "timeframe" else None)
            variable, combo = getattr(self, f"_{key}_var"), getattr(self, f"_{key}_combo")
            combo.configure(values=options)
            if variable.get() not in options:
                variable.set(options[0] if options else "")
            subset = [d for d in subset if d[key] == variable.get()]
        if len(subset) == 1:
            self._selected_dataset = subset[0]
            d = subset[0]
            self._dataset_info.configure(text=f"{d['pair']} · Perpetual · {d['timeframe']} · {d['broker']}\n{d['size_mb']:,.1f} MB\n{d['name']}")
        else:
            self._selected_dataset = None
            self._dataset_info.configure(text="Tidak ada dataset yang cocok.")

    # ─── Results ───

    def _build_results_page(self) -> None:
        page = tk.Frame(self._container, bg=BG)
        self._pages["results"] = page
        self._empty_frame = tk.Frame(page, bg=BG)
        self._empty_frame.pack(fill="both", expand=True)
        tk.Label(self._empty_frame, text="TL", fg=ACCENT, bg=BG, font=("Consolas", 36, "bold")).pack(pady=(80, 8))
        tk.Label(self._empty_frame, text="Belum ada hasil", fg=TEXT, bg=BG, font=("Segoe UI Semibold", 16)).pack()
        tk.Label(self._empty_frame, text="Jalankan strategy dari Strategy Lab untuk membuka analytics.",
                 fg=TEXT2, bg=BG, font=("Segoe UI", 10)).pack(pady=(4, 0))
        self._result_frame = tk.Frame(page, bg=BG)
        # header
        self._res_header = tk.Frame(self._result_frame, bg=BG)
        self._res_header.pack(fill="x", padx=24, pady=(12, 4))
        self._run_id_label = ttk.Label(self._res_header, text="", style="Sub.TLabel")
        self._run_id_label.pack(side="left")
        ttk.Button(self._res_header, text="Export to Obsidian", style="Primary.TButton",
                   command=self._open_export).pack(side="right")
        # notebook
        nav = tk.Frame(self._result_frame, bg=BG)
        nav.pack(fill="x", padx=24, pady=(4, 12))
        ttk.Label(nav, text="Analisis").pack(side="left", padx=(0, 8))
        self._view_var = tk.StringVar()
        self._view_combo = ttk.Combobox(nav, textvariable=self._view_var, state="readonly", width=28)
        self._view_combo.pack(side="left")
        self._view_combo.bind("<<ComboboxSelected>>", lambda e: self._notebook.select(self._view_combo.current()))
        self._result_context = ttk.Label(nav, text="", style="Sub.TLabel")
        self._result_context.pack(side="right")
        self._notebook = ttk.Notebook(self._result_frame, style="Results.TNotebook")
        self._notebook.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self._notebook.bind("<<NotebookTabChanged>>", self._result_tab_changed)
        self._build_overview_tab()
        self._build_risk_tab()
        self._build_trades_tab()
        self._build_distribution_tab()
        self._build_costs_tab()
        self._build_heatmap_tab()
        self._build_timing_tab()
        self._build_simulation_tab()
        rolling_tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(rolling_tab, text="  Rolling risk  ")
        tk.Label(rolling_tab, text="90-observation daily Sharpe · risk-free 0 · 365.25 days/year", bg=BG, fg=TEXT2).pack(pady=12)
        self._rolling_canvas = tk.Canvas(rolling_tab, bg=BG, highlightthickness=0, height=240)
        self._rolling_canvas.pack(fill="x", padx=16)
        self._audit_text = scrolledtext.ScrolledText(self._notebook, bg=BG, fg=TEXT, font=("Consolas", 10), wrap="word")
        self._notebook.add(self._audit_text, text="  Methodology / Audit  ")
        self._view_combo.configure(values=[self._notebook.tab(t, "text").strip() for t in self._notebook.tabs()])
        self._view_combo.current(0)

    def _result_tab_changed(self, event=None):
        if self._notebook.select():
            self._view_combo.current(self._notebook.index("current"))
        self._schedule_result_render()

    def _build_overview_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Overview  ")
        # Scroll the overview so KPI cards remain reachable on smaller displays.
        outer = tk.Canvas(tab, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(tab, orient="vertical", command=outer.yview)
        scroll.pack(side="right", fill="y")
        outer.pack(fill="both", expand=True)
        outer.configure(yscrollcommand=scroll.set)
        body = tk.Frame(outer, bg=BG)
        item = outer.create_window(0, 0, window=body, anchor="nw")
        body.bind("<Configure>", lambda e: outer.configure(scrollregion=outer.bbox("all")))
        outer.bind("<Configure>", lambda e: outer.itemconfigure(item, width=e.width))
        charts = tk.Frame(body, bg=BG)
        charts.pack(fill="x", padx=12, pady=8)
        charts.columnconfigure((0, 1), weight=1, uniform="charts")
        for col, title, attr in [(0, "Equity curve  /  USDT", "_equity_canvas"), (1, "Drawdown  /  %", "_overview_dd")]:
            card = ttk.LabelFrame(charts, text=title, padding=8)
            card.grid(row=0, column=col, sticky="nsew", padx=4)
            canvas = tk.Canvas(card, bg=BG, height=240, highlightthickness=0)
            canvas.pack(fill="x")
            setattr(self, attr, canvas)
        self._period_label = ttk.Label(body, style="Sub.TLabel")
        self._period_label.pack(anchor="w", padx=20, pady=(0, 8))
        self._kpi_frame = tk.Frame(body, bg=BG)
        self._kpi_frame.pack(fill="x", padx=16, pady=(0, 8))

    def _build_risk_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Risk  ")
        self._dd_canvas = tk.Canvas(tab, bg=BG, height=220, highlightthickness=0, bd=0)
        self._dd_canvas.pack(fill="x", padx=16, pady=(12, 8))
        self._risk_info = tk.Frame(tab, bg=BG)
        self._risk_info.pack(fill="x", padx=16)

    def _build_trades_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Trades  ")
        cols = ("entry_time", "exit_time", "side", "entry_price", "exit_price", "net_pnl", "fees",
                "holding_hours", "entry_notional", "return_pct", "entry_reason", "exit_reason")
        self._trades_tree = ttk.Treeview(tab, columns=cols, show="headings", height=20)
        widths = {"entry_time": 140, "exit_time": 140, "side": 55, "entry_price": 90, "exit_price": 90,
                  "net_pnl": 85, "fees": 70, "holding_hours": 80, "entry_notional": 95,
                  "return_pct": 75, "entry_reason": 90, "exit_reason": 90}
        for c in cols:
            self._trades_tree.heading(c, text=c.replace("_", " ").title(),
                                       command=lambda col=c: self._sort_trades(col))
            self._trades_tree.column(c, width=widths.get(c, 80), anchor="e" if c not in ("side", "entry_reason", "exit_reason") else "w")
        sb = ttk.Scrollbar(tab, orient="vertical", command=self._trades_tree.yview)
        self._trades_tree.configure(yscrollcommand=sb.set)
        self._trades_tree.pack(side="left", fill="both", expand=True, padx=(16, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8, padx=(0, 16))
        self._trades_sort_reverse = False

    def _build_distribution_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Distribution  ")
        self._dist_canvas = tk.Canvas(tab, bg=BG, height=220, highlightthickness=0, bd=0)
        self._dist_canvas.pack(fill="x", padx=16, pady=(12, 8))
        self._dist_info = tk.Frame(tab, bg=BG)
        self._dist_info.pack(fill="x", padx=16)

    def _build_costs_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Costs  ")
        self._costs_frame = tk.Frame(tab, bg=BG)
        self._costs_frame.pack(fill="both", expand=True, padx=24, pady=16)

    def _build_heatmap_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Monthly returns  ")
        ttk.Label(tab, text="Monthly portfolio return (%) · first/last month may be partial", style="Sub.TLabel").pack(anchor="w", padx=16, pady=8)
        self._heatmap_canvas = tk.Canvas(tab, bg=BG, height=300, highlightthickness=0, bd=0)
        self._heatmap_canvas.pack(fill="both", expand=True, padx=16, pady=12)

    def _build_timing_tab(self):
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Trade timing & factors  ")
        controls = tk.Frame(tab, bg=BG)
        controls.pack(fill="x", padx=16, pady=8)
        self._timing_basis = tk.StringVar(value="entry")
        self._timing_side = tk.StringVar(value="All")
        self._timing_shift = tk.StringVar(value="0")
        self._timing_min = tk.StringVar(value="20")
        self._timing_weekends = tk.BooleanVar(value=False)
        for label,var,options in [("Trade time", self._timing_basis, ["entry", "exit"]), ("Side",self._timing_side,["All","LONG","SHORT"]), ("Shift clock (hours)",self._timing_shift,None), ("Min. sample",self._timing_min,None)]:
            group = tk.Frame(controls,bg=BG); group.pack(side="left",padx=(0,12))
            ttk.Label(group,text=label,style="Sub.TLabel").pack(anchor="w")
            if options:
                ttk.Combobox(group,textvariable=var,values=options,state="readonly",width=10).pack()
            else:
                ttk.Entry(group,textvariable=var,width=12).pack()
        ttk.Checkbutton(controls,text="Sabtu / Minggu",variable=self._timing_weekends).pack(side="left",padx=8,pady=(16,0))
        ttk.Button(controls,text="Apply",command=self._render_timing).pack(side="left",pady=(16,0))
        self._timing_note = ttk.Label(tab,style="Sub.TLabel",wraplength=900)
        self._timing_note.pack(fill="x",padx=16,pady=(0,4))
        self._timing_canvas = tk.Canvas(tab,bg=BG,height=240,highlightthickness=0)
        self._timing_canvas.pack(fill="x",padx=16)
        lower = ttk.Notebook(tab)
        lower.pack(fill="both",expand=True,padx=16,pady=8)
        self._cohort_trees = []
        for title in ["Weekday summary", "Side / entry reason"]:
            frame = tk.Frame(lower,bg=BG); lower.add(frame,text=title)
            columns = ("group","trades","net_pnl","expectancy","win_rate_pct","profit_factor","sample_status")
            tree = ttk.Treeview(frame,columns=columns,show="headings",height=8)
            for col,label in zip(columns,["Group","Trades","Net PnL (USDT)","Expectancy (USDT)","Win rate %","Profit factor","Sample"]):
                tree.heading(col,text=label); tree.column(col,width=125 if col != "group" else 155,anchor="w" if col in ("group","sample_status") else "e")
            sb=ttk.Scrollbar(frame,orient="vertical",command=tree.yview)
            tree.configure(yscrollcommand=sb.set); sb.pack(side="right",fill="y"); tree.pack(fill="both",expand=True)
            self._cohort_trees.append(tree)
        ttk.Button(tab,text="Export cohort tables (.csv)",command=self._export_cohorts).pack(anchor="e",padx=16,pady=(0,8))

    def _render_timing(self):
        if not self.run_data:
            return
        try:
            report = trade_cohorts(self.run_data["result"]["trades"], self._timing_basis.get(), float(self._timing_shift.get()), self._timing_side.get(), self._timing_weekends.get(), int(self._timing_min.get()))
        except (ValueError, TypeError) as exc:
            self._timing_note.configure(text=str(exc),foreground=BAD)
            return
        self._cohort_report = report
        self._timing_note.configure(foreground=TEXT2,text=f"Whole-trade PnL by {report['basis']} time, dataset clock {report['shift_hours']:+g}h. Heatmap: expectancy USDT / n trades. Weekend excluded: {report['excluded_weekend_trades']}; invalid: {report['invalid_trades']}. Hatched cells: n < {report['minimum']}. Exploratory; verify on holdout.")
        for tree, rows in zip(self._cohort_trees,[report["weekdays"],report["factors"]]):
            tree.delete(*tree.get_children())
            for r in rows:
                tree.insert("","end",values=(r.get("factor", "")+" "+r["label"],r["trades"],fmt(r["net_pnl"]),fmt(r["expectancy"]),fmt(r["win_rate_pct"]),fmt(r["profit_factor"]),r["sample_status"]))
        self._draw_timing()

    def _draw_timing(self):
        report=getattr(self,"_cohort_report",None)
        if report is None:
            return
        canvas=self._timing_canvas; canvas.delete("all")
        w=max(300,canvas.winfo_width()); h=240
        cw=(w-85)/6; ch=(h-30)/len(report["weekdays"])
        scale=max((abs(r["expectancy"] or 0) for r in report["cells"]),default=1) or 1
        for b in range(6):
            canvas.create_text(75+(b+.5)*cw,10,text=f"{b*4:02d}:00–{b*4+3:02d}:59",fill=TEXT2,font=("Consolas",9))
        for r in report["weekdays"]:
            canvas.create_text(65,30+(r["day"]+.5)*ch,text=r["label"],anchor="e",fill=TEXT,font=("Segoe UI",9))
        for r in report["cells"]:
            x,y=75+r["bucket"]*cw,30+r["day"]*ch
            intensity=min(1,abs(r["expectancy"] or 0)/scale)
            rgb=(39,99,75) if (r["expectancy"] or 0)>=0 else (165,64,56)
            color="#"+"".join(f"{int(247+(c-247)*intensity):02x}" for c in rgb)
            canvas.create_rectangle(x,y,x+cw-2,y+ch-2,fill=color,outline=BORDER)
            if 0 < r["trades"] < report["minimum"]:
                canvas.create_line(x,y+ch-2,x+cw-2,y,fill=TEXT2,dash=(2,4))
            text=f"{fmt(r['expectancy'])} / n={r['trades']}" if r["trades"] else "— / n=0"
            canvas.create_text(x+cw/2,y+ch/2,text=text,fill=BG if intensity>.55 else TEXT,font=("Consolas",9))

    def _export_cohorts(self):
        report=getattr(self,"_cohort_report",None)
        if report is None:
            return
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".csv",initialfile="trade_cohorts.csv")
        if path:
            try:
                rows=[dict(section=section,**r) for section in ("weekdays","cells","factors") for r in report[section]]
                fields=list(dict.fromkeys(k for row in rows for k in row))
                with open(path,"w",newline="",encoding="utf-8-sig") as stream:
                    writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
                write_json(Path(path).with_suffix(".metadata.json"),{k:v for k,v in report.items() if k not in ("weekdays","cells","factors")})
                self._timing_note.configure(text=f"Exported {Path(path).name} + metadata JSON.")
            except Exception as exc:
                self._timing_note.configure(text=str(exc),foreground=BAD)

    def _build_placeholder_tab(self, title: str, message: str) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text=f"  {title}  ")
        tk.Label(tab, text="NOT AVAILABLE", bg=BG, fg="#E67700", font=("Consolas", 12, "bold")).pack(pady=(100, 8))
        tk.Label(tab, text=message, bg=BG, fg=TEXT2, font=("Segoe UI", 10)).pack()

    def _build_simulation_tab(self) -> None:
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text="  Simulation  ")
        controls = tk.Frame(tab, bg=BG); controls.pack(fill="x", padx=16, pady=(12, 6))
        self._sim_method = tk.StringVar(value="bootstrap"); self._sim_paths = tk.StringVar(value="500")
        self._sim_seed = tk.StringVar(value="42"); self._sim_block = tk.StringVar(value="5"); self._sim_threshold = tk.StringVar(value="20")
        for label, var, values in [("Method", self._sim_method, ["bootstrap", "permutation", "block_bootstrap"]), ("Paths", self._sim_paths, None), ("Seed", self._sim_seed, None), ("Block", self._sim_block, None), ("DD threshold %", self._sim_threshold, None)]:
            f = tk.Frame(controls, bg=BG); f.pack(side="left", padx=(0, 8)); tk.Label(f, text=label, bg=BG, fg=TEXT2, font=("Segoe UI", 8)).pack(anchor="w")
            (ttk.Combobox(f, textvariable=var, values=values, state="readonly", width=14) if values else ttk.Entry(f, textvariable=var, width=10)).pack()
        ttk.Button(controls, text="Run Simulation", style="Primary.TButton", command=self._run_simulation).pack(side="left", pady=(12, 0))
        self._sim_diag = tk.Label(tab, text="Simulation menggunakan completed trade ledger.", bg=BG, fg=TEXT2, font=("Consolas", 9), anchor="w"); self._sim_diag.pack(fill="x", padx=16)
        self._sim_canvas = tk.Canvas(tab, bg=BG, height=270, highlightthickness=0, bd=0); self._sim_canvas.pack(fill="both", expand=True, padx=16, pady=8)
        self._sim_info = tk.Frame(tab, bg=BG); self._sim_info.pack(fill="x", padx=16, pady=(0, 12))

    def _run_simulation(self) -> None:
        if self._busy:
            return
        if not self.run_data or not self.run_dir:
            self._sim_diag.configure(text="Jalankan backtest terlebih dahulu.", fg=BAD); return
        try:
            kwargs = dict(method=self._sim_method.get(), paths=int(self._sim_paths.get()), seed=int(self._sim_seed.get()), block_length=int(self._sim_block.get()), drawdown_threshold_pct=float(self._sim_threshold.get()))
            self._busy = True
            self._sim_future = self._executor.submit(run_simulation, self.run_data["result"], **kwargs)
            self._sim_diag.configure(text="Simulation running…", fg=TEXT2)
            self.after(100, self._poll_simulation)
        except (ValueError, TypeError) as exc: self._sim_diag.configure(text=str(exc), fg=BAD)

    def _poll_simulation(self):
        if not self._sim_future.done():
            self.after(100, self._poll_simulation)
            return
        self._busy = False
        try:
            output = self._sim_future.result()
            write_json(self.run_dir / f"simulation_{output['method']}.json", output)
            self._sim_output = output
            self._sim_diag.configure(text="Saved. Fixed-PnL resampling; not a margin-aware simulation.", fg=GOOD)
            self._render_simulation()
        except Exception as exc:
            self._sim_diag.configure(text=str(exc), fg=BAD)

    def _render_simulation(self) -> None:
        output = getattr(self, "_sim_output", None)
        if not output: return
        self._sim_canvas.update_idletasks()
        draw_fan(self._sim_canvas, output)
        for child in self._sim_info.winfo_children(): child.destroy()
        for name, value in [
            ("Method / seed / paths", f"{output['method']} · seed {output['seed']} · {output['paths']} paths · {output['trade_count']} trades"),
            ("Source run", self.run_data["run_id"] if self.run_data else "—"),
            ("Loss probability", f"{output['probability_of_loss_pct']:.2f}%"),
            ("DD breach probability", f"{output['probability_drawdown_breach_pct']:.2f}% (threshold {output['drawdown_threshold_pct']:.0f}%)"),
            ("Terminal equity P05/P25/P50/P75/P95", "/".join(f"{v:,.0f}" for v in output["terminal_equity_percentiles"])),
            ("Max drawdown P05/P50/P95", "/".join(f"{v:.1f}%" for v in output["max_drawdown_percentiles"][::2])),
        ]:
            tk.Label(self._sim_info, text=f"{name}: {value}", bg=BG, fg=TEXT, font=("Consolas", 10), anchor="w").pack(fill="x")

    # ─── Data Catalog ───

    def _build_data_page(self) -> None:
        page = tk.Frame(self._container, bg=BG)
        self._pages["data"] = page
        hdr = tk.Frame(page, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(16, 8))
        ttk.Label(hdr, text="CATALOG", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(hdr, text="Available Datasets", style="Header.TLabel").pack(anchor="w")
        cols = ("name", "id", "size_mb")
        tree = ttk.Treeview(page, columns=cols, show="headings", height=20)
        tree.heading("name", text="Name")
        tree.heading("id", text="Path")
        tree.heading("size_mb", text="Size (MB)")
        tree.column("name", width=220)
        tree.column("id", width=380)
        tree.column("size_mb", width=80, anchor="e")
        for d in self.datasets:
            tree.insert("", "end", values=(d["name"], d["id"], d["size_mb"]))
        tree.pack(fill="both", expand=True, padx=24, pady=8)

    def _build_history_page(self) -> None:
        page = tk.Frame(self._container, bg=BG)
        self._pages["history"] = page
        hdr = tk.Frame(page, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(16, 8))
        ttk.Label(hdr, text="RUN HISTORY", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(hdr, text="Previous runs", style="Header.TLabel").pack(anchor="w")
        self._history_tree = ttk.Treeview(page, columns=("run_id", "created", "status", "trades"), show="headings", height=20)
        for col, label, width in [("run_id", "Run ID", 260), ("created", "Created", 200), ("status", "Status", 110), ("trades", "Trades", 80)]:
            self._history_tree.heading(col, text=label)
            self._history_tree.column(col, width=width, anchor="e" if col == "trades" else "w")
        self._history_tree.pack(fill="both", expand=True, padx=24, pady=8)
        btn = tk.Frame(page, bg=BG)
        btn.pack(fill="x", padx=24)
        ttk.Button(btn, text="Load selected run", command=self._load_history_run).pack(side="left")

    def _refresh_history(self) -> None:
        self._history_tree.delete(*self._history_tree.get_children())
        for run in sorted(RUNS_ROOT.iterdir(), reverse=True):
            if not run.is_dir():
                continue
            metrics_path = run / "metrics.json"
            diag_path = run / "diagnostics.json"
            status, trades = "completed", ""
            if diag_path.exists():
                try:
                    diag = json.loads(diag_path.read_text(encoding="utf-8"))
                    status = diag.get("status", "error")
                except Exception:
                    status = "error"
            if metrics_path.exists():
                try:
                    trades = str(json.loads(metrics_path.read_text(encoding="utf-8")).get("trades", ""))
                except Exception:
                    pass
            created = datetime.fromtimestamp(run.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self._history_tree.insert("", "end", values=(run.name, created, status, trades))

    def _load_history_run(self) -> None:
        selection = self._history_tree.selection()
        if not selection:
            return
        run_id = self._history_tree.item(selection[0], "values")[0]
        run_dir = RUNS_ROOT / run_id
        try:
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._diag(f"Run tidak dapat dimuat: {exc}", error=True)
            return
        self.run_data = {"run_id": run_id, "result": result, "manifest": manifest}
        self.run_dir = run_dir
        self._sim_output = None
        self._diag(f"Loaded · {run_id}", success=True)
        self._rendered_run = None
        self._show_page("results")

    # ─── Run backtest ───

    def _cancel_worker(self):
        process = getattr(self, "_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            self._diag("Worker dihentikan; menunggu proses selesai.")

    def _run_backtest(self) -> None:
        if self._busy:
            self._diag("Pekerjaan sebelumnya masih berjalan.")
            return
        if not getattr(self, "_selected_dataset", None):
            self._diag("Pilih kombinasi pair, timeframe, dan broker yang tersedia.", error=True)
            return
        dataset_path = self._selected_dataset["path"]
        code = self._code_text.get("1.0", "end").strip()
        if len(code) < 20:
            self._diag("Kode strategy terlalu pendek.", error=True)
            return
        try:
            params = parse_params(self._params_text.get("1.0", "end"))
        except ValueError as exc:
            self._diag(f"Parameters tidak valid: {exc}", error=True)
            return
        try:
            config = EngineConfig(
                initial_equity=1000.0, max_leverage=10.0,
                default_leverage=float(self._leverage_var.get()),
                taker_fee_bps=float(self._fee_var.get()),
                half_spread_bps=float(self._spread_var.get()),
                slippage_bps=float(self._slip_var.get()),
            )
            config.validate()
        except (ValueError, TypeError) as exc:
            self._diag(str(exc), error=True)
            return
        self._diag("Worker sedang memuat dataset dan menjalankan strategy…")
        self.update_idletasks()
        RUNS_ROOT.mkdir(exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        run_dir = RUNS_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        request = {
            "run_id": run_id, "run_dir": str(run_dir), "dataset": dataset_path,
            "strategy_code": code, "parameters": params, "config": config.__dict__,
        }
        write_json(run_dir / "request.json", request)
        self._busy = True
        self._future = self._executor.submit(self._execute_worker, run_dir)
        self.after(100, lambda: self._poll_worker(run_dir))

    def _execute_worker(self, run_dir):
        with (run_dir / "worker.log").open("w", encoding="utf-8") as log:
            self._process = subprocess.Popen(
                [sys.executable, str(APP_ROOT / "worker.py"), str(run_dir / "request.json")],
                cwd=str(APP_ROOT), stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                code = self._process.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
                raise ValueError("Backtest melebihi batas 30 menit; worker dihentikan.")
        if code != 0:
            diag_path = run_dir / "diagnostics.json"
            msg = "Backtest gagal."
            if diag_path.exists():
                try:
                    msg = json.loads(diag_path.read_text(encoding="utf-8")).get("message", msg)
                except Exception:
                    pass
            raise ValueError(msg + " Lihat worker.log dan diagnostics.json.")
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        return {"run_id": run_dir.name, "result": result, "manifest": manifest}

    def _poll_worker(self, run_dir):
        if not self._future.done():
            self.after(100, lambda: self._poll_worker(run_dir))
            return
        self._busy = False
        try:
            self.run_data = self._future.result()
            self.run_dir = run_dir
            self._sim_output = None
            self._sim_canvas.delete("all")
            for child in self._sim_info.winfo_children():
                child.destroy()
            self._diag(f"Completed · {run_dir.name}", success=True)
            self._show_page("results")
        except Exception as exc:
            self._diag(str(exc), error=True)

    def _diag(self, msg: str, error: bool = False, success: bool = False) -> None:
        self._diag_label.configure(text=msg, fg=BAD if error else GOOD if success else TEXT2)

    # ─── Render results ───

    def _schedule_result_render(self) -> None:
        if self.run_data and not self._rendering:
            if self._render_job is not None:
                self.after_cancel(self._render_job)
            self._render_job = self.after(120, self._render_results)

    def _render_results(self) -> None:
        self._render_job = None
        if self._rendering:
            return
        self._rendering = True
        try:
            if self.run_data and self._rendered_run == self.run_data["run_id"]:
                self._redraw_charts()
            else:
                self._populate_results()
                if self.run_data:
                    self._rendered_run = self.run_data["run_id"]
        finally:
            self._rendering = False

    def _redraw_charts(self):
        result = self.run_data["result"]
        equity_times = [p["time"] for p in result["equity"]]
        draw_line_chart(self._rolling_canvas, [p["value"] for p in result.get("rolling_sharpe_90d", [])], max(400, self._rolling_canvas.winfo_width()), 240)
        draw_line_chart(self._equity_canvas, [p["equity"] for p in result["equity"]], max(400, self._equity_canvas.winfo_width()), 240, labels=equity_times)
        draw_line_chart(self._overview_dd, [p["drawdown_pct"] for p in result.get("drawdown", [])], max(300, self._overview_dd.winfo_width()), 240, color=BAD, fill_below="#FDECEC", labels=equity_times)
        draw_line_chart(self._dd_canvas, [p["drawdown_pct"] for p in result.get("drawdown", [])], max(400, self._dd_canvas.winfo_width()), 220, color=BAD, fill_below="#FDECEC")
        draw_histogram(self._dist_canvas, [t["net_pnl"] for t in result["trades"]], max(400, self._dist_canvas.winfo_width()), 220)
        draw_heatmap(self._heatmap_canvas, result.get("monthly_returns", []), max(400, self._heatmap_canvas.winfo_width()), max(200, self._heatmap_canvas.winfo_height()))
        if getattr(self, "_sim_output", None):
            draw_fan(self._sim_canvas, self._sim_output)

    def _populate_results(self) -> None:
        if not self.run_data:
            return
        self._empty_frame.pack_forget()
        self._result_frame.pack(fill="both", expand=True)
        result = self.run_data["result"]
        m = result["metrics"]
        run_id = self.run_data["run_id"]
        self._run_id_label.configure(text=f"COMPLETED  ·  {run_id}")
        self._audit_text.configure(state="normal")
        self._audit_text.delete("1.0", "end")
        self._audit_text.insert("1.0", json.dumps({"methodology": result.get("methodology", {"note": "Legacy run: rerun to use current analytics"}), "data_quality": result.get("data_quality", {}), "warnings": self.run_data["manifest"].get("warnings", []) + result.get("warnings", []), "metrics": m}, indent=2, ensure_ascii=False))
        self._audit_text.configure(state="disabled")
        # KPI grid
        for w in self._kpi_frame.winfo_children():
            w.destroy()
        kpis = [
            ("Net PnL", f"{fmt(m['net_pnl'])} USDT", m["net_pnl"]),
            ("Return", f"{fmt(m['return_pct'])}%", m["return_pct"]),
            ("CAGR", f"{fmt(m.get('cagr_pct', 0))}%", m.get("cagr_pct", 0)),
            ("Sharpe", fmt(m.get("sharpe", 0), 4), None),
            ("Sortino", fmt(m.get("sortino", 0), 4), None),
            ("Calmar", fmt(m.get("calmar", 0), 4), None),
            ("Max Drawdown", f"{fmt(m['max_drawdown_pct'])}%", -1),
            ("Volatility", f"{fmt(m.get('annual_volatility_pct', 0))}%", None),
            ("Trades", str(m["trades"]), None),
            ("Win Rate", f"{fmt(m['win_rate_pct'])}%", None),
            ("Profit Factor", fmt(m.get("profit_factor"), 4), None),
            ("Payoff Ratio", fmt(m.get("payoff_ratio"), 4), None),
            ("Expectancy", f"{fmt(m.get('expectancy', 0), 4)} USDT", m.get("expectancy", 0)),
            ("Exposure", f"{fmt(m.get('exposure_pct', 0))}%", None),
            ("Turnover", f"{fmt(m.get('turnover_equity_x', 0))}×", None),
            ("Total Fees", f"{fmt(m.get('total_fees', 0))} USDT", -1),
        ]
        cols = 4
        for i, (name, val_str, sentiment) in enumerate(kpis):
            r, c = divmod(i, cols)
            cell = tk.Frame(self._kpi_frame, bg=SURFACE, bd=1, relief="solid", highlightthickness=0)
            cell.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
            self._kpi_frame.columnconfigure(c, weight=1)
            ttk.Label(cell, text=name, style="MetricName.TLabel").pack(anchor="w", padx=10, pady=(8, 2))
            sty = "Metric.TLabel"
            if sentiment is not None:
                if sentiment > 0:
                    sty = "Good.TLabel"
                elif sentiment < 0:
                    sty = "Bad.TLabel"
            ttk.Label(cell, text=val_str, style=sty).pack(anchor="w", padx=10, pady=(0, 8))
        # equity + drawdown side-by-side on Overview
        equity_times = [p["time"] for p in result["equity"]]
        eq_vals = [p["equity"] for p in result["equity"]]
        w = max(400, self._equity_canvas.winfo_width())
        draw_line_chart(self._equity_canvas, eq_vals, w, 240, color=ACCENT, labels=equity_times)
        dd_vals = [p["drawdown_pct"] for p in result.get("drawdown", [])]
        draw_line_chart(self._overview_dd, dd_vals, max(300, self._overview_dd.winfo_width()), 240, color=BAD, fill_below="#FDECEC", labels=equity_times)
        # risk tab
        w2 = max(400, self._dd_canvas.winfo_width())
        draw_line_chart(self._dd_canvas, dd_vals, w2, 220, color=BAD, fill_below="#FDECEC")
        for w in self._risk_info.winfo_children():
            w.destroy()
        risk_items = [
            ("Max Drawdown", f"{fmt(m['max_drawdown_pct'])}%"),
            ("Longest Underwater", f"{m.get('longest_underwater_bars', 0)} bars"),
            ("Active Underwater", f"{m.get('active_underwater_bars', 0)} bars"),
            ("VaR 95%", f"{fmt(m.get('var_95_pct', 0))}%"),
            ("Expected Shortfall 95%", f"{fmt(m.get('expected_shortfall_95_pct', 0))}%"),
        ]
        for name, val in risk_items:
            rf = tk.Frame(self._risk_info, bg=BG)
            rf.pack(fill="x", pady=1)
            tk.Label(rf, text=name, bg=BG, fg=TEXT2, font=("Segoe UI", 10), width=24, anchor="w").pack(side="left")
            tk.Label(rf, text=val, bg=BG, fg=TEXT, font=("Consolas", 10)).pack(side="left")
        # trades tab
        self._trades_tree.delete(*self._trades_tree.get_children())
        for t in reversed(result["trades"][-500:]):
            vals = (t.get("entry_time", ""), t.get("exit_time", ""), t.get("side", ""),
                    fmt(t.get("entry_price", 0), 4), fmt(t.get("exit_price", 0), 4),
                    fmt(t.get("net_pnl", 0)), fmt(t.get("fees", 0)),
                    fmt(t.get("holding_hours", 0), 1), fmt(t.get("entry_notional", 0)),
                    fmt(t.get("return_pct", 0)), t.get("entry_reason", ""), t.get("exit_reason", ""))
            iid = self._trades_tree.insert("", "end", values=vals)
            if t.get("net_pnl", 0) >= 0:
                self._trades_tree.item(iid, tags=("good",))
            else:
                self._trades_tree.item(iid, tags=("bad",))
        self._trades_tree.tag_configure("good", foreground=GOOD)
        self._trades_tree.tag_configure("bad", foreground=BAD)
        # distribution tab
        pnls = [t["net_pnl"] for t in result["trades"]]
        w3 = max(400, self._dist_canvas.winfo_width())
        draw_histogram(self._dist_canvas, pnls, w3, 220)
        for w in self._dist_info.winfo_children():
            w.destroy()
        dist_items = [
            ("Skewness", fmt(m.get("skewness", 0), 4)),
            ("Kurtosis", fmt(m.get("kurtosis", 0), 4)),
            ("Winners", str(len([p for p in pnls if p > 0]))),
            ("Losers", str(len([p for p in pnls if p < 0]))),
        ]
        # streaks
        if pnls:
            max_win_streak = max_loss_streak = cur_win = cur_loss = 0
            for p in pnls:
                if p > 0:
                    cur_win += 1
                    cur_loss = 0
                else:
                    cur_loss += 1
                    cur_win = 0
                max_win_streak = max(max_win_streak, cur_win)
                max_loss_streak = max(max_loss_streak, cur_loss)
            dist_items.append(("Max Win Streak", str(max_win_streak)))
            dist_items.append(("Max Loss Streak", str(max_loss_streak)))
        for name, val in dist_items:
            rf = tk.Frame(self._dist_info, bg=BG)
            rf.pack(fill="x", pady=1)
            tk.Label(rf, text=name, bg=BG, fg=TEXT2, font=("Segoe UI", 10), width=20, anchor="w").pack(side="left")
            tk.Label(rf, text=val, bg=BG, fg=TEXT, font=("Consolas", 10)).pack(side="left")
        # costs tab
        for w in self._costs_frame.winfo_children():
            w.destroy()
        ttk.Label(self._costs_frame, text="Cost Attribution", style="Header.TLabel").pack(anchor="w", pady=(0, 12))
        total_fees = m.get("total_fees", 0)
        manifest = self.run_data["manifest"]
        cfg = manifest.get("config", {})
        cost_items = [
            ("Total fees", f"{fmt(total_fees)} USDT"),
            ("Spread cost", f"{fmt(m.get('total_spread_cost'))} USDT"),
            ("Slippage cost", f"{fmt(m.get('total_slippage_cost'))} USDT"),
            ("PnL reconciliation", f"{fmt(m.get('pnl_reconciliation_error'), 6)} USDT"),
            ("Taker fee rate", f"{cfg.get('taker_fee_bps', '?')} bps per side"),
            ("Half-spread", f"{cfg.get('half_spread_bps', '?')} bps per side"),
            ("Slippage", f"{cfg.get('slippage_bps', '?')} bps per side"),
            ("Funding", "Not available — excluded in MVP"),
        ]
        for name, val in cost_items:
            rf = tk.Frame(self._costs_frame, bg=BG)
            rf.pack(fill="x", pady=2)
            tk.Label(rf, text=name, bg=BG, fg=TEXT2, font=("Segoe UI", 10), width=20, anchor="w").pack(side="left")
            color = "#E67700" if "Not available" in val else TEXT
            tk.Label(rf, text=val, bg=BG, fg=color, font=("Consolas", 10)).pack(side="left")
        warn_frame = tk.Frame(self._costs_frame, bg="#FFF3CD", bd=1, relief="solid")
        warn_frame.pack(fill="x", pady=(16, 0))
        tk.Label(warn_frame, text="Funding dan liquidation belum dimodelkan. Spread/slippage adalah asumsi tetap per fill; gross_pnl sudah sesudah friction, sebelum fees.",
                 bg="#FFF3CD", fg="#664D03", font=("Segoe UI", 9), wraplength=500, justify="left").pack(padx=12, pady=8)
        # heatmap tab
        monthly = result.get("monthly_returns", [])
        w4 = max(400, self._heatmap_canvas.winfo_width())
        h4 = max(200, self._heatmap_canvas.winfo_height())
        draw_heatmap(self._heatmap_canvas, monthly, w4, h4)
        draw_line_chart(self._rolling_canvas, [p["value"] for p in result.get("rolling_sharpe_90d", [])], max(400, self._rolling_canvas.winfo_width()), 240)

    def _sort_trades(self, col: str) -> None:
        items = [(self._trades_tree.set(k, col), k) for k in self._trades_tree.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0].replace(",", "")), reverse=self._trades_sort_reverse)
        except ValueError:
            items.sort(key=lambda t: t[0], reverse=self._trades_sort_reverse)
        for index, (_, k) in enumerate(items):
            self._trades_tree.move(k, "", index)
        self._trades_sort_reverse = not self._trades_sort_reverse

    # ─── Export ───

    def _open_export(self) -> None:
        if not self.run_data or not self.run_dir:
            return
        dlg = tk.Toplevel(self)
        dlg.title("Export to Obsidian")
        dlg.geometry("480x420")
        dlg.configure(bg=BG)
        dlg.transient(self)
        dlg.grab_set()
        ttk.Label(dlg, text="KNOWLEDGE CAPTURE", style="Sub.TLabel").pack(anchor="w", padx=20, pady=(16, 2))
        ttk.Label(dlg, text="Export to Obsidian", style="Header.TLabel").pack(anchor="w", padx=20)
        ttk.Label(dlg, text="Judul eksperimen").pack(anchor="w", padx=20, pady=(12, 2))
        title_var = tk.StringVar(value="EMA Cross Experiment")
        ttk.Entry(dlg, textvariable=title_var).pack(fill="x", padx=20)
        ttk.Label(dlg, text="Hipotesis").pack(anchor="w", padx=20, pady=(8, 2))
        hyp_text = tk.Text(dlg, height=3, font=("Segoe UI", 10), bg=SURFACE, bd=1, relief="solid")
        hyp_text.pack(fill="x", padx=20)
        ttk.Label(dlg, text="Mengapa hasil ini menarik?").pack(anchor="w", padx=20, pady=(8, 2))
        int_text = tk.Text(dlg, height=3, font=("Segoe UI", 10), bg=SURFACE, bd=1, relief="solid")
        int_text.pack(fill="x", padx=20)
        ttk.Label(dlg, text="Status").pack(anchor="w", padx=20, pady=(8, 2))
        status_var = tk.StringVar(value="observation")
        ttk.Combobox(dlg, textvariable=status_var, state="readonly",
                     values=["observation", "revise", "validate-further", "candidate", "rejected"]).pack(fill="x", padx=20)
        msg_label = tk.Label(dlg, text="", bg=BG, fg=TEXT2, font=("Consolas", 9))
        msg_label.pack(fill="x", padx=20, pady=(8, 0))

        def do_export():
            try:
                path = export_to_obsidian(
                    self.run_dir, VAULT_ROOT,
                    title_var.get(), hyp_text.get("1.0", "end").strip(),
                    int_text.get("1.0", "end").strip(), status_var.get(),
                )
                msg_label.configure(text=f"Tersimpan: {path}", fg=GOOD)
            except Exception as exc:
                msg_label.configure(text=str(exc), fg=BAD)

        btn_frame = tk.Frame(dlg, bg=BG)
        btn_frame.pack(fill="x", padx=20, pady=(12, 16))
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btn_frame, text="Export note", style="Primary.TButton", command=do_export).pack(side="right")


def main() -> None:
    RUNS_ROOT.mkdir(exist_ok=True)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
