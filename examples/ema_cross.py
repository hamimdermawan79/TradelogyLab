import pandas as pd


def generate_signals(data: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Contoh trend-following sederhana untuk memvalidasi pipeline, bukan rekomendasi trading."""
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    if fast < 2 or slow <= fast:
        raise ValueError("Gunakan slow > fast >= 2.")

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
