from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_ai.data.validator import DataValidator, IssueSeverity


@pytest.fixture
def validator():
    return DataValidator()


class TestMissingCandleDetection:
    def test_detects_timestamp_gaps(self, validator, sample_ohlcv_with_gaps):
        report = validator.validate(sample_ohlcv_with_gaps, "EURUSD", "H1")
        gap_issues = [i for i in report.issues if i.category == "timestamp_gaps"]
        assert len(gap_issues) >= 1
        assert gap_issues[0].severity == IssueSeverity.WARNING

    def test_no_gaps_in_continuous_data(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        gap_issues = [i for i in report.issues if i.category == "timestamp_gaps"]
        assert len(gap_issues) == 0


class TestDuplicateDetection:
    def test_detects_duplicate_timestamps(self, validator, sample_ohlcv_duplicate_timestamps):
        report = validator.validate(sample_ohlcv_duplicate_timestamps, "EURUSD", "H1")
        dup_issues = [i for i in report.issues if i.category == "duplicate_candles"]
        assert len(dup_issues) == 1
        assert dup_issues[0].severity == IssueSeverity.WARNING

    def test_no_duplicates_in_clean_data(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        dup_issues = [i for i in report.issues if i.category == "duplicate_candles"]
        assert len(dup_issues) == 0


class TestInvalidOHLC:
    def test_detects_high_below_low(self, validator, sample_ohlcv_invalid):
        report = validator.validate(sample_ohlcv_invalid, "EURUSD", "H1")
        ohlc_issues = [i for i in report.issues if i.category == "invalid_ohlc"]
        assert len(ohlc_issues) >= 1
        assert any(i.severity == IssueSeverity.ERROR for i in ohlc_issues)

    def test_detects_nan_values(self, validator, sample_ohlcv_invalid):
        report = validator.validate(sample_ohlcv_invalid, "EURUSD", "H1")
        nan_issues = [i for i in report.issues if i.category == "nan_values"]
        assert len(nan_issues) >= 1

    def test_detects_negative_prices(self, validator, sample_ohlcv_invalid):
        report = validator.validate(sample_ohlcv_invalid, "EURUSD", "H1")
        neg_issues = [i for i in report.issues if i.category == "negative_prices"]
        assert len(neg_issues) >= 1

    def test_clean_data_has_no_ohlc_errors(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        ohlc_errors = [
            i for i in report.issues
            if i.category == "invalid_ohlc" and i.severity == IssueSeverity.ERROR
        ]
        assert len(ohlc_errors) == 0


class TestAbnormalSpread:
    def test_detects_abnormal_spread(self, validator, sample_ohlcv_wide_spread):
        report = validator.validate(sample_ohlcv_wide_spread, "EURUSD", "H1")
        spread_issues = [i for i in report.issues if i.category == "abnormal_spread"]
        assert len(spread_issues) == 1
        assert spread_issues[0].severity == IssueSeverity.WARNING

    def test_normal_spread_passes(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        spread_issues = [i for i in report.issues if i.category == "abnormal_spread"]
        assert len(spread_issues) == 0


class TestGapDetection:
    def test_gap_reported_in_metadata(self, validator, sample_ohlcv_with_gaps):
        report = validator.validate(sample_ohlcv_with_gaps, "EURUSD", "H1")
        gap_issues = [i for i in report.issues if i.category == "timestamp_gaps"]
        assert len(gap_issues) >= 1
        assert "gap_sizes" in gap_issues[0].metadata

    def test_empty_dataframe(self, validator):
        empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        empty_df.index.name = "time"
        report = validator.validate(empty_df, "EURUSD", "H1")
        assert report.has_errors
        assert any(i.category == "empty_data" for i in report.issues)


class TestValidationReport:
    def test_summary_contains_key_info(self, validator, sample_ohlcv_invalid):
        report = validator.validate(sample_ohlcv_invalid, "EURUSD", "H1")
        summary = report.summary()
        assert "EURUSD" in summary
        assert "H1" in summary
        assert "100" in summary  # total candles

    def test_has_errors_property(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        assert isinstance(report.has_errors, bool)

    def test_has_warnings_property(self, validator, sample_ohlcv):
        report = validator.validate(sample_ohlcv, "EURUSD", "H1")
        assert isinstance(report.has_warnings, bool)
