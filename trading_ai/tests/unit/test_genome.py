from __future__ import annotations

import json
import pytest

from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.genome.mutator import StrategyMutator, PROTECTED_KEYS


@pytest.fixture
def genome():
    return StrategyGenome(
        strategy_id="test-001",
        version=1,
        name="Test Strategy",
        market=["EURUSD"],
        timeframes=["H4", "H1"],
        entry_rules=[{"type": "supply_demand", "params": {"lookback": 50}}],
        exit_rules=[{"type": "risk_reward", "params": {"min_rr": 3.0}}],
        risk_rules=[{"type": "fixed_fraction", "params": {"risk": 0.01}}],
        filters=[],
    )


@pytest.fixture
def mutator():
    return StrategyMutator()


class TestSerialization:
    def test_to_json_produces_valid_json(self, genome):
        raw = genome.to_json()
        data = json.loads(raw)
        assert data["strategy_id"] == "test-001"
        assert data["version"] == 1

    def test_from_json_roundtrip(self, genome):
        raw = genome.to_json()
        restored = StrategyGenome.from_json(raw)
        assert restored.strategy_id == genome.strategy_id
        assert restored.version == genome.version
        assert restored.market == genome.market
        assert restored.entry_rules == genome.entry_rules

    def test_json_preserves_nested_data(self, genome):
        raw = genome.to_json()
        data = json.loads(raw)
        assert data["entry_rules"][0]["params"]["lookback"] == 50

    def test_to_json_with_metadata(self, genome):
        genome.metadata["custom_key"] = "custom_value"
        raw = genome.to_json()
        data = json.loads(raw)
        assert data["metadata"]["custom_key"] == "custom_value"


class TestMutation:
    def test_mutation_creates_child(self, genome):
        child = genome.mutate({"name": "Mutated Strategy"})
        assert child.strategy_id != genome.strategy_id
        assert child.parent_id == genome.strategy_id
        assert child.version == 2
        assert child.name == "Mutated Strategy"

    def test_mutation_preserves_original(self, genome):
        original_id = genome.strategy_id
        genome.mutate({"name": "Changed"})
        assert genome.strategy_id == original_id
        assert genome.name == "Test Strategy"

    def test_mutation_updates_metadata(self, genome):
        child = genome.mutate({})
        assert "mutated_from" in child.metadata
        assert child.metadata["mutated_from"] == genome.strategy_id
        assert "mutation_time" in child.metadata

    def test_mutation_entry_rules(self, genome):
        new_rules = [{"type": "breakout", "params": {"period": 20}}]
        child = genome.mutate({"entry_rules": new_rules})
        assert child.entry_rules == new_rules
        assert genome.entry_rules != new_rules

    def test_mutation_filters(self, genome):
        new_filters = [{"type": "volatility", "params": {"min_atr": 0.001}}]
        child = genome.mutate({"filters": new_filters})
        assert child.filters == new_filters

    def test_mutation_version_increments(self, genome):
        child1 = genome.mutate({})
        child2 = child1.mutate({})
        assert child2.version == 3


class TestImmutability:
    def test_clone_is_independent(self, genome):
        clone = genome.clone()
        clone.name = "Changed"
        clone.entry_rules.append({"type": "new", "params": {}})
        assert genome.name == "Test Strategy"
        assert len(genome.entry_rules) == 1

    def test_original_not_affected_by_mutation(self, genome):
        original_rules = list(genome.entry_rules)
        genome.mutate({"entry_rules": [{"type": "other", "params": {}}]})
        assert genome.entry_rules == original_rules

    def test_deep_copy_of_lists(self, genome):
        child = genome.mutate({})
        child.entry_rules[0]["params"]["lookback"] = 999
        assert genome.entry_rules[0]["params"]["lookback"] == 50


class TestMutator:
    def test_protected_key_rejected(self, genome, mutator):
        with pytest.raises(ValueError, match="protected"):
            mutator.mutate_entry_filter(genome, {"broker_credentials": "hack"})

    def test_add_volatility_filter(self, genome, mutator):
        child = mutator.mutate_volatility_filter(genome, {"min_atr": 0.001, "max_atr": 0.01})
        vol_filters = [f for f in child.filters if f["type"] == "volatility"]
        assert len(vol_filters) == 1
        assert vol_filters[0]["params"]["min_atr"] == 0.001

    def test_upsert_volatility_filter(self, genome, mutator):
        child1 = mutator.mutate_volatility_filter(genome, {"min_atr": 0.001})
        child2 = mutator.mutate_volatility_filter(child1, {"max_atr": 0.01})
        vol_filters = [f for f in child2.filters if f["type"] == "volatility"]
        assert len(vol_filters) == 1
        assert vol_filters[0]["params"]["min_atr"] == 0.001
        assert vol_filters[0]["params"]["max_atr"] == 0.01

    def test_add_session_filter(self, genome, mutator):
        child = mutator.mutate_session_filter(genome, {"allowed": ["london", "new_york"]})
        session_filters = [f for f in child.filters if f["type"] == "session"]
        assert len(session_filters) == 1

    def test_mutate_stop_placement(self, genome, mutator):
        child = mutator.mutate_stop_placement(genome, {"method": "structure"})
        stop_rules = [r for r in child.risk_rules if r["type"] == "stop_placement"]
        assert len(stop_rules) == 1
        assert stop_rules[0]["params"]["method"] == "structure"
