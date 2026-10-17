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
import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger("mt5_mcp.settings_guard")

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "settings.json"
MEM = ROOT / "memory"
HASH_FILE = MEM / "settings.sha256"
GOOD_COPY = MEM / "settings.good.json"

# Quality mode (2026-10-13): enforce existing loss-review restrictions.
# Spread never blocks, per user policy; sizing still accounts for spread.
# Preserve the narrowed allowlist and outcome/prop risk ceilings.
ALLOWLIST = [
    "USOIL", "AUDUSD", "BTCUSD", "ETHUSD", "SPX500", "XAUUSD",
]
CRITICAL: dict[tuple[str, str], object] = {
    ("risk", "max_spread_points"): 0,
    ("risk", "max_spread_relative"): 0,
    ("risk", "reject_orders"): True,
    ("quality", "enabled"): True,
    ("risk", "confidence_floor_percent"): 80,
    ("risk", "max_trades_per_day"): 2,
    ("risk", "max_daily_loss_percent"): 4.0,
    ("risk", "max_risk_percent"): 1.0,
    ("risk", "conservative_risk_percent"): 0.1,
    ("risk", "max_drawdown_percent"): 8.0,
    ("performance_risk", "enabled"): True,
    ("performance_risk", "mode"): "rolling",
    ("performance_risk", "window"): 20,
    ("performance_risk", "min_trades"): 20,
    ("performance_risk", "losing_percent"): 0.1,
    ("performance_risk", "winning_percent"): 1.0,
    ("prop_risk", "enabled"): True,
    ("prop_risk", "account_login"): 11911448,
    ("prop_risk", "server"): "FundingPips2-SIM",
    ("prop_risk", "initial_balance"): 10000.0,
    ("prop_risk", "reset_utc_offset_seconds"): 10800,
    ("prop_risk", "safety_buffer_percent"): 0.1,
    ("risk", "allowed_hours_utc"): [0, 2, 7, 8, 9, 17, 23],
    ("risk", "blocked_setup_prefixes"): ["desk-", "desk:"],
    ("consensus", "risk_percent"): 1.0,
    ("consensus", "allowed_symbols"): ALLOWLIST,
}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rollback_candidate(data: dict) -> dict:
    """Offline candidate only: preserve every unrelated setting and credential.

    Never call verify_settings here: that routine mutates live guard files and
    can notify Telegram. Environment overrides require separate manual review.
    """
    candidate = copy.deepcopy(data)
    candidate.setdefault("risk", {})["reject_orders"] = True
    candidate.setdefault("forward_test", {})["expires_at"] = 0
    return candidate


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
        try:
            if _criticals(json.loads(SETTINGS.read_text(encoding="utf-8"))):
                return "unreadable"
        except (ValueError, TypeError, AttributeError):
            return "unreadable"
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
        # A historical Oct 9 log-mode copy must not undo the current strict
        # policy on restoration. Leave the files untouched if it is unsafe.
        try:
            if _criticals(json.loads(GOOD_COPY.read_text(encoding="utf-8"))):
                logger.error("settings guard: known-good copy fails current criticals")
                return "unreadable"
        except (ValueError, TypeError, AttributeError):
            return "unreadable"
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
