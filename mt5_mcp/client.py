"""Shared client access point using MT5 terminal for all symbols.

Supports dual-terminal routing:
  - Default (Prop): uses settings.mt5 config → FundingPips2-SIM
  - Deriv: set MT5_MCP_ACCOUNT=DERIV env var → DerivVU-Server

Each terminal gets its own MT5Client instance, keyed by account label.
"""

from __future__ import annotations

import os
import threading

from mt5_mcp.config import get_settings
from mt5_mcp.mt5_client import MT5Client

_clients: dict[str, MT5Client] = {}
_client_lock = threading.Lock()


def _account_key() -> str:
    """Return 'deriv', 'deriv_real', or 'prop' based on env var."""
    acct = os.getenv("MT5_MCP_ACCOUNT", "PROP").upper()
    if acct == "DERIV":
        return "deriv"
    if acct == "DERIV_REAL":
        return "deriv_real"
    return "prop"


def get_client(symbol: str | None = None):
    """Get MT5 client for the active account (Prop, Deriv demo, or Deriv real)."""
    key = _account_key()
    with _client_lock:
        if key not in _clients or _clients[key] is None:
            settings = get_settings()
            if key == "deriv":
                cfg = getattr(settings, "deriv_mt5", None)
                if cfg is None:
                    cfg = settings.mt5
            elif key == "deriv_real":
                cfg = getattr(settings, "deriv_real_mt5", None)
                if cfg is None:
                    raise RuntimeError(
                        "DERIV_REAL account not configured: add 'deriv_real_mt5' to settings.json"
                    )
            else:
                cfg = settings.mt5
            _clients[key] = MT5Client(cfg)
        return _clients[key]


def set_client(client: MT5Client | None) -> None:
    """Inject an MT5 client instance (used by tests)."""
    key = _account_key()
    with _client_lock:
        _clients[key] = client


def reset_client() -> None:
    """Reset all client instances."""
    with _client_lock:
        _clients.clear()


def get_deriv_client():
    """Deprecated: Deriv WebSocket client not used."""
    return None