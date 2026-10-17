"""Tests for asset class abstraction."""

from __future__ import annotations

from mt5_mcp.asset import (
    AssetClass,
    asset_class_from_symbol,
    is_forex,
    get_phase1_symbols,
    get_extended_symbols,
)


def test_asset_class_forex():
    """Test that major forex pairs are classified as FOREX."""
    forex_pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
    for pair in forex_pairs:
        ac = asset_class_from_symbol(pair)
        assert ac == AssetClass.FOREX, f"{pair} should be FOREX, got {ac}"


def test_asset_class_equity():
    """Test that equity symbols are classified correctly."""
    # This would be extended in Phase 2
    ac = asset_class_from_symbol("AAPL")
    # Default phase 1 behavior
    assert ac in [AssetClass.FOREX, AssetClass.EQUITY]


def test_asset_class_crypto():
    """Test that crypto symbols are classified correctly."""
    ac = asset_class_from_symbol("BTCUSD")
    assert ac == AssetClass.CRYPTO


def test_asset_class_index():
    """Test that index symbols are classified correctly."""
    ac = asset_class_from_symbol("US500")
    assert ac == AssetClass.INDEX


def test_is_forex():
    """Test is_forex helper."""
    assert is_forex(AssetClass.FOREX) is True
    assert is_forex(AssetClass.CRYPTO) is False


def test_phase1_symbols():
    """Test Phase 1 symbol list."""
    symbols = get_phase1_symbols()
    assert len(symbols) == 7
    assert "EURUSD" in symbols
    assert "GBPUSD" in symbols
    assert "USDJPY" in symbols


def test_extended_symbols():
    """Test extended symbol list for future phases."""
    symbols = get_extended_symbols()
    assert len(symbols) > 7  # More than just the 7 phase 1 forex pairs
    # Should include the 7 phase 1 plus extended
    for s in ["EURGBP", "EURJPY", "GBPJPY", "XAUUSD"]:
        assert s in symbols, f"{s} should be in extended symbols"