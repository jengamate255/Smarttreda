"""Structured JSON-line logging with file and console handlers."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry, default=str)


class _ConsoleFormatter(logging.Formatter):
    """Human-friendly console output."""

    FMT = "%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s"
    DATE_FMT = "%Y-%m-%d %H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        formatter = logging.Formatter(self.FMT, datefmt=self.DATE_FMT)
        return formatter.format(record)


def setup_logger(
    name: str,
    log_dir: str | Path | None = None,
    level: int | str = logging.INFO,
) -> logging.Logger:
    """Create a logger with both file (JSON) and console (human) handlers.

    Parameters
    ----------
    name : logger name (typically module or experiment name)
    log_dir : directory for log files; ``None`` disables file output
    level : logging level – int or string like ``"INFO"``
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_ConsoleFormatter())
    logger.addHandler(console)

    # File handler (JSON lines)
    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        safe_name = name.replace(".", "_").replace("/", "_")
        file_handler = logging.FileHandler(
            log_path / f"{safe_name}.jsonl", encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(_JSONFormatter())
        logger.addHandler(file_handler)

    return logger
