"""Slow broker compliance calls must not block the dashboard event loop."""
import asyncio
import threading


def test_websocket_compliance_runs_off_event_loop(monkeypatch):
    import web_dashboard as dashboard
    import mt5_mcp.compliance as compliance

    async def scenario():
        started = asyncio.Event()
        finished = threading.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def slow_compliance():
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=2)
            finished.set()
            return {}

        monkeypatch.setattr(compliance, "evaluate", slow_compliance)
        monkeypatch.setattr(dashboard, "_summary_snapshot", lambda: {"connected": True, "positions": []})
        monkeypatch.setattr(dashboard, "_ws_clients", {object()})
        task = asyncio.create_task(dashboard._ws_broadcast_loop())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert not finished.is_set(), "event loop was blocked until compliance finished"
        finally:
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_websocket_breakout_runs_off_event_loop(monkeypatch):
    import web_dashboard as dashboard
    import mt5_mcp.compliance as compliance

    async def scenario():
        started = asyncio.Event()
        finished = threading.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def slow_breakout():
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=2)
            finished.set()
            return []

        client = object()
        monkeypatch.setattr(compliance, "evaluate", lambda: {})
        monkeypatch.setattr(dashboard, "_breakout_snapshot", slow_breakout)
        monkeypatch.setattr(dashboard, "_summary_snapshot", lambda: {"connected": True, "positions": []})
        monkeypatch.setattr(dashboard, "_ws_clients", {client})
        monkeypatch.setattr(dashboard, "_ws_subscriptions", {client: {"forex-breakout"}})
        task = asyncio.create_task(dashboard._ws_broadcast_loop())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert not finished.is_set(), "broker scan blocked the HTTP event loop"
        finally:
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
