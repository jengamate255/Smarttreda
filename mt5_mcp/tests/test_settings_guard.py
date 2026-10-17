"""settings_guard: clobber detection, critical-key validation, restore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import settings_guard as sg


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: dict) -> Path:
    mem = tmp_path / "memory"
    mem.mkdir()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    monkeypatch.setattr(sg, "SETTINGS", settings_path)
    monkeypatch.setattr(sg, "MEM", mem)
    monkeypatch.setattr(sg, "HASH_FILE", mem / "settings.sha256")
    monkeypatch.setattr(sg, "GOOD_COPY", mem / "settings.good.json")
    monkeypatch.setattr(sg, "_alert", lambda m: None)
    return settings_path


def _good_settings() -> dict:
    """A settings dict that passes the guard as written.

    Critical values are taken from ``sg.CRITICAL`` so retuning the live
    baseline (allowlist, risk, spread cap) cannot silently turn these tests
    into restore-instead-of-rebaseline cases.
    """
    settings: dict = {"other": {"kept": True}}
    for (section, key), expected in sg.CRITICAL.items():
        settings.setdefault(section, {})[key] = (
            list(expected) if isinstance(expected, list) else expected
        )
    return settings


def test_baseline_and_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _good_settings())
    assert sg.verify_settings() == "baselined"
    assert sg.verify_settings() == "ok"


def test_old_log_mode_good_copy_cannot_restore(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, _good_settings())
    assert sg.verify_settings() == "baselined"
    old = _good_settings()
    old["risk"]["reject_orders"] = False
    sg.GOOD_COPY.write_text(json.dumps(old), encoding="utf-8")
    path.write_text("{broken", encoding="utf-8")
    assert sg.verify_settings() == "unreadable"
    assert path.read_text() == "{broken"


def test_bad_first_baseline_is_not_trusted(tmp_path, monkeypatch):
    bad = _good_settings()
    bad["risk"]["reject_orders"] = False
    _setup(tmp_path, monkeypatch, bad)
    assert sg.verify_settings() == "unreadable"
    assert not sg.GOOD_COPY.exists()


def test_benign_change_rebaselines_and_alerts(tmp_path, monkeypatch):
    alerts: list[str] = []
    path = _setup(tmp_path, monkeypatch, _good_settings())
    monkeypatch.setattr(sg, "_alert", alerts.append)
    assert sg.verify_settings() == "baselined"

    data = json.loads(path.read_text(encoding="utf-8"))
    data["other"]["kept"] = False  # non-critical edit
    path.write_text(json.dumps(data), encoding="utf-8")

    assert sg.verify_settings() == "rebaselined"
    assert alerts and "critical" in alerts[0].lower() and "re-baselined" in alerts[0]
    assert sg.verify_settings() == "ok"


def test_clobbered_criticals_restored(tmp_path, monkeypatch):
    alerts: list[str] = []
    path = _setup(tmp_path, monkeypatch, _good_settings())
    monkeypatch.setattr(sg, "_alert", alerts.append)
    assert sg.verify_settings() == "baselined"
    good = path.read_bytes()

    data = json.loads(good.decode("utf-8"))
    data["risk"]["max_spread_points"] = 60
    data["consensus"]["allowed_symbols"] = ["NZDUSD"]
    path.write_text(json.dumps(data), encoding="utf-8")

    assert sg.verify_settings() == "restored"
    assert path.read_bytes() == good  # last-known-good restored
    assert alerts and "CORRUPTED" in alerts[0]
    assert sg.verify_settings() == "ok"


def test_unparseable_restored(tmp_path, monkeypatch):
    alerts: list[str] = []
    path = _setup(tmp_path, monkeypatch, _good_settings())
    monkeypatch.setattr(sg, "_alert", alerts.append)
    assert sg.verify_settings() == "baselined"
    good = path.read_bytes()

    path.write_text("{not json", encoding="utf-8")
    assert sg.verify_settings() == "restored"
    assert path.read_bytes() == good
