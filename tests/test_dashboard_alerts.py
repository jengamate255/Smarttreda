import web_account


def test_stored_alerts_do_not_require_broker_quotes(monkeypatch):
    from mt5_mcp import alerts

    stored = [{"id": "saved", "symbol": "EURUSD", "price": 1.1}]
    monkeypatch.setattr(alerts, "list_alerts", lambda include_triggered: stored)

    def unavailable_quote(symbol):
        raise AssertionError("stored alert listing must not call the broker")

    monkeypatch.setattr(web_account, "_tick_quote_fn", unavailable_quote)
    assert web_account.get_alerts(include_triggered=True, include_prices=False) == {"alerts": stored}
