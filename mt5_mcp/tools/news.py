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
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mt5_mcp.common import NewsEvent

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
    DISK_CACHE_TTL = 6 * 3600.0
    CACHE_PATH = Path(__file__).resolve().parents[1] / "memory" / "news_cache.json"
    _cache: tuple[float, list[NewsEvent]] | None = None
    _failure_until: float = 0.0
    _cache_usable: bool = False
    _last_error: str = ""

    def _load_disk_cache(self) -> list[NewsEvent] | None:
        try:
            payload = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            saved_at = float(payload.get("saved_at", 0.0))
            if not saved_at or time.time() - saved_at > self.DISK_CACHE_TTL:
                return None
            events = [
                NewsEvent(
                    time=datetime.fromisoformat(item["time"]),
                    currency=str(item.get("currency", "")),
                    impact=str(item.get("impact", "low")),
                    title=str(item.get("title", "")),
                    forecast=str(item.get("forecast", "")),
                    previous=str(item.get("previous", "")),
                )
                for item in payload.get("events", [])
            ]
            self._cache = (saved_at, events)
            self._cache_usable = True
            return events
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _save_disk_cache(self, saved_at: float, events: list[NewsEvent]) -> None:
        try:
            self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.CACHE_PATH.write_text(
                json.dumps(
                    {
                        "saved_at": saved_at,
                        "events": [
                            {
                                "time": event.time.isoformat(),
                                "currency": event.currency,
                                "impact": event.impact,
                                "title": event.title,
                                "forecast": event.forecast,
                                "previous": event.previous,
                            }
                            for event in events
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("news cache write failed: %s", exc)

    def _fetch(self) -> list[NewsEvent]:
        now = time.time()
        if self._cache is not None and now - self._cache[0] < self.CACHE_TTL:
            return self._cache[1]
        if now < self._failure_until:
            return self._cache[1] if self._cache_usable and self._cache else []
        raw = b""
        try:
            req = urllib.request.Request(  # noqa: S310 - public calendar feed
                self.FEED_URL, headers={"User-Agent": "Mozilla/5.0 (MT5 dashboard)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - public calendar feed
                raw = resp.read()
        except Exception as exc:  # noqa: BLE001 - offline fallback
            logger.warning("news feed unavailable: %s", exc)
            self._last_error = str(exc)
            self._failure_until = now + self.FAILURE_TTL
            return self._load_disk_cache() or []

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
            self._last_error = str(exc)
            self._failure_until = now + self.FAILURE_TTL
            return self._load_disk_cache() or []
        events.sort(key=lambda e: e.time)
        self._cache = (now, events)
        self._cache_usable = True
        self._last_error = ""
        self._failure_until = 0.0
        self._save_disk_cache(now, events)
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
        "cached_data_usable": bool(getattr(get_provider(), "_cache_usable", False)),
        "provider_error": str(getattr(get_provider(), "_last_error", "") or ""),
        "note": "ForexFactory weekly calendar (UTC, converted from ET)",
    }


def register(mcp) -> None:
    @mcp.tool(
        name="news_filter",
        description="Upcoming economic events and whether high-impact news blocks trading in the next hour.",
    )
    def tool_news_filter(horizon_hours: int = 24, currency: str = "") -> dict:
        return news_filter(horizon_hours, currency)
