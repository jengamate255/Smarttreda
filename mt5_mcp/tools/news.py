"""News filter tool.

Pluggable provider design so real economic calendars can be added later.
The default provider parses the public ForexFactory weekly calendar RSS
(nfs.faireconomy.media); a stub is kept as the fallback when the feed is
unreachable. Strategy calls can consult ``high_impact_imminent``.

To add another provider, subclass ``NewsProvider`` and swap it in
``set_provider()`` - nothing else needs to change.
"""

from __future__ import annotations

import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from common import NewsEvent

logger = logging.getLogger("mt5_mcp.news")


class NewsProvider(ABC):
    @abstractmethod
    def upcoming(self, hours: int = 24, currency: str = "") -> list[NewsEvent]: ...


class StubNewsProvider(NewsProvider):
    """No live calendar configured - returns empty events."""

    def upcoming(self, hours: int = 24, currency: str = "") -> list[NewsEvent]:
        return []


def _eastern_utc_offset(dt: datetime) -> timedelta:
    """US Eastern time offset (EDT -4h in DST, EST -5h otherwise)."""
    d = datetime(dt.year, 3, 8, 2)
    while d.weekday() != 6:
        d += timedelta(days=1)
    start_dst = d
    d = datetime(dt.year, 11, 1, 2)
    while d.weekday() != 6:
        d += timedelta(days=1)
    end_dst = d
    if start_dst <= dt < end_dst:
        return timedelta(hours=-4)
    return timedelta(hours=-5)


class ForexFactoryNewsProvider(NewsProvider):
    """Parses the public ForexFactory weekly calendar RSS.

    Feed covers the current week only and times are in US Eastern time;
    they are converted to UTC before being returned."""

    FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    CACHE_TTL = 900.0
    FAILURE_TTL = 180.0
    _cache: tuple[float, list[NewsEvent]] | None = None
    _failure_until: float = 0.0

    def _fetch(self) -> list[NewsEvent]:
        now = time.time()
        if self._cache is not None and now - self._cache[0] < self.CACHE_TTL:
            return self._cache[1]
        if now < self._failure_until:
            return []
        raw = b""
        try:
            req = urllib.request.Request(  # noqa: S310 - public calendar feed
                self.FEED_URL, headers={"User-Agent": "Mozilla/5.0 (MT5 dashboard)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - public calendar feed
                raw = resp.read()
        except Exception as exc:  # noqa: BLE001 - offline fallback
            logger.warning("news feed unavailable: %s", exc)
            self._failure_until = now + self.FAILURE_TTL
            return []

        events: list[NewsEvent] = []
        try:
            root = ET.fromstring(raw)  # noqa: S314 - public calendar XML, no DTD
            for el in root.findall("event"):
                title = (el.findtext("title") or "").strip()
                country = (el.findtext("country") or "").strip()
                date_s = (el.findtext("date") or "").strip()
                time_s = (el.findtext("time") or "").strip()
                impact = (el.findtext("impact") or "Low").strip().lower()
                forecast = (el.findtext("forecast") or "").strip()
                previous = (el.findtext("previous") or "").strip()
                if not title or not date_s or not time_s:
                    continue
                try:
                    local = datetime.strptime(f"{date_s} {time_s}", "%m-%d-%Y %I:%M%p")
                except ValueError:
                    continue
                dt = local - _eastern_utc_offset(local)
                dt = dt.replace(tzinfo=timezone.utc)
                events.append(
                    NewsEvent(
                        time=dt,
                        currency=country,
                        impact=impact if impact in ("low", "medium", "high") else "low",
                        title=title,
                        forecast=forecast,
                        previous=previous,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - malformed feed
            logger.warning("news feed parse failed: %s", exc)
            events = []
        events.sort(key=lambda e: e.time)
        self._cache = (now, events)
        return events

    def upcoming(self, hours: int = 24, currency: str = "") -> list[NewsEvent]:
        events = self._fetch()
        now = datetime.now(timezone.utc)
        until = now + timedelta(hours=hours)
        out = [e for e in events if now <= e.time <= until]
        if currency:
            out = [e for e in out if e.currency.upper() == currency.upper()]
        return out


_provider: NewsProvider = ForexFactoryNewsProvider()


def set_provider(provider: NewsProvider) -> None:
    global _provider
    _provider = provider


def get_provider() -> NewsProvider:
    return _provider


def news_filter(horizon_hours: int = 24, currency: str = "") -> dict:
    events = get_provider().upcoming(horizon_hours, currency)
    high_impact = [e for e in events if e.impact == "high"]
    now = datetime.now(timezone.utc)
    imminent = [e for e in high_impact if 0 <= (e.time - now).total_seconds() <= 3600]
    return {
        "provider": type(get_provider()).__name__,
        "events": events,
        "high_impact_count": len(high_impact),
        "high_impact_imminent": len(imminent) > 0,
        "imminent_events": imminent,
        "trading_blocked": len(imminent) > 0,
        "note": "ForexFactory weekly calendar (UTC, converted from ET)",
    }


def register(mcp) -> None:
    @mcp.tool(
        name="news_filter",
        description="Upcoming economic events and whether high-impact news blocks trading in the next hour.",
    )
    def tool_news_filter(horizon_hours: int = 24, currency: str = "") -> dict:
        return news_filter(horizon_hours, currency)
