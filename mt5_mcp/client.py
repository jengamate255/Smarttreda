"""Shared MT5 client access point.

Tools get their client from here, which keeps the architecture ready for
multi-account support: swap in a registry keyed by account id later.
"""

from __future__ import annotations

import threading

from mt5_mcp.config import get_settings
from mt5_mcp.mt5_client import MT5Client

_client: MT5Client | None = None
_client_lock = threading.Lock()


def get_client() -> MT5Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = MT5Client(get_settings().mt5)
        return _client


def set_client(client: MT5Client | None) -> None:
    """Inject a client instance (used by tests and multi-account setups)."""
    global _client
    with _client_lock:
        _client = client


def reset_client() -> None:
    set_client(None)
