import numpy as np
import pytest

from simulation import run_simulation


def result():
    return {"metrics": {"initial_equity": 1000.0}, "trades": [{"net_pnl": 10.0}, {"net_pnl": -5.0}, {"net_pnl": 20.0}]}


def test_seeded_bootstrap_is_reproducible():
    a = run_simulation(result(), paths=20, seed=7)
    b = run_simulation(result(), paths=20, seed=7)
    assert a == b
    assert a["method"] == "bootstrap"


def test_permutation_and_block_bootstrap():
    for method in ("permutation", "block_bootstrap"):
        output = run_simulation(result(), method=method, paths=20, seed=3, block_length=2)
        assert len(output["terminal_equity"]) == 20
        assert len(output["max_drawdown_pct"]) == 20


def test_bounds_rejected():
    with pytest.raises(ValueError):
        run_simulation(result(), paths=9)
    with pytest.raises(ValueError):
        run_simulation({"metrics": {"initial_equity": 1000}, "trades": []})
