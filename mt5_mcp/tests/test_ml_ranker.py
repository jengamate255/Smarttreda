import json

from ml_ranker import FEATURES, build_dataset, load_result_trades, rank_signal

SAMPLE_TRADES = [
    {"entry": 1.15, "entry_time": "2026-01-19T10:00:00", "pnl": 25.0, "rr": 3.0,
     "sl_pips": 20.0, "trend_confidence": 0.7, "zone_strength": 80.0,
     "confirmation_confidence": 0.6, "confirmation": "pin_bar (bearish, conf 0.60)"},
    {"entry": 1.14, "entry_time": "2026-02-01T15:00:00", "pnl": -12.5, "rr": 4.5,
     "sl_pips": 20.0, "trend_confidence": 0.6, "zone_strength": 70.0,
     "confirmation_confidence": 0.5, "confirmation": "shooting_star (bearish, conf 0.50)"},
]


def test_build_dataset():
    X, y, meta = build_dataset(SAMPLE_TRADES)
    assert X.shape == (2, len(FEATURES))
    assert y.tolist() == [1, 0]
    assert meta[0]["confirmation_confidence"] == 0.6


def test_rank_signal_in_unit_range():
    score = rank_signal(0.7, 80.0, 0.6, 3.0, 25.0, 10.0)
    assert 0.0 < score < 1.0


def test_load_result_trades_parses_rr3(tmp_path):
    fake = tmp_path / "EURUSD_H4_H1+H4_z40_rr3_tp80-100.json"
    fake.write_text(
        json.dumps(
            {
                "params": {"symbol": "EURUSD"},
                "n_trades": 1, "wins": 0, "losses": 1,
                "gross_profit": 0.0, "gross_loss": 10.0, "net_pnl": -10.0,
                "profit_factor": 0.0, "win_rate": 0.0, "expectancy": -10.0,
                "total_return_pct": -0.1, "max_drawdown_pct": 0.1, "avg_rr": 3.0,
                "start": "2026-01-01", "end": "2026-01-02", "errors": [],
                "trades": [SAMPLE_TRADES[0]],
            }
        ),
        encoding="utf-8",
    )
    trades = load_result_trades(tmp_path)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "EURUSD"
