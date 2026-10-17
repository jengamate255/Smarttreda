"""Wait for Deriv to recover, then restore the $500 micro sim.

Logic (every 60s, up to 6h):
  1. Try MT5 auth for demo 41204968 (pinned terminal path, pinned creds, NO
     path=none fallback). If it works -> relaunch the $500 watcher, done.
  2. If Deriv's websocket API answers: authorize with the stored API token and
     list MT5 accounts.
       - 41204968 still listed -> account alive; MT5 side still recovering; keep waiting.
       - 41204968 gone -> account was deleted server-side. User authorized
         (2026-10-12): create a fresh Deriv DEMO account via the API, set its
         MT5 password, swap settings.json deriv_mt5 to the new credentials,
         verify MT5 auth, relaunch the watcher.

NEVER touches real account 140794455 (human-trader account, forbidden for bots).

Exit codes: 0 = watcher relaunched; 1 = timed out waiting; 2/3 = API error
(needs manual action); 4 = settings write failed. All output goes to
logs/deriv_recovery.log and stdout.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mt5_mcp"))

LOG_PATH = ROOT / "logs" / "deriv_recovery.log"

PYTHONW = r"C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe"
DERIV_PATH = r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe"
OLD_DEMO_LOGIN = 41204968
WATCHER_ARGS = [
    "-X", "utf8", str(ROOT / "trade_watch.py"),
    "--micro", "--live", "--interval", "15", "--fixed-equity", "500",
    "--symbols", "BOOM1000,CRASH1000,CRASH500",
    "--log-file", str(ROOT / "logs" / "trade_watch.log"),
]
SETTINGS_PATH = ROOT / "mt5_mcp" / "settings.json"
BACKUP_PATH = ROOT / "mt5_mcp" / "memory" / "settings.pre_deriv_swap.json"
MAX_SECONDS = 6 * 3600
POLL_SECONDS = 60

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("deriv_recovery")


def mt5_auth_ok(login: int, password: str, server: str) -> tuple[bool, str]:
    import MetaTrader5 as mt5

    ok = mt5.initialize(path=DERIV_PATH, login=login, password=password, server=server, timeout=20000)
    if not ok:
        err = f"{mt5.last_error()}"
        mt5.shutdown()
        return False, err
    acc = mt5.account_info()
    detail = "account_info None"
    if acc is not None:
        detail = f"login={acc.login} balance={acc.balance} equity={acc.equity}"
    mt5.shutdown()
    return True, detail


def launch_watcher() -> int:
    env = dict(os.environ)
    env["MT5_MCP_ACCOUNT"] = "DERIV"
    proc = subprocess.Popen(
        [PYTHONW, *WATCHER_ARGS],
        cwd=str(ROOT),
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )
    log.info("watcher relaunched pid=%s args=%s", proc.pid, WATCHER_ARGS)
    return proc.pid


# ---------------------------------------------------------------------------
# Deriv websocket API
# ---------------------------------------------------------------------------

async def _api_call(req: dict, timeout: float = 30.0) -> dict:
    import websockets

    from mt5_mcp.config import get_settings

    d = get_settings().deriv
    url = f"{d.ws_url}?app_id={d.app_id}"
    async with websockets.connect(url, open_timeout=20) as ws:
        await ws.send(json.dumps(req))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    return resp


def api_call(req: dict, timeout: float = 30.0) -> dict | None:
    """Returns response dict, or None if the websocket is unreachable."""
    try:
        return asyncio.run(_api_call(req, timeout))
    except Exception as exc:
        log.info("ws call %s unreachable: %s", next(iter(req)), exc)
        return None


def generate_password() -> str:
    return "Xx9" + secrets.token_urlsafe(12) + "7"


def update_settings(login: int, password: str, server: str) -> bool:
    try:
        raw = SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        old = data.get("deriv_mt5", {})
        shutil.copyfile(SETTINGS_PATH, BACKUP_PATH)
        data["deriv_mt5"] = {
            **old,
            "login": int(login),
            "password": password,
            "server": server,
            "terminal_path": DERIV_PATH,
        }
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_PATH)
        log.info("settings.json deriv_mt5 updated: login=%s server=%s (backup %s)", login, server, BACKUP_PATH.name)
        return True
    except Exception as exc:
        log.error("settings update FAILED: %s", exc)
        return False


def handle_account_gone() -> int:
    from mt5_mcp.config import get_settings

    token = get_settings().deriv.api_token

    log.info("demo %s gone from mt5_login_list -> creating fresh DEMO account (user-authorized 2026-10-12)", OLD_DEMO_LOGIN)
    resp = api_call({
        "trading_account_create": 1,
        "platform": "dmt5",
        "account_type": "demo",
        "product": "standard",
    })
    if resp is None:
        return 1
    if resp.get("error"):
        log.error("trading_account_create error: %s", json.dumps(resp["error"]))
        return 2
    log.info("create response keys: %s", sorted(resp.keys()))

    info = resp.get("mt5_login_info") or {}
    new_login = info.get("login") or resp.get("new_account_loginid")
    new_server = info.get("server")
    new_loginid = resp.get("new_account_loginid")
    log.info("new demo: loginid=%s login=%s server=%s", new_loginid, new_login, new_server)
    if not new_login or not new_server:
        log.error("create response missing login/server: %s", json.dumps(resp)[:800])
        return 3

    password = generate_password()
    presp = api_call({
        "trading_account_password": 1,
        "loginid": new_loginid or new_login,
        "new_password": password,
    })
    if presp is None:
        return 1
    if presp.get("error"):
        log.error("trading_account_password error: %s", json.dumps(presp["error"]))
        log.error("MT5 password NOT set — manual action required for login %s", new_login)
        return 3
    log.info("MT5 password set for login=%s", new_login)

    if not update_settings(int(new_login), password, new_server):
        return 4

    for attempt in range(1, 11):
        ok, detail = mt5_auth_ok(int(new_login), password, new_server)
        if ok:
            log.info("new demo MT5 auth OK: %s", detail)
            launch_watcher()
            return 0
        log.info("new demo MT5 auth attempt %d/10: %s", attempt, detail)
        time.sleep(20)
    log.error("new demo credentials never authenticated")
    return 5


def main() -> int:
    log.info("=== deriv recovery waiter started (pid=%s, max %.1fh) ===", os.getpid(), MAX_SECONDS / 3600)
    start = time.time()
    while time.time() - start < MAX_SECONDS:
        ok, detail = mt5_auth_ok(OLD_DEMO_LOGIN, "REDACTED_MT5_PASSWORD$$$$", "Deriv-Demo")
        if ok:
            log.info("old demo %s auth OK: %s -> relaunching watcher", OLD_DEMO_LOGIN, detail)
            launch_watcher()
            log.info("=== done (old account restored) ===")
            return 0
        log.info("mt5 auth fail: %s", detail)

        ping = api_call({"ping": 1}, timeout=15)
        if ping is not None and ping.get("ping") == "pong":
            from mt5_mcp.config import get_settings

            auth = api_call({"authorize": get_settings().deriv.api_token})
            if auth and auth.get("error"):
                log.warning("ws alive but authorize failed: %s", json.dumps(auth["error"]))
            elif auth:
                log.info("ws authorize OK: loginid=%s", auth.get("authorize", {}).get("loginid"))
                listing = api_call({"mt5_login_list": 1})
                if listing and not listing.get("error"):
                    accounts = listing.get("mt5_login_list", [])
                    log.info("mt5_login_list: %s",
                             [(a.get("login"), a.get("server")) for a in accounts])
                    if any(str(a.get("login")) == str(OLD_DEMO_LOGIN) for a in accounts):
                        log.info("old demo still listed — MT5 server side still recovering; keep waiting")
                    else:
                        code = handle_account_gone()
                        log.info("=== recovery flow exited code=%s ===", code)
                        return code
                elif listing:
                    log.warning("mt5_login_list error: %s", json.dumps(listing["error"]))
            else:
                log.info("ws authorize unreachable")
        time.sleep(POLL_SECONDS)

    log.error("=== timed out after %.1fh without recovery ===", (time.time() - start) / 3600)
    return 1


if __name__ == "__main__":
    sys.exit(main())
