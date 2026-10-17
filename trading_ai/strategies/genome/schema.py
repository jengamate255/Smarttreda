from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class StrategyGenome:
    strategy_id: str
    version: int = 1
    parent_id: Optional[str] = None
    name: str = ""
    description: str = ""
    market: list[str] = field(default_factory=lambda: ["EURUSD"])
    timeframes: list[str] = field(default_factory=lambda: ["H4", "H1", "M15", "M5"])
    entry_rules: list[dict] = field(default_factory=list)
    exit_rules: list[dict] = field(default_factory=list)
    risk_rules: list[dict] = field(default_factory=list)
    filters: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if "created_at" not in self.metadata:
            self.metadata["created_at"] = datetime.utcnow().isoformat()

    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> StrategyGenome:
        data = json.loads(raw)
        return cls(**data)

    def clone(self) -> StrategyGenome:
        return StrategyGenome.from_json(self.to_json())

    def mutate(self, change_spec: dict) -> StrategyGenome:
        child = self.clone()
        child.parent_id = self.strategy_id
        child.strategy_id = f"{self.strategy_id}-M{uuid.uuid4().hex[:6]}"
        child.version = self.version + 1
        child.metadata["mutated_from"] = self.strategy_id
        child.metadata["mutation_time"] = datetime.utcnow().isoformat()

        for key, value in change_spec.items():
            if key in ("strategy_id", "parent_id", "version"):
                continue
            if key == "entry_rules":
                child.entry_rules = deepcopy(value)
            elif key == "exit_rules":
                child.exit_rules = deepcopy(value)
            elif key == "risk_rules":
                child.risk_rules = deepcopy(value)
            elif key == "filters":
                child.filters = deepcopy(value)
            elif key == "timeframes":
                child.timeframes = list(value)
            elif key == "market":
                child.market = list(value)
            elif key == "name":
                child.name = value
            elif key == "description":
                child.description = value
            else:
                child.metadata[key] = value

        return child
