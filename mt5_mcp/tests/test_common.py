from common import pip_size, pip_value_per_lot


def test_pip_size_fx_5digit():
    assert pip_size({"digits": 5, "point": 1e-5}) == 0.0001


def test_pip_size_fx_4digit():
    assert pip_size({"digits": 4, "point": 1e-4}) == 0.0001


def test_pip_size_jpy_3digit():
    assert pip_size({"digits": 3, "point": 0.001}) == 0.01


def test_pip_size_index_2digit():
    assert pip_size({"digits": 2, "point": 0.01}) == 1.0


def test_pip_size_7digit():
    assert abs(pip_size({"digits": 7, "point": 1e-7}) - 0.0001) < 1e-12


def test_pip_value_per_lot_fx():
    info = {"digits": 5, "point": 1e-5, "trade_tick_size": 1e-5, "trade_tick_value": 1.0}
    assert abs(pip_value_per_lot(info) - 10.0) < 1e-9


def test_pip_value_per_lot_index():
    info = {"digits": 2, "point": 0.01, "trade_tick_size": 0.25, "trade_tick_value": 5.0}
    assert abs(pip_value_per_lot(info) - 20.0) < 1e-9
