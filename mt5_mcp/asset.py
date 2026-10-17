"""Asset class abstraction for multi-asset support."""

from enum import Enum


class AssetClass(Enum):
    """Asset class types for multi-asset support."""

    FOREX = "forex"
    EQUITY = "equity"
    CRYPTO = "crypto"
    COMMODITY = "commodity"
    FUTURE = "future"
    INDEX = "index"


def asset_class_from_symbol(symbol: str) -> AssetClass:
    """Determine asset class from symbol prefix/name.

    Phase 1 default: Forex symbols.
    Future phases can extend this logic.
    """
    symbol_upper = symbol.upper()

    # Forex pairs
    forex_pairs = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
        "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY",
        "XAUUSD", "XAGUSD",
    ]
    if symbol_upper in forex_pairs:
        return AssetClass.FOREX

    # Crypto
    crypto_symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "ADAUSD", "DOTUSD", "DOGEUSD"]
    if symbol_upper in crypto_symbols:
        return AssetClass.CRYPTO

    # Index symbols
    index_symbols = ["US500", "US100", "DE40", "FR40", "UK100", "JP225", "HK50"]
    if symbol_upper in index_symbols:
        return AssetClass.INDEX

    # Default to FOREX for Phase 1
    return AssetClass.FOREX


def is_forex(asset_class: AssetClass) -> bool:
    """Check if asset class is Forex (Phase 1 default)."""
    return asset_class == AssetClass.FOREX


# Phase 1: Forex symbols (from the project vision)
PHASE1_FOREX_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "NZDUSD",
]

# Extended future symbols (from the project vision)
FUTURE_PHASE1_SYMBOLS = PHASE1_FOREX_SYMBOLS + [
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "XAUUSD",
]


def get_phase1_symbols() -> list[str]:
    """Get the Phase 1 Forex symbol universe."""
    return PHASE1_FOREX_SYMBOLS.copy()


def get_extended_symbols() -> list[str]:
    """Get extended symbol universe for future phases."""
    return FUTURE_PHASE1_SYMBOLS.copy()