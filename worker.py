from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from engine import EngineConfig, build_manifest, load_ohlcv, run_backtest, write_json


def main(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    run_dir = Path(request["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        data = load_ohlcv(request["dataset"])
        namespace = {"pd": pd, "np": np, "__name__": "tradelogy_user_strategy"}
        code = request["strategy_code"]
        compiled = compile(code, "strategy.py", "exec")
        exec(compiled, namespace)
        function = namespace.get("generate_signals")
        if not callable(function):
            raise ValueError("Strategy wajib mendefinisikan generate_signals(data, params).")
        output = function(data.copy(), request.get("parameters", {}))
        config = EngineConfig(**request["config"])
        result = run_backtest(data, output, config)
        manifest = build_manifest(Path(request["dataset"]), code, config, request["run_id"])
        manifest.update({"status": "completed", "parameters": request.get("parameters", {})})
        write_json(run_dir / "manifest.json", manifest)
        write_json(run_dir / "metrics.json", result["metrics"])
        write_json(run_dir / "result.json", result)
        (run_dir / "strategy.py").write_text(code, encoding="utf-8")
        pd.DataFrame(result["equity"]).to_csv(run_dir / "equity.csv", index=False)
        pd.DataFrame(result["trades"]).to_csv(run_dir / "trades.csv", index=False)
        write_json(run_dir / "diagnostics.json", {"status": "ok", "messages": []})
        print(json.dumps({"ok": True, "run_id": request["run_id"]}))
        return 0
    except Exception as exc:
        diagnostic = {"status": "error", "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        write_json(run_dir / "diagnostics.json", diagnostic)
        print(json.dumps({"ok": False, "error": diagnostic}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
