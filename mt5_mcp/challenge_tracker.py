"""FundingPips challenge tracker.

Tracks progress toward passing the prop challenge:
- Profit target: 8% of initial balance
- Max daily loss: 5% (internal cap: 4%)
- Max drawdown: 8% from peak equity
- Consistency: no single day > 40% of total profit
- Min trading days: 5

State persisted to mt5_mcp/memory/challenge_state.json.

Usage:
    from mt5_mcp.challenge_tracker import ChallengeTracker
    ct = ChallengeTracker()
    status = ct.get_status()
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path

logger = logging.getLogger("challenge_tracker")

MEMORY_DIR = Path(__file__).parent / "memory"
STATE_FILE = MEMORY_DIR / "challenge_state.json"
JOURNAL_DB = MEMORY_DIR / "journal.db"

CHALLENGE_CONFIG = {
    "profit_target_pct": 8.0,
    "max_daily_loss_pct": 4.0,
    "max_drawdown_pct": 8.0,
    "consistency_max_day_pct": 40.0,
    "min_trading_days": 5,
    "risk_per_trade_pct": 1.0,
}


class ChallengeTracker:
    def __init__(self, initial_balance: float | None = None):
        self.config = CHALLENGE_CONFIG.copy()
        self.state = self._load_state()
        if initial_balance is not None:
            self.state["initial_balance"] = initial_balance
            self._save_state()

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "initial_balance": 0.0,
            "start_date": date.today().isoformat(),
            "challenge_passed": False,
        }

    def _save_state(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(self.state, indent=2, default=str),
            encoding="utf-8",
        )

    def _query_trades(self, days: int | None = None) -> list[dict]:
        if not JOURNAL_DB.exists():
            return []
        conn = sqlite3.connect(str(JOURNAL_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if days:
            cur.execute(
                "SELECT * FROM trades WHERE close_time >= date('now', ?) "
                "ORDER BY close_time",
                (f"-{days} days",),
            )
        else:
            cur.execute("SELECT * FROM trades ORDER BY close_time")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _get_current_balance(self) -> float:
        try:
            from mt5_mcp.client import get_client
            client = get_client()
            client.ensure_connected()
            account = client.account_info()
            return float(account.get("equity", 0.0) or 0.0)
        except Exception as exc:
            logger.warning("cannot fetch live balance: %s", exc)
            return 0.0

    def get_status(self, live_balance: float | None = None) -> dict:
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")

        initial = self.state.get("initial_balance", 0.0)
        if initial <= 0:
            if live_balance and live_balance > 0:
                initial = live_balance
                self.state["initial_balance"] = initial
                self._save_state()
            else:
                return {"error": "initial_balance not set — call /api/challenge/init"}

        balance = live_balance or self._get_current_balance()
        if balance <= 0:
            return {"error": "cannot determine current balance"}

        all_trades = self._query_trades()
        today_trades = [t for t in all_trades if (t.get("close_time") or "")[:10] == today_str]

        total_pnl = sum(t.get("pnl", 0.0) or 0.0 for t in all_trades)
        today_pnl = sum(t.get("pnl", 0.0) or 0.0 for t in today_trades)

        total_pnl_pct = total_pnl / initial * 100
        target_pct = self.config["profit_target_pct"]
        progress_pct = min(100.0, total_pnl_pct / target_pct * 100)

        daily_loss_remaining = self.config["max_daily_loss_pct"] + today_pnl / initial * 100
        daily_loss_remaining = max(0.0, daily_loss_remaining)

        peak_equity = initial + max(0, total_pnl)
        current_dd_pct = max(0.0, (peak_equity - balance) / initial * 100)

        daily_pnl_by_day: dict[str, float] = {}
        for t in all_trades:
            day = (t.get("close_time") or "")[:10]
            if day:
                daily_pnl_by_day[day] = daily_pnl_by_day.get(day, 0.0) + (t.get("pnl", 0.0) or 0.0)

        trading_days = len([d for d, pnl in daily_pnl_by_day.items() if pnl != 0.0])
        best_day = max(daily_pnl_by_day.values()) if daily_pnl_by_day else 0.0
        consistency_ratio = (best_day / total_pnl * 100) if total_pnl > 0 else 0.0
        consistency_ok = consistency_ratio <= self.config["consistency_max_day_pct"]

        remaining_pnl = max(0.0, initial * target_pct / 100 - total_pnl)
        avg_daily_pnl = total_pnl / trading_days if trading_days > 0 else 0.0
        projected_days = int(remaining_pnl / avg_daily_pnl) if avg_daily_pnl > 0 else -1

        min_days_met = trading_days >= self.config["min_trading_days"]
        challenge_passed = (
            total_pnl_pct >= target_pct
            and min_days_met
            and current_dd_pct < self.config["max_drawdown_pct"]
            and consistency_ok
        )

        if challenge_passed and not self.state.get("challenge_passed"):
            self.state["challenge_passed"] = True
            self.state["passed_date"] = today_str
            self._save_state()

        status = {
            "timestamp": now.isoformat(),
            "initial_balance": round(initial, 2),
            "current_balance": round(balance, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "target_pct": target_pct,
            "progress_pct": round(progress_pct, 1),
            "remaining_pnl": round(remaining_pnl, 2),
            "today_pnl": round(today_pnl, 2),
            "today_trades": len(today_trades),
            "daily_loss_remaining_pct": round(daily_loss_remaining, 2),
            "max_daily_loss_pct": self.config["max_daily_loss_pct"],
            "current_dd_pct": round(current_dd_pct, 2),
            "max_drawdown_pct": self.config["max_drawdown_pct"],
            "trading_days": trading_days,
            "min_trading_days": self.config["min_trading_days"],
            "min_days_met": min_days_met,
            "consistency_ratio": round(consistency_ratio, 1),
            "consistency_ok": consistency_ok,
            "best_day_pnl": round(best_day, 2),
            "projected_days_to_pass": projected_days,
            "avg_daily_pnl": round(avg_daily_pnl, 2),
            "challenge_passed": challenge_passed,
            "risk_alerts": [],
        }

        if daily_loss_remaining < 1.0:
            status["risk_alerts"].append(
                f"DAILY LOSS WARNING: only {daily_loss_remaining:.1f}% remaining today"
            )
        if current_dd_pct > self.config["max_drawdown_pct"] * 0.75:
            status["risk_alerts"].append(
                f"DRAWDOWN WARNING: {current_dd_pct:.1f}% of {self.config['max_drawdown_pct']}% limit"
            )
        if not consistency_ok:
            status["risk_alerts"].append(
                f"CONSISTENCY RISK: best day is {consistency_ratio:.0f}% of total profit "
                f"(max {self.config['consistency_max_day_pct']}%)"
            )

        return status

    def init_challenge(self, balance: float) -> dict:
        self.state["initial_balance"] = balance
        self.state["start_date"] = date.today().isoformat()
        self.state["challenge_passed"] = False
        self.state.pop("passed_date", None)
        self._save_state()
        logger.info("Challenge initialized with balance $%.2f", balance)
        return self.get_status(live_balance=balance)

    def get_daily_breakdown(self) -> list[dict]:
        all_trades = self._query_trades()
        by_day: dict[str, dict] = {}
        for t in all_trades:
            day = (t.get("close_time") or "")[:10]
            if not day:
                continue
            if day not in by_day:
                by_day[day] = {"date": day, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}
            by_day[day]["pnl"] += t.get("pnl", 0.0) or 0.0
            by_day[day]["trades"] += 1
            if (t.get("outcome") or "") == "win":
                by_day[day]["wins"] += 1
            else:
                by_day[day]["losses"] += 1

        result = sorted(by_day.values(), key=lambda d: d["date"], reverse=True)
        for d in result:
            d["pnl"] = round(d["pnl"], 2)
        return result

    def get_consistency_multiplier(self, live_balance: float | None = None) -> float:
        """Return lot size multiplier (0.0-1.0) based on consistency risk.

        FundingPips rule: no single day > 40% of total profit.
        If best day approaches this limit, reduce lot size to spread gains.

        Returns:
            1.0 if no risk (can trade full size)
            0.5 if moderate risk (best day > 30% of total)
            0.25 if high risk (best day > 35% of total)
            0.25 if critical (best day > 40% of total) — FLOORED 2026-10-01,
                was 0.0 (full pause): trade_desk is the sole consumer, so a hard
                pause deadlocks total_pnl growth and the ratio can never self-heal.
        """
        status = self.get_status(live_balance=live_balance)
        consistency_ratio = status.get("consistency_ratio", 0.0)
        total_pnl = status.get("total_pnl", 0.0)

        if total_pnl <= 0:
            return 1.0

        if consistency_ratio >= 40.0:
            return 0.25
        elif consistency_ratio >= 35.0:
            return 0.25
        elif consistency_ratio >= 30.0:
            return 0.5
        else:
            return 1.0
