"""settings.json clobber protection (unknown-writer incident 2026-09-30).

verify_settings() compares sha256(settings.json) against a stored baseline:

- match        -> "ok"
- no baseline  -> baseline current file + refresh known-good copy
- mismatch     -> validate CRITICAL keys:
    - intact    -> alert (Telegram), re-baseline, refresh known-good copy
    - broken    -> restore known-good copy over settings.json, alert
- unparseable  -> restore known-good copy, alert

Called every cycle from analysis_agent.main(); alerts via mt5_mcp.notify.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger("mt5_mcp.settings_guard")

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "settings.json"
MEM = ROOT / "memory"
HASH_FILE = MEM / "settings.sha256"
GOOD_COPY = MEM / "settings.good.json"

# The values this trading stack was retuned to (2026-09-27..10-01 sessions).
# If any of these differ, the file was clobbered by an unknown writer.
ALLOWLIST = [
    "EURUSD", "EURJPY", "GBPJPY", "USDJPY", "SPX500", "XAUUSD", "ETHUSD",
    "USDCAD", "GBPUSD", "USDCHF", "BTCUSD",
]
CRITICAL: dict[tuple[str, str], object] = {
    ("risk", "max_spread_points"): 0,
    ("risk", "confidence_floor_percent"): 80,
    ("risk", "max_trades_per_day"): 2,
    ("risk", "max_daily_loss_percent"): 4.0,
    ("risk", "max_risk_percent"): 1.0,
    ("consensus", "allowed_symbols"): ALLOWLIST,
}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _alert(message: str) -> None:
    try:
        from mt5_mcp.notify import notify
        notify("settings_guard", message, force=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings guard alert failed: %s", exc)


def _criticals(data: dict) -> list[str]:
    bad: list[str] = []
    for (section, key), expected in CRITICAL.items():
        actual = (data.get(section) or {}).get(key)
        if actual != expected:
            bad.append(f"{section}.{key}={actual!r} (want {expected!r})")
    return bad


def verify_settings() -> str:
    """One integrity check. Returns ok|baselined|rebaselined|restored|unreadable."""
    MEM.mkdir(parents=True, exist_ok=True)
    try:
        current_hash = _hash(SETTINGS)
    except Exception as exc:  # noqa: BLE001
        logger.error("settings guard: cannot read settings.json: %s", exc)
        return "unreadable"

    try:
        baseline = HASH_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        baseline = ""

    if not baseline:
        HASH_FILE.write_text(current_hash, encoding="utf-8")
        GOOD_COPY.write_bytes(SETTINGS.read_bytes())
        logger.info("settings guard: baselined settings.json (%s)", current_hash[:8])
        return "baselined"

    if current_hash == baseline:
        return "ok"

    parse_err = ""
    data: dict | None = None
    try:
        loaded = json.loads(SETTINGS.read_text(encoding="utf-8"))
        data = loaded if isinstance(loaded, dict) else None
        if data is None:
            parse_err = "top-level JSON is not an object"
    except Exception as exc:  # noqa: BLE001
        parse_err = str(exc)

    bad = _criticals(data) if data is not None else [f"unparseable: {parse_err}"]

    if not bad:
        HASH_FILE.write_text(current_hash, encoding="utf-8")
        GOOD_COPY.write_bytes(SETTINGS.read_bytes())
        logger.warning(
            "settings guard: external change with intact criticals, re-baselined (%s)",
            current_hash[:8],
        )
        _alert(
            "settings.json changed externally (sha %s); critical values intact - "
            "re-baselined. Review the file when convenient." % current_hash[:8]
        )
        return "rebaselined"

    if GOOD_COPY.exists():
        SETTINGS.write_bytes(GOOD_COPY.read_bytes())
        logger.error("settings guard: restored settings.json; bad keys: %s", "; ".join(bad))
        _alert(
            "settings.json CORRUPTED by unknown writer (%s) - restored last-known-good copy."
            % "; ".join(bad[:3])
        )
        return "restored"

    logger.error("settings guard: corrupted and no good copy: %s", "; ".join(bad))
    _alert("settings.json corrupted (%s) and no known-good copy exists!" % "; ".join(bad[:3]))
    return "unreadable"
