from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all trading AI agents.

    Provides a uniform lifecycle: init -> configure -> process -> teardown.
    """

    def __init__(self, name: str, config: dict[str, Any] | None = None) -> None:
        self.name = name
        self.config: dict[str, Any] = config or {}
        self._is_configured = False
        logger.info("Agent '%s' instantiated", self.name)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self, config: dict[str, Any] | None = None) -> None:
        """Apply configuration overrides.  Subclasses should call super().configure()."""
        if config:
            self.config.update(config)
        self._is_configured = True
        logger.info("Agent '%s' configured", self.name)

    @abstractmethod
    def process(self, data: pd.DataFrame) -> dict[str, Any]:
        """Run the agent's main logic on *data* and return results.

        Parameters
        ----------
        data : pd.DataFrame
            OHLCV (or derived) data, indexed by datetime.

        Returns
        -------
        dict
            Agent-specific result payload.
        """

    def teardown(self) -> None:
        """Release any resources held by the agent."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_configured(self) -> None:
        if not self._is_configured:
            raise RuntimeError(f"Agent '{self.name}' has not been configured")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
