from common import BOOM_CRASH_SYMBOLS
from tools.boom_crash_strategy import MT5_SYMBOL_MAP, mt5_symbol


EXPECTED = {
    *(f"BOOM{n}" for n in (1000, 900, 600, 500, 300, 200, 150, 100, 99, 50)),
    *(f"CRASH{n}" for n in (1000, 900, 600, 500, 300, 200, 150, 100, 99, 50)),
}


def test_all_standard_boom_crash_symbols_are_mapped():
    assert set(BOOM_CRASH_SYMBOLS) == EXPECTED
    assert all(symbol in MT5_SYMBOL_MAP for symbol in BOOM_CRASH_SYMBOLS)
    assert all(mt5_symbol(symbol).endswith(" Index") for symbol in BOOM_CRASH_SYMBOLS)
