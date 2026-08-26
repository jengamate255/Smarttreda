"""Run the synthetic-index watcher against the Deriv terminal profile."""

from __future__ import annotations

import os

os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from trade_watch import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
