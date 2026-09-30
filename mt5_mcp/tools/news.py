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
import os
import random
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


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds form only)."""
    if not value:
        return None
    try:
        return max(float(str(value).strip()), 0.0)
    except (TypeError, ValueError):
        return None


class ForexFactoryNewsProvider(NewsProvider):
    """Parses the public ForexFactory weekly calendar RSS.

    Feed covers the current week only and times are in US Eastern time;
    they are converted to UTC before being returned.

    Several long-lived processes share this provider (desk, confirm, breakout,
    analysis, dashboards, auto_trader), so the feed is read from the shared disk
    cache first and only fetched over the network when that cache is older than
    ``DISK_CACHE_TTL``. That keeps request volume at ~1 per process per TTL
    instead of one per memory-cache expiry, which is what triggered the
    provider's HTTP 429 responses.
    """

    FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    CACHE_TTL = 900.0
    FAILURE_TTL = 180.0
    DISK_CACHE_TTL = 6 * 3600.0
    # On a network failure, fall back to disk data this old rather than
    # pretending the calendar is empty (old events are filtered out by
    # ``upcoming()`` anyway - only this week's window can match).
    STALE_GRACE_TTL = 7 * 86400.0
    BACKOFF_MAX = 3600.0
    CACHE_PATH = Path(__file__).resolve().parents[1] / "memory" / "news_cache.json"
    _cache: tuple[float, list[NewsEvent]] | None = None
    _failure_until: float = 0.0
    _cache_usable: bool = False
    _last_error: str = ""
    _source: str = "none"
    _data_saved_at: float = 0.0
    _fail_count: int = 0

    def _load_disk_cache(self, max_age: float | None = DISK_CACHE_TTL) -> list[NewsEvent] | None:
        try:
            payload = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            saved_at = float(payload.get("saved_at", 0.0))
            age = time.time() - saved_at
            if not saved_at or (max_age is not None and age > max_age):
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
            self._cache = (time.time(), events)
            self._cache_usable = True
            self._data_saved_at = saved_at
            self._source = "disk" if age <= self.DISK_CACHE_TTL else "disk-stale"
            return events
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _save_disk_cache(self, saved_at: float, events: list[NewsEvent]) -> None:
        payload = json.dumps(
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
        )
        # Atomic replace: the file is shared by every service process, and a
        # plain write_text can be read half-written by a sibling.
        tmp_path = self.CACHE_PATH.with_suffix(".json.tmp")
        try:
            self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self.CACHE_PATH)
        except OSError as exc:
            logger.warning("news cache write failed: %s", exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _note_failure(self, retry_after: float | None = None) -> None:
        """Back off after a failed fetch (honours Retry-After when present)."""
        self._fail_count += 1
        delay = self.FAILURE_TTL * (2 ** min(self._fail_count - 1, 5))
        delay = min(delay, self.BACKOFF_MAX)
        if retry_after and retry_after > 0:
            delay = max(delay, min(retry_after, self.BACKOFF_MAX))
        # jitter so sibling processes do not retry in lockstep
        delay *= 1.0 + (random.random() * 0.4 - 0.2)
        self._failure_until = time.time() + delay

    def _fallback_to_disk(self) -> list[NewsEvent]:
        """Best available data when the network is unusable.

        Preference: shared disk cache (possibly stale) > this process's memory
        cache > nothing. Returning [] is what makes blackout checks go blind,
        so every other option is tried first.
        """
        stale = self._load_disk_cache(self.STALE_GRACE_TTL)
        if stale is not None:
            return stale
        if self._cache_usable and self._cache:
            self._source = "memory"
            return self._cache[1]
        self._source = "none"
        return []

    def _fetch(self) -> list[NewsEvent]:
        now = time.time()
        # 1. in-process cache
        if self._cache is not None and now - self._cache[0] < self.CACHE_TTL:
            return self._cache[1]
        # 2. shared disk cache first - never hit the network just because this
        #    process restarted or its memory cache expired.
        disk = self._load_disk_cache()
        if disk is not None:
            return disk
        # 3. inside the failure backoff window
        if now < self._failure_until:
            return self._fallback_to_disk()
        # 4. network
        raw = b""
        retry_after: float | None = None
        try:
            req = urllib.request.Request(  # noqa: S310 - public calendar feed
                self.FEED_URL, headers={"User-Agent": "Mozilla/5.0 (MT5 dashboard)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - public calendar feed
                raw = resp.read()
                retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
        except Exception as exc:  # noqa: BLE001 - offline fallback
            logger.warning("news feed unavailable: %s", exc)
            self._last_error = str(exc)
            self._note_failure(retry_after)
            return self._fallback_to_disk()

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
            self._note_failure()
            return self._fallback_to_disk()
        events.sort(key=lambda e: e.time)
        self._cache = (time.time(), events)
        self._cache_usable = True
        self._data_saved_at = time.time()
        self._source = "network"
        self._last_error = ""
        self._failure_until = 0.0
        self._fail_count = 0
        self._save_disk_cache(time.time(), events)
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


def news_status() -> dict:
    """Provider health without triggering a fetch.

    ``stale`` is True whenever the calendar cannot be trusted (never loaded, or
    older than ``DISK_CACHE_TTL``) - callers should surface it instead of
    reporting a clean "no news" verdict.
    """
    prov = get_provider()
    saved_at = float(getattr(prov, "_data_saved_at", 0.0) or 0.0)
    usable = bool(getattr(prov, "_cache_usable", False))
    ttl = float(getattr(prov, "DISK_CACHE_TTL", 6 * 3600.0))
    age = (time.time() - saved_at) if saved_at else None
    retry_in = max(0.0, float(getattr(prov, "_failure_until", 0.0) or 0.0) - time.time())
    return {
        "news_source": str(getattr(prov, "_source", "none") or "none") if usable else "none",
        "news_data_age_sec": round(age, 1) if age is not None else None,
        "news_stale": (not usable) or age is None or age > ttl,
        "news_retry_in_sec": round(retry_in, 1),
        "news_provider_error": str(getattr(prov, "_last_error", "") or ""),
    }


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
        **news_status(),
        "note": "ForexFactory weekly calendar (UTC, converted from ET)",
    }


def register(mcp) -> None:
    @mcp.tool(
        name="news_filter",
        description="Upcoming economic events and whether high-impact news blocks trading in the next hour.",
    )
    def tool_news_filter(horizon_hours: int = 24, currency: str = "") -> dict:
        return news_filter(horizon_hours, currency)
