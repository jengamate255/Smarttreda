from __future__ import annotations

from copy import deepcopy
from typing import Any

from trading_ai.strategies.genome.schema import StrategyGenome

PROTECTED_KEYS = frozenset({
    "broker_credentials",
    "risk_limits",
    "kill_switch",
    "withdrawal",
    "security",
    "api_keys",
    "max_drawdown",
    "max_position_size",
    "max_daily_loss",
    "max_risk_per_trade",
})


class StrategyMutator:

    @staticmethod
    def _validate_no_protected(change: dict) -> None:
        for key in change:
            if key in PROTECTED_KEYS:
                raise ValueError(f"Mutation of protected key '{key}' is forbidden")

    @staticmethod
    def _find_filter(genome: StrategyGenome, filter_type: str) -> dict | None:
        for f in genome.filters:
            if f.get("type") == filter_type:
                return f
        return None

    @staticmethod
    def _upsert_filter(genome: StrategyGenome, filter_type: str, params: dict) -> list[dict]:
        filters = deepcopy(genome.filters)
        for f in filters:
            if f.get("type") == filter_type:
                f["params"] = {**f.get("params", {}), **params}
                return filters
        filters.append({"type": filter_type, "params": params, "description": ""})
        return filters

    def mutate_entry_filter(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        entry_rules = deepcopy(genome.entry_rules)
        rule_type = change.pop("rule_type", None)

        if rule_type:
            for rule in entry_rules:
                if rule.get("type") == rule_type:
                    rule["params"] = {**rule.get("params", {}), **change}
                    break
            else:
                entry_rules.append({"type": rule_type, "params": change, "description": change.pop("description", "")})
        else:
            entry_rules.append({"type": "generic", "params": change, "description": change.pop("description", "")})

        return genome.mutate({"entry_rules": entry_rules})

    def mutate_exit_rule(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        exit_rules = deepcopy(genome.exit_rules)
        rule_type = change.pop("rule_type", None)

        if rule_type:
            for rule in exit_rules:
                if rule.get("type") == rule_type:
                    rule["params"] = {**rule.get("params", {}), **change}
                    break
            else:
                exit_rules.append({"type": rule_type, "params": change, "description": change.pop("description", "")})
        else:
            exit_rules.append({"type": "generic", "params": change, "description": change.pop("description", "")})

        return genome.mutate({"exit_rules": exit_rules})

    def mutate_session_filter(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        filters = self._upsert_filter(genome, "session", change)
        return genome.mutate({"filters": filters})

    def mutate_volatility_filter(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        filters = self._upsert_filter(genome, "volatility", change)
        return genome.mutate({"filters": filters})

    def mutate_timeframe(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        new_timeframes = change.get("timeframes", genome.timeframes)
        return genome.mutate({"timeframes": list(new_timeframes)})

    def mutate_stop_placement(self, genome: StrategyGenome, change: dict) -> StrategyGenome:
        self._validate_no_protected(change)
        risk_rules = deepcopy(genome.risk_rules)
        for rule in risk_rules:
            if rule.get("type") == "stop_placement":
                rule["params"] = {**rule.get("params", {}), **change}
                return genome.mutate({"risk_rules": risk_rules})
        risk_rules.append({"type": "stop_placement", "params": change, "description": change.pop("description", "")})
        return genome.mutate({"risk_rules": risk_rules})
