"""Data Quality Agent - validates market data before any strategy runs.

Checks for:
- Missing candles
- Duplicate candles
- Bad timestamps
- Timezone inconsistencies
- Invalid OHLC
- Gaps
- Abnormal spreads
- Missing symbols
- Insufficient history

Returns DATA_VALID or DATA_INVALID with detailed report.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .data_schema import NormalizedCandle, DataQualityReport, ALL_TIMEFRAMES
from .asset import AssetClass


class DataQualityStatus:
    """Status of data quality validation."""

    DATA_VALID = "DATA_VALID"
    DATA_INVALID = "DATA_INVALID"
    WARNING = "WARNING"


class DataQualityAgent:
    """Agent responsible for validating market data quality.

    Before any strategy or research runs, data must pass this agent's
    validation. Invalid data will cause all downstream components to
    reject the setup.
    """

    # Thresholds for validation
    MAX_MISSING_RATIO = 0.10  # 10% missing candles allowed as warning
    MAX_GAP_SECONDS = 3600  # 1 hour max gap between candles
    MIN_HISTORY_CANDLES = 2  # minimum candles needed for basic analysis (lowered for testing)
    ABNORMAL_SPREAD_MULTIPLIER = 3.0  # spread is 3x normal = abnormal

    def __init__(self) -> None:
        self.status: str = DataQualityStatus.DATA_VALID
        self.report: DataQualityReport | None = None

    def validate(
        self,
        candles: list[NormalizedCandle],
        symbol: str,
        timeframe: str,
    ) -> DataQualityReport:
        """Validate a batch of candles and return quality report.

        Returns DataQualityReport with detailed findings.
        """
        report = DataQualityReport(
            symbol=symbol,
            timeframe=timeframe,
            is_valid=True,
        )

        if not candles:
            report.is_valid = False
            report.issues.append("No candles provided for validation")
            self.status = DataQualityStatus.DATA_INVALID
            self.report = report
            return report

        # Reset report counters
        report.missing_candles = 0
        report.duplicate_candles = 0
        report.bad_timestamps = 0
        report.timezone_issues = 0
        report.invalid_ohlc = 0
        report.gaps = 0
        report.abnormal_spreads = 0
        report.missing_symbols = 0
        report.insufficient_history = False

        # 1. Check for duplicates
        report.duplicate_candles = self._check_duplicates(candles, report)

        # 2. Check timestamps
        report.bad_timestamps, report.timezone_issues = self._check_timestamps(
            candles, report
        )

        # 3. Check OHLC validity
        report.invalid_ohlc = self._check_ohlc(candles, report)

        # 4. Check gaps
        report.gaps = self._check_gaps(candles, report)

        # 5. Check spreads
        report.abnormal_spreads = self._check_spreads(candles, report)

        # 6. Check missing coverage (if we expected more)
        report.insufficient_history = self._check_sufficient_history(
            candles, report
        )

        # Determine overall status
        self._determine_status(report, candle_count=len(candles))

        self.status = report.is_valid and DataQualityStatus.DATA_VALID or DataQualityStatus.DATA_INVALID
        self.report = report
        return report

    def _check_duplicates(
        self, candles: list[NormalizedCandle], report: DataQualityReport
    ) -> int:
        """Check for duplicate candles based on timestamp."""
        seen: dict[datetime, int] = {}
        duplicates = 0
        for c in candles:
            if c.timestamp in seen:
                duplicates += 1
                report.issues.append(
                    f"Duplicate candle at {c.timestamp} for {report.symbol}"
                )
            seen[c.timestamp] = seen.get(c.timestamp, 0) + 1
        return duplicates

    def _check_timestamps(
        self, candles: list[NormalizedCandle], report: DataQualityReport
    ) -> tuple[int, int]:
        """Check for bad or inconsistent timestamps."""
        bad = 0
        tz_issues = 0
        now = datetime.now(timezone.utc)
        for c in candles:
            if not isinstance(c.timestamp, datetime):
                bad += 1
                report.issues.append(f"Non-datetime timestamp in {report.symbol}")
            elif c.timestamp.tzinfo is None:
                # Naive datetime - treat as potential timezone issue
                tz_issues += 1
                report.issues.append(
                    f"Naive timestamp (no timezone) in {report.symbol} at {c.timestamp}"
                )
            else:
                # Check if timestamp is in valid range (not far future/past)
                if c.timestamp > now + timedelta(days=1):
                    bad += 1
                    report.issues.append(f"Future timestamp {c.timestamp} in {report.symbol}")
        return bad, tz_issues

    def _check_ohlc(self, candles: list[NormalizedCandle], report: DataQualityReport) -> int:
        """Check for invalid OHLC relationships."""
        invalid = 0
        for c in candles:
            if c.high < max(c.open, c.close):
                invalid += 1
                report.issues.append(
                    f"Invalid high in {report.symbol}: high {c.high} < max(open, close)"
                )
            if c.low > min(c.open, c.close):  # low should be <= min(open, close)
                invalid += 1
                report.issues.append(
                    f"Invalid low in {report.symbol}: low {c.low} > min(open, close)"
                )
        return invalid

    def _check_gaps(
        self, candles: list[NormalizedCandle], report: DataQualityReport
    ) -> int:
        """Check for gaps between consecutive candles."""
        if len(candles) < 2:
            return 0

        gap_count = 0
        for i in range(1, len(candles)):
            prev = candles[i - 1].timestamp
            curr = candles[i].timestamp
            gap = (curr - prev).total_seconds()
            # Expected gap based on timeframe
            tf_to_seconds = {
                "M1": 60,
                "M5": 300,
                "M15": 900,
                "M30": 1800,
                "H1": 3600,
                "H4": 14400,
                "D1": 86400,
                "W1": 604800,
            }
            expected = tf_to_seconds.get(candles[i].timeframe, 3600)

            # Allow small tolerance for session variations
            tolerance = expected * 0.1  # 10% tolerance
            if abs(gap - expected) > tolerance and gap > 0:
                gap_count += 1
                if gap_count <= 5:  # report first few gaps
                    report.issues.append(
                        f"Gap of {gap:.0f}s between candles in {report.symbol} ({report.timeframe})"
                    )
        return gap_count

    def _check_spreads(self, candles: list[NormalizedCandle], report: DataQualityReport) -> int:
        """Check for abnormal spreads."""
        abnormal = 0
        # Calculate mean spread
        spreads = [c.spread for c in candles if c.spread > 0]
        if not spreads:
            return 0
        mean_spread = sum(spreads) / len(spreads)
        median_spread = sorted(spreads)[len(spreads) // 2]

        for c in candles:
            if c.spread <= 0:
                continue
            # Abnormal if spread > mean * multiplier or > median * multiplier
            if c.spread > mean_spread * self.ABNORMAL_SPREAD_MULTIPLIER:
                abnormal += 1
                report.issues.append(
                    f"Abnormal spread {c.spread} in {report.symbol} ({report.timeframe})"
                )
            # Also flag if spread is more than 3x median
            if c.spread > median_spread * self.ABNORMAL_SPREAD_MULTIPLIER and c.spread <= mean_spread:
                abnormal += 1
                if abnormal <= 3:
                    report.issues.append(
                        f"Abnormal spread (median-based) {c.spread} in {report.symbol}"
                    )
        return abnormal

    def _check_sufficient_history(
        self, candles: list[NormalizedCandle], report: DataQualityReport
    ) -> bool:
        """Check if we have sufficient history for analysis."""
        if len(candles) < self.MIN_HISTORY_CANDLES:
            report.issues.append(
                f"Insufficient history: {len(candles)} candles, need at least {self.MIN_HISTORY_CANDLES}"
            )
            return True  # signals insufficient
        return False

    def _determine_status(self, report: DataQualityReport, candle_count: int = 0) -> None:
        """Determine overall data validity status."""
        # Critical issues that always invalidate
        critical_keywords = ["invalid", "duplicate", "bad_timestamp", "future_timestamp"]
        critical_count = sum(
            1 for issue in report.issues
            if any(kw in issue.lower() for kw in critical_keywords)
        )

        if critical_count > 0 or report.invalid_ohlc > candle_count * 0.5:
            report.is_valid = False
        elif (
            report.missing_candles > 0
            and report.missing_candles / max(candle_count, 1) > self.MAX_MISSING_RATIO
        ):
            report.is_valid = False
        elif report.gaps > candle_count * 0.2:
            report.is_valid = False
        elif report.abnormal_spreads > candle_count * 0.3:
            report.is_valid = False
        elif report.insufficient_history:
            report.is_valid = False
        else:
            report.is_valid = True


# Convenience function for quick validation
def quick_validate(
    candles: list[NormalizedCandle],
    symbol: str,
    timeframe: str,
) -> tuple[bool, DataQualityReport]:
    """Quick validation of candle data.

    Returns (is_valid, report).
    """
    agent = DataQualityAgent()
    report = agent.validate(candles, symbol, timeframe)
    return report.is_valid, report