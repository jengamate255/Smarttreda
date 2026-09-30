from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from mt5_mcp.common import NewsEvent
from mt5_mcp.tools import news as news_module
from mt5_mcp.tools.news import ForexFactoryNewsProvider, news_status


def _event(minutes: int = 10, title: str = "Cached event") -> NewsEvent:
    return NewsEvent(
        time=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        currency="USD",
        impact="high",
        title=title,
    )


def _write_cache(path, saved_at: float, events: list[NewsEvent]) -> None:
    path.write_text(
        json.dumps(
            {
                "saved_at": saved_at,
                "events": [
                    {
                        "time": e.time.isoformat(),
                        "currency": e.currency,
                        "impact": e.impact,
                        "title": e.title,
                        "forecast": e.forecast,
                        "previous": e.previous,
                    }
                    for e in events
                ],
            }
        ),
        encoding="utf-8",
    )


def _no_network(*args, **kwargs):
    raise AssertionError("network fetch attempted despite a usable disk cache")


def test_recent_cached_news_is_used_during_feed_failure(tmp_path, monkeypatch):
    provider = ForexFactoryNewsProvider()
    # hermetic: no shared disk cache, so only this process's memory cache is left
    monkeypatch.setattr(provider, "CACHE_PATH", tmp_path / "missing_cache.json")
    event = NewsEvent(
        time=datetime.now(timezone.utc) + timedelta(minutes=10),
        currency="USD",
        impact="high",
        title="Cached event",
    )
    provider._cache = (time.time() - provider.CACHE_TTL - 1, [event])
    provider._cache_usable = True
    provider._failure_until = time.time() + 60

    events = provider.upcoming(hours=1)

    assert events == [event]
    assert provider._source == "memory"


def test_fresh_disk_cache_is_read_before_any_network_call(tmp_path, monkeypatch):
    provider = ForexFactoryNewsProvider()
    cache = tmp_path / "news_cache.json"
    monkeypatch.setattr(provider, "CACHE_PATH", cache)
    monkeypatch.setattr(news_module, "_provider", provider)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    event = _event()
    _write_cache(cache, time.time() - 60, [event])

    events = provider.upcoming(hours=1)

    assert events == [event]
    assert provider._source == "disk"
    assert news_status()["news_stale"] is False
    assert news_status()["news_source"] == "disk"


def test_stale_disk_cache_is_used_when_the_feed_is_rate_limited(tmp_path, monkeypatch):
    provider = ForexFactoryNewsProvider()
    cache = tmp_path / "news_cache.json"
    monkeypatch.setattr(provider, "CACHE_PATH", cache)

    def _raise(*args, **kwargs):
        raise urllib.error.URLError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    event = _event(minutes=30, title="Old-but-usable")
    # older than DISK_CACHE_TTL (needs network) but inside STALE_GRACE_TTL
    _write_cache(cache, time.time() - provider.DISK_CACHE_TTL - 300, [event])

    events = provider.upcoming(hours=1)

    assert events == [event]
    assert provider._source == "disk-stale"
    assert provider._failure_until > time.time()
    assert provider._last_error


def test_retry_after_header_sets_the_backoff_window():
    provider = ForexFactoryNewsProvider()
    provider._fail_count = 0

    provider._note_failure(700.0)

    delay = provider._failure_until - time.time()
    assert 700.0 * 0.8 <= delay <= 700.0 * 1.2


def test_backoff_grows_on_repeated_failures():
    provider = ForexFactoryNewsProvider()
    provider._fail_count = 0

    provider._note_failure()
    first = provider._failure_until - time.time()
    provider._note_failure()
    second = provider._failure_until - time.time()

    assert second > first
    assert second <= provider.BACKOFF_MAX * 1.2


def test_disk_cache_write_is_atomic(tmp_path, monkeypatch):
    provider = ForexFactoryNewsProvider()
    cache = tmp_path / "news_cache.json"
    monkeypatch.setattr(provider, "CACHE_PATH", cache)
    event = _event()

    provider._save_disk_cache(time.time(), [event])

    assert cache.exists()
    assert not cache.with_suffix(".json.tmp").exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["events"][0]["title"] == event.title


def test_news_status_reports_missing_data_as_stale(monkeypatch):
    provider = ForexFactoryNewsProvider()
    provider._cache = None
    provider._cache_usable = False
    provider._data_saved_at = 0.0
    monkeypatch.setattr(news_module, "_provider", provider)

    status = news_status()

    assert status["news_stale"] is True
    assert status["news_source"] == "none"
    assert status["news_data_age_sec"] is None
