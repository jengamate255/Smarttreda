"""MT5 Gateway: the single process that owns the terminal connection.

Every other service (web dashboard, trade desk, watch daemon, telegram
bot, research scripts) talks to this gateway over HTTP instead of
opening its own MetaTrader5 IPC connection. This fixes the contention
class of bugs we hit repeatedly:

* IPC timeouts when 5 processes initialize against one terminal
* Session-0 terminal respawns fighting the Console-session terminal
* order_send returning empty results under concurrent access
* 5-minute scan stalls while processes wait on each other's locks

Protocol: JSON over HTTP, bearer-token auth (same token as dashboard).
When the gateway is unreachable, clients transparently fall back to a
direct in-process MT5Client, so scripts keep working standalone.

Run:  python mt5_gateway.py            (port 8060)
      scheduled task "MT5 Gateway" (at-logon, hidden via desk_service.vbs)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "mt5_mcp"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_DIR / ".env")

# The gateway owns the terminal directly — never proxy to itself.
os.environ["MT5_GATEWAY"] = "0"

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
import uvicorn  # noqa: E402

from mt5_mcp.client import get_client  # noqa: E402

LOG_PATH = PROJECT_DIR / "logs" / "mt5_gateway.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("mt5_gateway")

app = FastAPI(title="MT5 Gateway", docs_url=None, redoc_url=None)

# Method registry: name -> (client callable, arg names, allow_empty_body)
_METHODS: dict[str, tuple] = {}

# ---- request queue: one worker, bounded, with backpressure ----------------
# The terminal is single-threaded IPC; concurrent calls inside one process
# caused IPC timeouts and event-loop freezes during heavy backtests. All
# /call work now runs on a dedicated worker thread with a bounded queue.
# When the queue is full (burst overload), callers get 503 immediately
# instead of hanging the terminal.

import queue as _queue  # noqa: E402
import threading as _threading  # noqa: E402

_WORK_Q: _queue.Queue = _queue.Queue(maxsize=50)
_QUEUE_STATS = {"depth": 0, "rejected": 0}


def _queue_worker() -> None:
    while True:
        fn, kwargs, done = _WORK_Q.get()
        try:
            done["result"] = fn(**kwargs)
        except BaseException as exc:  # noqa: BLE001
            done["error"] = exc
        finally:
            done["event"].set()
            _QUEUE_STATS["depth"] = _WORK_Q.qsize()


_threading.Thread(target=_queue_worker, name="mt5-ipc", daemon=True).start()


def _run_queued(fn, kwargs, timeout_s: float = 180.0):
    """Execute fn(kwargs) on the IPC worker; 503 on full queue, 504 on stall."""
    import time as _t
    if _WORK_Q.qsize() >= _WORK_Q.maxsize:
        _QUEUE_STATS["rejected"] += 1
        raise HTTPException(status_code=503,
                            detail="gateway overloaded — retry after backoff")
    done = {"event": _threading.Event()}
    _WORK_Q.put((fn, kwargs, done))
    _QUEUE_STATS["depth"] = _WORK_Q.qsize()
    if not done["event"].wait(timeout_s):
        raise HTTPException(status_code=504, detail="gateway worker stalled")
    if "error" in done:
        raise done["error"]
    return done["result"]


def _register(name: str, arg_names: list[str]):
    def deco(fn):
        _METHODS[name] = (fn, arg_names)
        return fn
    return deco


def _auth(request: Request) -> None:
    token = os.getenv("WEB_AUTH_TOKEN", "")
    if not token:
        return  # dev mode, no auth configured
    supplied = request.headers.get("X-Auth-Token", "")
    if supplied != token:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict:
    try:
        c = get_client()
        c.ensure_connected()
        info = c.account_info()
        return {
            "gateway": "ok",
            "connected": True,
            "login": info.get("login"),
            "server": info.get("server"),
            "uptime_s": round(time.time() - _START_TS, 1),
            "qdepth": _QUEUE_STATS["depth"],
            "q_rejected": _QUEUE_STATS["rejected"],
        }
    except Exception as exc:  # noqa: BLE001
        return {"gateway": "ok", "connected": False, "error": str(exc)[:200],
                "qdepth": _QUEUE_STATS["depth"]}


@app.post("/call/{method}")
async def call(method: str, request: Request) -> dict:
    _auth(request)
    spec = _METHODS.get(method)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown method {method}")
    fn, arg_names = spec
    try:
        body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    kwargs = {k: body.get(k) for k in arg_names if k in body}
    try:
        result = _run_queued(fn, kwargs)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:500])
    # FastAPI can serialize dicts/lists/bools/numbers; DataFrame results are
    # converted by the registered wrappers before returning.
    return {"ok": True, "data": result, "qdepth": _QUEUE_STATS["depth"]}


# ---- registered client methods -------------------------------------------

def _install_methods() -> None:
    c = get_client()

    def wrap_simple(name, arg_names=()):
        fn = getattr(c, name, None)
        if fn is None:
            return
        _METHODS[name] = (fn, list(arg_names))

    def wrap_rates(symbol="EURUSD", timeframe="H1", count=500):
        df = get_client().rates(symbol, timeframe, count)
        if df is None or len(df) == 0:
            return {"columns": [], "rows": []}
        return {
            "columns": [str(col) for col in df.columns],
            "rows": [
                [None if v != v else v for v in row]  # NaN -> None
                for row in df.itertuples(index=False, name=None)
            ],
        }

    wrap_simple("account_info")
    wrap_simple("terminal_info")
    wrap_simple("health")
    wrap_simple("is_connected")
    wrap_simple("last_error")
    wrap_simple("symbols", ("group",))
    wrap_simple("symbol_info", ("symbol",))
    wrap_simple("symbol_exists", ("symbol",))
    wrap_simple("tick", ("symbol",))
    wrap_simple("positions", ("symbol",))
    wrap_simple("pending_orders", ("symbol",))
    wrap_simple("closed_orders", ("days", "symbol"))
    wrap_simple("deals", ("days", "symbol"))
    wrap_simple("deals_since", ("epoch_seconds", "symbol"))
    wrap_simple("account_deals_since", ("epoch_seconds",))
    wrap_simple("order_send", ("request",))
    wrap_simple("order_calc_margin", ("action", "symbol", "volume", "price"))
    wrap_simple("order_calc_profit", ("action", "symbol", "volume", "price_open", "price_close"))
    wrap_simple("market_open", ("symbol",))
    wrap_simple("server_time")
    wrap_simple("ensure_connected")
    _METHODS["rates"] = (wrap_rates, ["symbol", "timeframe", "count"])


_START_TS = time.time()


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 gateway (single terminal owner)")
    ap.add_argument("--port", type=int, default=int(os.getenv("MT5_GATEWAY_PORT", "8060")))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    # Single-instance guard (task + manual launches race on the port).
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((args.host, args.port))
        probe.close()
    except OSError:
        log.warning("port %s already in use - another gateway instance running; exiting", args.port)
        return 0

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("mt5 gateway starting on %s:%s", args.host, args.port)

    client = get_client()
    try:
        client.ensure_connected()
        info = client.account_info()
        log.info("terminal connected: login=%s server=%s", info.get("login"), info.get("server"))
    except Exception as exc:  # noqa: BLE001
        log.warning("initial connect failed (will keep retrying): %s", exc)

    _install_methods()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
