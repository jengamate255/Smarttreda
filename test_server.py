from server import AccountInfo, MT5Config, MT5Connection, PositionInfo, SymbolInfo


def test_mt5_config_defaults(monkeypatch):
    for name in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH"):
        monkeypatch.delenv(name, raising=False)
    config = MT5Config()
    assert config.login == 0
    assert config.password == ""
    assert config.server == ""
    assert config.path == ""
    assert not config.is_configured()


def test_mt5_config_with_env(monkeypatch):
    monkeypatch.setenv("MT5_LOGIN", "12345")
    monkeypatch.setenv("MT5_PASSWORD", "testpass")
    monkeypatch.setenv("MT5_SERVER", "TestServer")
    
    config = MT5Config()
    assert config.login == 12345
    assert config.password == "testpass"
    assert config.server == "TestServer"
    assert config.is_configured()


def test_mt5_connection_singleton():
    conn1 = MT5Connection()
    conn2 = MT5Connection()
    assert conn1 is conn2


def test_symbol_info_model():
    symbol = SymbolInfo(
        name="EURUSD",
        bid=1.0850,
        ask=1.0852,
        spread=2,
        point=0.00001,
        digits=5,
        trade_mode=0,
    )
    assert symbol.name == "EURUSD"
    assert symbol.spread == 2


def test_account_info_model():
    account = AccountInfo(
        login=12345,
        balance=10000.0,
        equity=10100.0,
        margin=500.0,
        free_margin=9600.0,
        margin_level=2020.0,
        currency="USD",
        leverage=100,
    )
    assert account.login == 12345
    assert account.currency == "USD"


def test_position_info_model():
    position = PositionInfo(
        ticket=123456789,
        symbol="EURUSD",
        type=0,
        volume=0.1,
        price_open=1.0850,
        sl=1.0800,
        tp=1.0900,
        price_current=1.0860,
        profit=10.0,
        swap=0.0,
        comment="Test",
        magic=234000,
    )
    assert position.ticket == 123456789
    assert position.symbol == "EURUSD"