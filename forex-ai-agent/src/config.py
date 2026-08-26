"""Configuration loading and project paths.

Configuration lives in config/config.yaml. Secrets (MT5 login etc.) come from
environment variables or a .env file next to the project root. Nothing
credential-like is hard-coded here.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any

import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def load_yaml(path: pathlib.Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_env_file(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Config:
    def __init__(self, data: dict | None = None, path: pathlib.Path | None = None):
        self.path = path or CONFIG_PATH
        self.data = data if data is not None else load_yaml(self.path)
        self._env_path = self.path.parents[0].parent / ".env"
        load_env_file(self._env_path)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> "Config":
        return cls(path=pathlib.Path(path))

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def symbol_overrides(self, symbol: str) -> dict:
        """Optional per-symbol section under `symbols_overrides".

        Example config:
            symbols_overrides:
              EURUSD:
                agent: {max_spread_pips: 2.0}
                labels: {atr_stop_multiple: 2.0}
                risk: {default_percent: 0.5}

        Returns the (possibly empty) merged section dict for the symbol.
        """
        overrides = self.get("symbols_overrides", {}) or {}
        entry = overrides.get(symbol.upper())
        if not isinstance(entry, dict):
            return {}
        return entry

    def get_symbol(self, dotted: str, symbol: str, default: Any = None) -> Any:
        """Resolve a dotted key with per-symbol precedence.

        A `symbols_overrides.<SYMBOL>.<section>.<key>` value wins over the global
        `dotted` value; both fall back to `default`.
        """
        section, _, tail = dotted.partition(".")
        overrides = (self.symbol_overrides(symbol) or {}).get(section)
        if isinstance(overrides, dict):
            node: Any = overrides
            for part in tail.split(".") if tail else []:
                if not isinstance(node, dict) or part not in node:
                    node = None
                    break
                node = node[part]
            if node is not None:
                return node
        return self.get(dotted, default)

    def env(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)

    def require_env(self, name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(
                f"Environment variable {name} is not set. "
                "Copy .env.example to .env and fill in your MT5 credentials."
            )
        return value

    def as_dict(self) -> dict:
        return self.data


DEFAULT_CONFIG = Config()
