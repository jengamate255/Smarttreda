"""Confirm-and-execute pipeline: scan, gate, execute, journal, notify.

Full cycle every CONFIRM_POLL_SEC (default 30s): compliance halt + day-cap
check -> per symbol (skip if positioned) -> health -> strategy floors ->
analysis availability -> gate-log the evaluation -> rolling 24h entry cap
-> open_market safety checks -> journal fill -> Telegram card.

Between full cycles (every CONFIRM_ALERT_POLL_SEC, default 5s):
  * watch-alert bridge: a newly triggered watch alert forces an immediate
    scan of its symbol (entry lands within seconds of the level, not at
    the next full poll)
  * position watcher: vanished positions get a journal upsert + Telegram
    close card; pipeline positions close early when the last CLOSED H1
    bar closes beyond their zone floor (CONFIRM_ZONE_EXIT=1)
  * daily summary card once CONFIRM_SUMMARY_HOUR_UTC (default 21:00 UTC)
"""
import time, logging, os, sys, json, threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone

PROJECT_DIR = Path(__file__).resolve().parent
LOG_PATH = PROJECT_DIR / "logs" / "confirm_execute.log"
LOCK_PATH = PROJECT_DIR / "mt5_mcp" / "memory" / "confirm_execute.lock"
STATE_PATH = PROJECT_DIR / "mt5_mcp" / "memory" / "confirm_exec_state.json"
GATE_LOG_PATH = PROJECT_DIR / "mt5_mcp" / "memory" / "confirm_gate_log.jsonl"

# relaxed floors per user
MIN_CONF = 60
MIN_SCORE = 60
MIN_HEALTH = 55
MIN_RR = 2.0
MIN_ZONE = 60
# Pipeline watchlist: legacy scan set + analysis watchlist (24 Sep 2026:
# EURUSD supply-retest/floor-break short, JP225 H1-reclaim long).
# SPX500 deliberately excluded (abnormal-vol + spread gate).
# Override with CONFIRM_SYMBOLS=A,B,C ; cadence with CONFIRM_POLL_SEC=<n>.
DEFAULT_SYMBOLS = "AUDNZD,USDJPY,USDCAD,NZDCAD,EURUSD,JP225"


def _env_symbols() -> list[str]:
    return [
        s.strip().upper()
        for s in os.getenv("CONFIRM_SYMBOLS", DEFAULT_SYMBOLS).split(",")
        if s.strip()
    ]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SYMBOLS = _env_symbols()
POLL_SEC = _env_int("CONFIRM_POLL_SEC", 30)              # full scan cadence
ALERT_POLL_SEC = _env_int("CONFIRM_ALERT_POLL_SEC", 5)   # bridge/exit/summary tick
MAX_ENTRIES_24H = _env_int("CONFIRM_MAX_ENTRIES", 3)      # rolling 24h pipeline entries (0=off)
ZONE_EXIT = os.getenv("CONFIRM_ZONE_EXIT", "1") == "1"
ZONE_EXIT_MIN_STRENGTH = float(os.getenv("CONFIRM_ZONE_EXIT_MIN_STRENGTH", "50") or "50")
SUMMARY_HOUR_UTC = _env_int("CONFIRM_SUMMARY_HOUR_UTC", 21)
ALERT_BRIDGE_MIN = _env_int("CONFIRM_ALERT_WINDOW_MIN", 15)
DRY_RUN = os.getenv("CONFIRM_DRY_RUN", "0") == "1"        # set 1 to not place real orders


def setup_log():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    l = logging.getLogger("confirm_exec")
    l.setLevel(logging.INFO)
    l.handlers.clear()
    l.addHandler(h)
    try:
        l.addHandler(logging.StreamHandler())
    except Exception:
        pass
    return l


logger = setup_log()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
            if not h:
                return False
            st = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
            ctypes.windll.kernel32.CloseHandle(h)
            return st == 0x00000102
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    return True


def acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
            except Exception:
                pid = 0
            if pid and pid_alive(pid):
                logger.error(f"another confirm_execute running pid {pid}")
                return False
            try:
                LOCK_PATH.unlink()
            except Exception:
                pass
    return False


def release_lock():
    try:
        LOCK_PATH.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------- state
_state_lock = threading.Lock()


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except Exception as e:
        logger.warning(f"state save failed: {e}")


# single per-process source of truth (the daemon is the only writer; the lock
# guarantees that). Tests can replace it wholesale.
_STATE: dict = _load_state()


def entry_budget_ok(state: dict, now: float | None = None) -> bool:
    """Rolling-24h new-entry cap for pipeline fills (CONFIRM_MAX_ENTRIES, 0=off)."""
    if MAX_ENTRIES_24H <= 0:
        return True
    now = time.time() if now is None else now
    entries = [t for t in state.get("entries", []) if now - float(t) < 86400]
    if len(entries) != len(state.get("entries", [])):
        state["entries"] = entries
        _save_state(state)
    return len(entries) < MAX_ENTRIES_24H


def _note_entry(state: dict, now: float | None = None) -> None:
    with _state_lock:
        state.setdefault("entries", []).append(time.time() if now is None else now)
        _save_state(state)


# -------------------------------------------------------------- gate log
def _record_gate(sym: str, health: float, plan, avail: bool, reasons) -> None:
    """Append every evaluation to the jsonl gate log (floor-tuning evidence)."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "health": float(health),
        "action": plan.action,
        "confidence": float(plan.confidence or 0),
        "trade_score": float(plan.trade_score or 0),
        "zone_strength": float(plan.zone_strength or 0),
        "rr": plan.risk_reward,
        "valid": bool(plan.valid),
        "avail": bool(avail),
        "reasons": list(reasons),
        "floors": {"conf": MIN_CONF, "score": MIN_SCORE, "health": MIN_HEALTH,
                   "rr": MIN_RR, "zone": MIN_ZONE},
    }
    try:
        GATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if GATE_LOG_PATH.exists() and GATE_LOG_PATH.stat().st_size > 5_000_000:
            GATE_LOG_PATH.replace(GATE_LOG_PATH.with_name(GATE_LOG_PATH.name + ".1"))
        with open(GATE_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"gate log write failed: {e}")


# ------------------------------------------------- watch-alert bridge
_seen_alert_ids: set = set()


def pop_forced_symbols() -> list[str]:
    """Symbols whose watch alert fired in the last ALERT_BRIDGE_MIN window.

    Read-only (list_alerts), so the dashboard alert loop still sends its own
    notice; each alert id is handled at most once per process lifetime and the
    time window keeps a restart from replaying old triggers.
    """
    forced: list[str] = []
    try:
        from mt5_mcp.alerts import list_alerts

        cutoff = datetime.now(timezone.utc).timestamp() - ALERT_BRIDGE_MIN * 60
        for alert in list_alerts(include_triggered=True):
            aid = alert.get("id")
            if not aid or aid in _seen_alert_ids or not alert.get("triggered"):
                continue
            _seen_alert_ids.add(aid)
            if len(_seen_alert_ids) > 500:
                _seen_alert_ids.clear()  # time window re-filters anyway
            ts = str(alert.get("triggered_at") or "")
            try:
                if datetime.fromisoformat(ts).timestamp() < cutoff:
                    continue
            except Exception:
                continue
            sym = str(alert.get("symbol") or "").upper()
            if sym in SYMBOLS:
                forced.append(sym)
    except Exception as e:
        logger.warning(f"alert bridge check failed: {e}")
    return forced


# ----------------------------------------------------- position watcher
_seen_positions: dict = {}
_zone_checked_bucket: dict = {}
_zone_exit_tickets: set = set()


def _pos_side(pos: dict) -> str:
    try:
        return "BUY" if int(pos.get("type") or 0) == 0 else "SELL"
    except Exception:
        return "BUY"


def _resolve_close(client, ticket: int, seen: dict) -> dict | None:
    """Realized close deal(s) for a vanished position (pnl/exit/time)."""
    try:
        since = int(seen.get("time") or 0) - 120
        if since <= 0:
            since = int(time.time()) - 5 * 86400
        deals = client.deals_since(since) or []
    except Exception as e:
        logger.warning(f"close deal lookup failed #{ticket}: {e}")
        return None
    outs = [
        d for d in deals
        if int(d.get("position_id") or 0) == ticket
        and int(d.get("entry", -1)) == 1       # DEAL_ENTRY_OUT (0 must stay 0!)
        and int(d.get("type", -1)) in (0, 1)   # buy/sell only (no `or` default!)
    ]
    if not outs:
        return None
    pnl = sum(
        float(d.get("profit") or 0) + float(d.get("swap") or 0) + float(d.get("fee") or 0)
        for d in outs
    )
    return {
        "pnl": pnl,
        "exit_price": float(outs[-1].get("price") or 0),
        "time": int(max(int(d.get("time") or 0) for d in outs)),
    }


def _handle_close(client, ticket: int, seen: dict) -> None:
    """Journal (upsert) a vanished position and send the Telegram close card."""
    sym = str(seen.get("symbol") or "")
    side = _pos_side(seen)
    entry = float(seen.get("price_open") or 0)
    sl = float(seen.get("sl") or 0)
    tp = float(seen.get("tp") or 0)
    lots = float(seen.get("volume") or 0)
    comment = str(seen.get("comment") or "").strip()
    origin = "confirm_exec" if comment.startswith("confirm_exec") else (comment[:32] or "manual")
    zone_exit = ticket in _zone_exit_tickets
    _zone_exit_tickets.discard(ticket)

    close = _resolve_close(client, ticket, seen)
    if close:
        pnl, exit_price = close["pnl"], close["exit_price"]
        reason = "pipeline zone exit" if zone_exit else "closed (SL/TP/EA/market)"
    else:
        # no closing deal found in history - fall back to last floating values
        pnl = float(seen.get("profit") or 0)
        exit_price = float(seen.get("price_current") or 0)
        reason = "close (fill deal not found; pnl from last tick)"
    r = None
    if entry and sl and entry != sl and exit_price:
        if side == "BUY":
            r = round((exit_price - entry) / (entry - sl), 2)
        else:
            r = round((entry - exit_price) / (sl - entry), 2)
    outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
    logger.info(f"CLOSED #{ticket} {sym} {side} pnl={pnl:.2f} ({outcome}) - {reason}")
    try:
        from mt5_mcp.memory.trade_journal import get_journal

        get_journal().record_trade(
            ticket=ticket, symbol=sym, side=side, setup=origin,
            entry=entry or None, exit_price=exit_price or None,
            sl=sl or None, tp=tp or None, lots=lots or None,
            pnl=round(pnl, 2), outcome=outcome, reason=reason,
        )
    except Exception as e:
        logger.warning(f"journal close failed #{ticket}: {e}")
    try:
        from mt5_mcp.telegram_alerts import send_telegram, trade_close_msg

        balance = None
        try:
            balance = float(client.account_info().get("balance") or 0)
        except Exception:
            pass
        send_telegram(trade_close_msg(
            sym, side, round(pnl, 2), r, reason=reason, balance=balance, ticket=ticket,
        ))
    except Exception as e:
        logger.warning(f"close telegram failed #{ticket}: {e}")


def _zone_floor_broken(client, pos: dict) -> bool:
    """True when the last CLOSED H1 bar closed beyond the position's zone floor.

    BUY: close below the tightest demand-zone lower edge at/below entry.
    SELL: close above the tightest supply-zone upper edge at/above entry.
    Zones weaker than ZONE_EXIT_MIN_STRENGTH are ignored (structure noise).
    """
    from mt5_mcp.supply_demand import detect_zones

    sym = str(pos.get("symbol") or "")
    side = _pos_side(pos)
    entry = float(pos.get("price_open") or 0)
    if not sym or not entry:
        return False
    df = client.rates(sym, "H1", 200)
    if df is None or len(df) < 40:
        return False
    closed = df.iloc[:-1] if len(df) > 1 else df  # drop the forming bar
    zones = detect_zones(closed)
    if side == "BUY":
        floors = [
            z.lower for z in zones
            if z.type == "demand" and z.strength >= ZONE_EXIT_MIN_STRENGTH and z.lower <= entry
        ]
        if not floors:
            return False
        floor = max(floors)  # tightest structure floor under entry
        broken = float(closed["close"].iloc[-1]) < floor
        detail = f"floor {floor:.5f}"
    else:
        ceilings = [
            z.upper for z in zones
            if z.type == "supply" and z.strength >= ZONE_EXIT_MIN_STRENGTH and z.upper >= entry
        ]
        if not ceilings:
            return False
        ceiling = min(ceilings)
        broken = float(closed["close"].iloc[-1]) > ceiling
        detail = f"ceiling {ceiling:.5f}"
    if broken:
        logger.info(f"zone exit signal {sym} {side}: last closed H1 beyond {detail}")
    return broken


def watch_positions(client) -> None:
    """Detect closes (journal + card) and run zone-floor exits for pipeline trades."""
    global _seen_positions
    try:
        current = {
            int(p.get("ticket") or 0): p
            for p in (client.positions() or [])
            if p.get("ticket")
        }
    except Exception as e:
        logger.warning(f"positions poll failed: {e}")
        return
    for ticket, seen in list(_seen_positions.items()):
        if ticket not in current:
            try:
                _handle_close(client, ticket, seen)
            except Exception as e:
                logger.exception(f"close handling failed #{ticket}: {e}")
    _seen_positions = current
    for stale in [t for t in _zone_checked_bucket if t not in current]:
        _zone_checked_bucket.pop(stale, None)
    if not ZONE_EXIT:
        return
    bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    for ticket, pos in current.items():
        if not str(pos.get("comment") or "").startswith("confirm_exec"):
            continue
        if _zone_checked_bucket.get(ticket) == bucket:
            continue
        _zone_checked_bucket[ticket] = bucket  # one check per closed H1 bar
        try:
            if not _zone_floor_broken(client, pos):
                continue
            from mt5_mcp.tools.trades import close_position

            logger.info(
                f"ZONE EXIT closing #{ticket} {pos.get('symbol')} - H1 close beyond zone floor"
            )
            res = close_position(ticket)
            if getattr(res, "accepted", False):
                _zone_exit_tickets.add(ticket)
            else:
                logger.warning(
                    f"zone exit close failed #{ticket}: {getattr(res, 'error', 'rejected')}"
                )
        except Exception as e:
            logger.warning(f"zone exit check failed #{ticket}: {e}")


def maybe_daily_summary(client, state: dict) -> None:
    """One Telegram summary per day once SUMMARY_HOUR_UTC is reached."""
    now = datetime.now(timezone.utc)
    if now.hour < SUMMARY_HOUR_UTC:
        return
    today = now.date().isoformat()
    if state.get("summary_date") == today:
        return
    # mark sent BEFORE the send attempt so a failing Telegram cannot spam retries
    state["summary_date"] = today
    _save_state(state)
    try:
        acct = client.account_info()
        balance = float(acct.get("balance") or 0)
        equity = float(acct.get("equity") or 0)
        day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        deals = [
            d for d in (client.deals_since(day_start) or [])
            if int(d.get("type", -1)) in (0, 1)   # buy/sell only (no `or` default!)
        ]
        closes: dict = {}
        for d in deals:
            if int(d.get("entry", -1)) != 1:      # closing deals only (0 must stay 0)
                continue
            pid = int(d.get("position_id") or 0)
            closes[pid] = closes.get(pid, 0.0) + (
                float(d.get("profit") or 0) + float(d.get("swap") or 0) + float(d.get("fee") or 0)
            )
        wins = sum(1 for v in closes.values() if v > 0)
        losses = sum(1 for v in closes.values() if v <= 0)
        pnl_today = sum(
            float(d.get("profit") or 0) + float(d.get("swap") or 0) + float(d.get("fee") or 0)
            for d in deals
        )
        open_positions = len(client.positions() or [])
        from mt5_mcp.telegram_alerts import send_telegram, daily_summary_msg

        send_telegram(daily_summary_msg(
            balance, equity, wins + losses, wins, losses, round(pnl_today, 2), open_positions,
        ))
        logger.info(
            f"daily summary sent: closed={wins + losses} pnl={pnl_today:.2f} open={open_positions}"
        )
    except Exception as e:
        logger.warning(f"daily summary failed: {e}")


# ------------------------------------------------------------------ scan
def _compliance_ok() -> bool:
    try:
        from mt5_mcp.compliance import evaluate as eval_comp

        comp = eval_comp()
        if str(comp.get("mode") or "") in ("halt", "halted"):
            logger.warning(f"compliance halt: {comp.get('halt_reason')}")
            return False
        cap = int(comp.get("max_trades_per_day") or 0)
        today = int(comp.get("trades_today") or 0)
        if cap and today >= cap:
            logger.info(f"compliance daily trade cap reached {today}/{cap} - no new entries")
            return False
    except Exception as e:
        logger.warning(f"compliance check failed: {e}")
    return True


def _journal_fill(sym: str, plan, res) -> None:
    try:
        from mt5_mcp.memory.trade_journal import get_journal

        get_journal().record_trade(
            ticket=int(getattr(res, "ticket", 0) or 0),
            symbol=sym, side=plan.action, setup="confirm_exec",
            timeframe=plan.timeframe or "H1",
            entry=float(plan.entry), sl=float(plan.sl), tp=float(plan.tp),
            lots=float(plan.lots),
            open_time=datetime.now(timezone.utc).isoformat(),
            confidence=float(plan.confidence or 0),
            trade_score=float(plan.trade_score or 0),
            zone_strength=float(plan.zone_strength or 0),
            rr=plan.risk_reward,
            session_hour=datetime.now(timezone.utc).hour,
            reason=plan.reason or "",
            trend_confidence=float(getattr(plan, "trend_confidence", 0) or 0),
            confirmation_confidence=float(getattr(plan, "confirmation_confidence", 0) or 0),
            sl_pips=float(getattr(plan, "sl_pips", 0) or 0),
        )
    except Exception as e:
        logger.warning(f"journal fill failed {sym}: {e}")



def scan_symbol(client, sym: str, state: dict) -> int:
    """Run one symbol through every gate; execute if all pass. Returns #fills."""
    from mt5_mcp.strategy import evaluate_strategy
    from mt5_mcp.market_intel import market_health
    from analysis_agent import is_available
    from mt5_mcp.consensus import risk_cap as consensus_risk_cap
    from mt5_mcp.pipeline import publish_stance, run_gates
    from mt5_mcp.notifier import trade_filled

    try:
        if client.positions(sym):
            logger.info(f"{sym} skip: existing position open")
            return 0
        health = market_health(sym, timeframe="H1", lookback=400)
        h = float(health.get("health_score", 0))
        # relaxed strategy evaluation, sized at the unified consensus risk cap
        plan = evaluate_strategy(symbol=sym, risk_percent=consensus_risk_cap(), min_zone_score=MIN_ZONE, min_trade_score=MIN_SCORE, min_confidence_percent=MIN_CONF, min_rr=MIN_RR, closed_confirmation=True)
        short = {"valid": bool(plan.valid), "action": plan.action, "confidence": float(plan.confidence or 0), "trade_score": float(plan.trade_score or 0), "risk_reward": plan.risk_reward, "reason": plan.reason}
        avail, reasons = is_available(short, h, min_confidence=MIN_CONF, min_score=MIN_SCORE, min_health=MIN_HEALTH, min_rr=MIN_RR)
        logger.info(f"{sym} health={h:.1f} plan={plan.action}/{plan.confidence:.1f} valid={plan.valid} score={plan.trade_score:.1f} RR={plan.risk_reward} zone={plan.zone_strength:.1f} conf={plan.confirmation} avail={avail} {'|'.join(reasons)}")
        _record_gate(sym, h, plan, avail, reasons)
        publish_stance("confirm", sym, plan.action, float(plan.confidence or 0))
        if not avail:
            return 0
        # need entry/sl/tp
        if not plan.entry or not plan.sl or not plan.tp:
            logger.info(f"{sym} no entry levels")
            return 0
        # unified gate: consensus allowlist/veto + compliance (fail-open consensus)
        gate_verdict = run_gates(
            symbol=sym, side=plan.action, lots=float(plan.lots),
            sl_price=float(plan.sl),
            confidence=float(plan.confidence or 0),
            source="confirm", score=float(plan.confidence or 0),
            setup="confirm_exec",
            tp_price=float(plan.tp),
            entry_price=float(plan.entry),
        )
        if not gate_verdict.allowed:
            logger.info(f"{sym} gate blocked: {'; '.join(gate_verdict.violations)}")
            return 0
        # execute
        if DRY_RUN:
            logger.info(f"DRY_RUN would execute {sym} {plan.action} {plan.lots} lots entry {plan.entry} sl {plan.sl} tp {plan.tp} RR {plan.risk_reward}")
            return 1
        if not entry_budget_ok(state):
            logger.info(f"{sym} entry cap reached ({MAX_ENTRIES_24H} entries/24h) - passing")
            return 0
        logger.info(f"EXECUTING {sym} {plan.action} {plan.lots} lots {plan.entry} SL {plan.sl} TP {plan.tp} RR {plan.risk_reward} conf {plan.confidence:.0f}%")
        from mt5_mcp.tools.trades import open_market

        res = open_market(sym, plan.action, float(plan.lots), plan.sl, plan.tp, comment="confirm_exec relaxed")
        if getattr(res, "accepted", False):
            logger.info(f"EXECUTED {sym} ticket {res.ticket} {plan.action} {plan.lots} RR {plan.risk_reward}")
            _note_entry(state)
            _journal_fill(sym, plan, res)
            trade_filled(
                symbol=sym, side=plan.action, lots=float(plan.lots),
                entry=float(plan.entry), sl=float(plan.sl), tp=float(plan.tp),
                ticket=int(getattr(res, "ticket", 0) or 0),
                confidence=float(plan.confidence or 0),
                setup="confirm_exec relaxed",
                rr=str(plan.risk_reward or ""),
            )
            return 1
        logger.warning(f"EXEC FAILED {sym}: {getattr(res, 'error', 'rejected')} checks={getattr(res, 'checks', [])}")
        return 0
    except Exception as e:
        logger.exception(f"{sym} scan failed: {e}")
        return 0


def _connected_client():
    from mt5_mcp.client import get_client

    client = get_client()
    try:
        client.ensure_connected()
    except Exception as e:
        logger.warning(f"MT5 not connected: {e}")
        return None
    return client


def scan_symbol_now(sym: str) -> int:
    """Immediate single-symbol scan (alert-bridge path)."""
    client = _connected_client()
    if client is None or not _compliance_ok():
        return 0
    return scan_symbol(client, sym, _STATE)


def scan_and_maybe_trade() -> int:
    client = _connected_client()
    if client is None:
        return 0
    if not _compliance_ok():
        return 0
    executed = 0
    for sym in SYMBOLS:
        executed += scan_symbol(client, sym, _STATE)
    return executed


def main():
    if not acquire_lock():
        sys.exit(1)
    logger.info(
        f"confirm_execute daemon starting symbols={SYMBOLS} floors conf>{MIN_CONF} score>{MIN_SCORE} "
        f"RR>{MIN_RR} zone>{MIN_ZONE} poll={POLL_SEC}s alert_poll={ALERT_POLL_SEC}s "
        f"max_entries_24h={MAX_ENTRIES_24H} zone_exit={ZONE_EXIT} "
        f"summary_hour_utc={SUMMARY_HOUR_UTC} dry_run={DRY_RUN}"
    )
    last_full = 0.0
    try:
        while True:
            try:
                if time.time() - last_full >= POLL_SEC:
                    n = scan_and_maybe_trade()
                    if n:
                        logger.info(f"cycle done executed {n}")
                    else:
                        logger.info("cycle done 0 executed - waiting for confirmation")
                    last_full = time.time()
                else:
                    # fast tick: alert bridge (immediate scans), watcher, summary
                    for sym in pop_forced_symbols():
                        logger.info(f"alert bridge: immediate scan for {sym}")
                        scan_symbol_now(sym)
                from mt5_mcp.client import get_client

                client = get_client()
                watch_positions(client)
                maybe_daily_summary(client, _STATE)
            except Exception as e:
                logger.exception(f"cycle error {e}")
            time.sleep(ALERT_POLL_SEC)
    except KeyboardInterrupt:
        logger.info("stopped")
    finally:
        release_lock()


if __name__ == "__main__":
    main()





