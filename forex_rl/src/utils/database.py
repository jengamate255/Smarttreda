"""SQLite persistence for experiments, trades, episodes and metrics."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "trades.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experiments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    config      TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    status      TEXT    NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS training_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    algorithm       TEXT    NOT NULL DEFAULT 'PPO',
    hyperparams     TEXT    NOT NULL DEFAULT '{}',
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT    NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    training_run_id INTEGER NOT NULL REFERENCES training_runs(id),
    version         INTEGER NOT NULL DEFAULT 0,
    filepath        TEXT    NOT NULL,
    metrics         TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    ticket          INTEGER,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    sl_price        REAL,
    tp_price        REAL,
    lots            REAL,
    pnl             REAL,
    pnl_pips        REAL,
    entry_time      TEXT,
    exit_time       TEXT,
    duration_s      REAL,
    setup           TEXT,
    regime          TEXT,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    episode_num     INTEGER NOT NULL,
    total_reward    REAL,
    sharpe          REAL,
    max_drawdown    REAL,
    win_rate        REAL,
    total_trades    INTEGER,
    profit_factor   REAL,
    equity_curve    TEXT,
    duration_s      REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    metric_name     TEXT    NOT NULL,
    metric_value    REAL    NOT NULL,
    period          TEXT,
    recorded_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_regimes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id   INTEGER NOT NULL REFERENCES experiments(id),
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    regime          TEXT    NOT NULL,
    confidence      REAL,
    start_time      TEXT,
    end_time        TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


class TradeDB:
    """SQLite-backed storage for RL trading experiments."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------

    def create_experiment(self, name: str, config: dict[str, Any] | None = None) -> int:
        """Insert a new experiment and return its ID."""
        cfg_json = json.dumps(config or {}, default=str)
        cur = self._conn.execute(
            "INSERT INTO experiments (name, config) VALUES (?, ?)",
            (name, cfg_json),
        )
        self._conn.commit()
        exp_id = cur.lastrowid
        logger.info("Created experiment %d (%s)", exp_id, name)
        return exp_id  # type: ignore[return-value]

    def get_experiment(self, experiment_id: int) -> dict[str, Any]:
        """Fetch a single experiment row as a dict."""
        row = self._conn.execute(
            "SELECT id, name, config, created_at, status FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Experiment {experiment_id} not found")
        return {
            "id": row[0],
            "name": row[1],
            "config": json.loads(row[2]),
            "created_at": row[3],
            "status": row[4],
        }

    def compare_experiments(self, ids: list[int]) -> pd.DataFrame:
        """Return a DataFrame comparing multiple experiments."""
        if not ids:
            return pd.DataFrame()
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT id, name, config, created_at, status FROM experiments WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        records = []
        for row in rows:
            rec: dict[str, Any] = {
                "id": row[0],
                "name": row[1],
                "created_at": row[3],
                "status": row[4],
            }
            cfg = json.loads(row[2])
            for k, v in cfg.items():
                rec[f"config_{k}"] = v
            # Aggregate trade stats
            stats = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(pnl),0), "
                "CASE WHEN COUNT(*)>0 THEN SUM(CASE WHEN pnl>0 THEN 1.0 ELSE 0 END)/COUNT(*) ELSE 0 END "
                "FROM trades WHERE experiment_id = ?",
                (row[0],),
            ).fetchone()
            rec["total_trades"] = stats[0]
            rec["total_pnl"] = stats[1]
            rec["win_rate"] = round(stats[2], 4) if stats[0] else 0.0
            records.append(rec)
        return pd.DataFrame(records)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def log_trade(self, experiment_id: int, trade_data: dict[str, Any]) -> int:
        """Insert a trade record."""
        columns = [
            "experiment_id", "ticket", "symbol", "side", "entry_price",
            "exit_price", "sl_price", "tp_price", "lots", "pnl", "pnl_pips",
            "entry_time", "exit_time", "duration_s", "setup", "regime", "notes",
        ]
        values = [experiment_id]
        for col in columns[1:]:
            val = trade_data.get(col)
            if isinstance(val, (dict, list)):
                val = json.dumps(val, default=str)
            values.append(val)
        placeholders = ",".join("?" * len(columns))
        col_names = ",".join(columns)
        cur = self._conn.execute(
            f"INSERT INTO trades ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Episodes
    # ------------------------------------------------------------------

    def log_episode(self, experiment_id: int, episode_data: dict[str, Any]) -> int:
        """Insert an episode record."""
        columns = [
            "experiment_id", "episode_num", "total_reward", "sharpe",
            "max_drawdown", "win_rate", "total_trades", "profit_factor",
            "equity_curve", "duration_s",
        ]
        values = [experiment_id]
        for col in columns[1:]:
            val = episode_data.get(col)
            if isinstance(val, (dict, list)):
                val = json.dumps(val, default=str)
            values.append(val)
        placeholders = ",".join("?" * len(columns))
        col_names = ",".join(columns)
        cur = self._conn.execute(
            f"INSERT INTO episodes ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
