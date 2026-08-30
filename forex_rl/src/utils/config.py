"""Load and access YAML configuration with .env overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read the YAML config and merge environment-variable overrides.

    Environment variables prefixed with ``FXRL_`` override nested keys using
    double-underscore separators, e.g. ``FXRL_RL__LEARNING_RATE=0.001``
    sets ``config["rl"]["learning_rate"]``.
    """
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh)

    # Load .env from project root if present
    env_file = config_path.parent / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    # Apply env overrides: FXRL_<KEY> or FXRL_<SECTION>__<KEY>
    prefix = "FXRL_"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        rest = env_key[len(prefix):]
        parts = rest.split("__")
        target = config
        for part in parts[:-1]:
            key = part.lower()
            if key not in target:
                target[key] = {}
            target = target[key]
        final_key = parts[-1].lower()
        target[final_key] = _coerce(env_val)

    return config


def get_nested(config: dict, key_path: str, default: Any = None) -> Any:
    """Access a nested dict value via dot-notation.

    Example::

        get_nested(cfg, "rl.learning_rate", 0.001)
    """
    keys = key_path.split(".")
    current: Any = config
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current


def _coerce(value: str) -> Any:
    """Best-effort string → typed coercion."""
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
