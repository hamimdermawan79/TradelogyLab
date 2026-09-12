"""Metadata-only dataset discovery; never guess a file when a selection is invalid."""
from pathlib import Path
import re


def timeframe_key(value):
    match = re.fullmatch(r"(\d+)([mhdw])", value)
    return int(match[1]) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[match[2]] if match else 10**9


def discover(root):
    items = []
    for path in sorted(Path(root).rglob("*_clean.csv")):
        match = re.fullmatch(r"(.+)\.Perpetual_(\d+[mhdw])_(.+)_clean\.csv", path.name)
        if match:
            items.append(dict(id=path.relative_to(root).as_posix(), path=str(path.resolve()),
                              name=path.name, pair=match[1], timeframe=match[2], broker=match[3],
                              size_mb=round(path.stat().st_size / 1048576, 2)))
    return items


def select(items, pair, timeframe, broker):
    matches = [d for d in items if (d["pair"], d["timeframe"], d["broker"]) == (pair, timeframe, broker)]
    if len(matches) != 1:
        raise ValueError("Pilih satu kombinasi pair, timeframe, dan broker yang tersedia.")
    return matches[0]
