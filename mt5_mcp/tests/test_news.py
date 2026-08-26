from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from mt5_mcp.common import NewsEvent
from mt5_mcp.tools.news import ForexFactoryNewsProvider


def test_recent_cached_news_is_used_during_feed_failure():
    provider = ForexFactoryNewsProvider()
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
