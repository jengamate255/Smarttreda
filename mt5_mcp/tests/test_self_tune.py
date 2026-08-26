from mt5_mcp import self_tune


def _row(pnl: float) -> dict:
    return {"pnl": pnl}


def test_stats_basic():
    s = self_tune._stats([_row(10), _row(-5), _row(2)])
    assert s["trades"] == 3
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert abs(s["pf"] - 2.4) < 1e-9
    assert abs(s["avg_r"] - 7 / 3) < 1e-9
    assert abs(s["wr"] - 66.666) < 0.01


def test_stats_empty():
    assert self_tune._stats([])["trades"] == 0


def test_stats_losing_only():
    s = self_tune._stats([_row(-10), _row(-5)])
    assert s["pf"] == 0.0
    assert s["wins"] == 0


def test_tune_disables_negative_edge():
    stats = self_tune._stats([_row(5), _row(-10), _row(-8), _row(2), _row(3)])
    params = self_tune._tune_symbol(stats)
    assert params["enabled"] is False


def test_tune_tightens_weak_edge():
    stats = self_tune._stats([_row(11), _row(-10), _row(2), _row(-10), _row(8)])
    assert stats["trades"] >= self_tune.MIN_LIVE_TRADES
    assert abs(stats["pf"] - 1.05) < 1e-9
    params = self_tune._tune_symbol(stats)
    assert params["enabled"] is True
    assert params["confidence_floor"] > self_tune.DEFAULT_PARAMS["confidence_floor"]
    assert params["max_spike_age"] < self_tune.DEFAULT_PARAMS["max_spike_age"]


def test_tune_needs_sample():
    params = self_tune._tune_symbol(self_tune._stats([_row(1), _row(-1)]))
    assert params == self_tune.DEFAULT_PARAMS


def test_tune_reduces_size_on_losses():
    stats = self_tune._stats([_row(-3), _row(-2), _row(-1), _row(-4), _row(-2)])
    params = self_tune._tune_symbol(stats)
    assert params["size_multiplier"] < 1.0


def test_current_symbol_defaults_when_unlearned():
    params = self_tune.current_symbol("BOOM999")
    assert params["enabled"] is True
    assert params["confidence_floor"] == 90.0