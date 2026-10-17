"""Synthetic archive tests: no trading imports, broker connection, or live writes."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/report_forward_paper_interim.py"
spec = importlib.util.spec_from_file_location("paper_interim", SCRIPT)
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / "paper"
    source.mkdir()
    (source / "bars").mkdir()
    (source / "ticks").mkdir()
    manifest = {"start": 1000, "end": 10000, "offset": 10800,
                "symbols": ["AAA", "BBB"], "settings": {"strategy": {"confirmation_timeframe": "H1"}}}
    trade = {"symbol": "AAA", "side": "BUY", "entry_time": 1200, "entry": 2,
             "sl": 1, "tp": 4, "lots": .1, "exit_time": 1400, "exit_reason": "target",
             "net_pnl": 2, "static_screen_reasons": ["research only"]}
    state = {"status": "running", "last_run": 7900,
             "books": {"trend_zone": {"positions": [], "closed": [trade]}},
             "errors": [{"utc": reporter.iso(1800), "family": "trend_zone", "symbol": "BBB",
                         "phase": "scan", "error": "Stale/invalid quote"}]}
    (source / "manifest.json").write_text(json.dumps(manifest))
    (source / "state.json").write_text(json.dumps(state))
    observation = {"utc": reporter.iso(1200), "symbol": "AAA", "family": "trend_zone",
                   "signal_bar_broker": 11000, "plan": {"valid": True, "action": "BUY"}, "paper_entry": trade}
    warmup = {**observation, "utc": reporter.iso(999)}
    (source / "observations.jsonl").write_text(json.dumps(observation) + "\n" + json.dumps(warmup) + "\n")
    (source / "trades.jsonl").write_text(json.dumps({"family": "trend_zone", **trade}) + "\n")
    for suffix, times in [(14400, [7200, 10800, 14400]), (18000, [10800, 14400, 18000])]:
        pd.DataFrame({"time": times}).to_parquet(source / "bars" / f"AAA_H1_{suffix}.parquet")
        pd.DataFrame({"time": times}).to_parquet(source / "bars" / f"BBB_H1_{suffix}.parquet")
    for left, right, times in [(12000, 13000, [12000, 12500]), (12500, 14000, [12500, 13500]), (15000, 16000, [15500])]:
        pd.DataFrame({"time_msc": [t*1000 for t in times], "bid": [2.0]*len(times),
                      "ask": [2.1]*len(times)}).to_parquet(source / "ticks" / f"AAA_{left*1000}_{right*1000}.parquet")
    return source


def hashes(directory):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file()}


def test_overlap_offset_warmup_and_trade_reconciliation_are_read_only(archive):
    before = hashes(archive)
    result = reporter.summarize(archive, now=8000)
    assert hashes(archive) == before
    bars = result["bar_coverage"]["AAA/H1"]
    assert bars["archived_rows_with_overlap"] == 6
    assert bars["all_history"]["unique_timestamps"] == 4
    # In-window CLOSED bars include open time zero (close=3600).
    assert bars["closed_in_measurement_window"]["unique_timestamps"] == 2
    tick = result["tick_coverage"]["AAA"]
    assert tick["archived_rows_with_overlap"] == 5
    assert tick["unique_valid_quote_triples_in_window"] == 4
    assert tick["archived_request_ranges"]["union_seconds"] == 3000
    assert tick["archived_request_ranges"]["gaps"][0]["seconds"] == 1000
    cell = result["cells"][0]
    assert (cell["observations"], cell["entries"], cell["exits"], cell["open_in_evidence"]) == (1, 1, 1, 0)
    assert result["tick_coverage"]["BBB"]["files"] == 0
    assert sum(result["cells"][1]["retained_feed_errors"].values()) == 1
    assert not result["warnings"]
    assert "last 100" in reporter.markdown(result)


def test_corrupt_archive_and_partial_append_are_disclosed(archive):
    (archive / "bars" / "AAA_H1_999.parquet").write_bytes(b"not parquet")
    with (archive / "observations.jsonl").open("a") as f:
        f.write('{"utc":')
    result = reporter.summarize(archive, now=8000)
    assert len(result["warnings"]) == 2
    assert result["cells"][0]["observations"] == 1


def test_stale_run_start_is_not_treated_as_current_liveness(archive):
    path = archive / "state.json"
    state = json.loads(path.read_text())
    state["last_run"] = 1000
    path.write_text(json.dumps(state))
    result = reporter.summarize(archive, now=8000)
    assert any("liveness is unverified" in w for w in result["warnings"])


def test_cli_refuses_input_tree_and_existing_output(archive, monkeypatch):
    for output in [archive, archive / "interim", archive.parent]:
        monkeypatch.setattr("sys.argv", [str(SCRIPT), "--directory", str(archive), "--output-directory", str(output)])
        with pytest.raises(SystemExit) as exc:
            reporter.main()
        assert exc.value.code == 2
    output = archive.parent / "existing"
    output.mkdir()
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--directory", str(archive), "--output-directory", str(output)])
    with pytest.raises(SystemExit):
        reporter.main()
