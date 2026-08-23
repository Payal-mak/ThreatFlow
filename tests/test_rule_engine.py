"""Tests for the rule DSL, condition evaluator, and RuleEngine.

Uses local mock ReconciledEvent-shaped dicts (plain dicts, following the
agreed field contract) rather than importing Srinivas's in-progress
correlation module, per the "don't wait on unfinished branches" instruction.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.rules.conditions import evaluate_condition
from app.rules.loader import RuleConfigError, load_ruleset
from app.rules.models import Condition, Rule, RuleSet
from app.services.rule_engine import RuleEngine

CONFIG_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.json"


# ---------------------------------------------------------------------------
# Mock ReconciledEvent fixtures
# ---------------------------------------------------------------------------

def make_event(**overrides: Any) -> dict[str, Any]:
    """A mock ReconciledEvent following the agreed foundation contract:
    an event_id plus canonical fraud-relevant fields resolved by correlation.
    """
    base: dict[str, Any] = {
        "event_id": "evt-001",
        "account_id": "acc-123",
        "amount": 5000,
        "currency": "USD",
        "location": "NY",
        "location_mismatch": False,
        "source_risk_score": 20,
        "tags": ["ecommerce"],
    }
    base.update(overrides)
    return base


def condition(**kwargs: Any) -> Condition:
    return Condition.model_validate(kwargs)


# ---------------------------------------------------------------------------
# 1-8: individual operators
# ---------------------------------------------------------------------------

def test_operator_greater_than() -> None:
    cond = condition(field="amount", op=">", value=10000)
    assert evaluate_condition(cond, make_event(amount=15000)).matched is True
    assert evaluate_condition(cond, make_event(amount=10000)).matched is False


def test_operator_greater_than_or_equal() -> None:
    cond = condition(field="amount", op=">=", value=10000)
    assert evaluate_condition(cond, make_event(amount=10000)).matched is True
    assert evaluate_condition(cond, make_event(amount=9999)).matched is False


def test_operator_less_than() -> None:
    cond = condition(field="amount", op="<", value=100)
    assert evaluate_condition(cond, make_event(amount=50)).matched is True
    assert evaluate_condition(cond, make_event(amount=100)).matched is False


def test_operator_less_than_or_equal() -> None:
    cond = condition(field="amount", op="<=", value=100)
    assert evaluate_condition(cond, make_event(amount=100)).matched is True
    assert evaluate_condition(cond, make_event(amount=101)).matched is False


def test_operator_equal() -> None:
    cond = condition(field="location_mismatch", op="==", value=True)
    assert evaluate_condition(cond, make_event(location_mismatch=True)).matched is True
    assert evaluate_condition(cond, make_event(location_mismatch=False)).matched is False


def test_operator_not_equal() -> None:
    cond = condition(field="currency", op="!=", value="USD")
    assert evaluate_condition(cond, make_event(currency="EUR")).matched is True
    assert evaluate_condition(cond, make_event(currency="USD")).matched is False


def test_operator_in() -> None:
    cond = condition(field="currency", op="IN", value=["USD", "EUR"])
    assert evaluate_condition(cond, make_event(currency="USD")).matched is True
    assert evaluate_condition(cond, make_event(currency="INR")).matched is False


def test_operator_contains() -> None:
    cond = condition(field="tags", op="CONTAINS", value="chargeback_history")
    assert evaluate_condition(cond, make_event(tags=["chargeback_history", "vip"])).matched is True
    assert evaluate_condition(cond, make_event(tags=["vip"])).matched is False


# ---------------------------------------------------------------------------
# 9-11: boolean combinators
# ---------------------------------------------------------------------------

def test_and_condition() -> None:
    cond = condition(
        AND=[
            {"field": "amount", "op": ">", "value": 10000},
            {"field": "location_mismatch", "op": "==", "value": True},
        ]
    )
    assert evaluate_condition(cond, make_event(amount=15000, location_mismatch=True)).matched is True
    assert evaluate_condition(cond, make_event(amount=15000, location_mismatch=False)).matched is False


def test_or_condition() -> None:
    cond = condition(
        OR=[
            {"field": "amount", "op": ">", "value": 10000},
            {"field": "source_risk_score", "op": ">=", "value": 90},
        ]
    )
    assert evaluate_condition(cond, make_event(amount=15000, source_risk_score=10)).matched is True
    assert evaluate_condition(cond, make_event(amount=100, source_risk_score=95)).matched is True
    assert evaluate_condition(cond, make_event(amount=100, source_risk_score=10)).matched is False


def test_nested_and_or_condition() -> None:
    # (amount > 10000 AND location_mismatch == true) OR source_risk_score >= 90
    cond = condition(
        OR=[
            {
                "AND": [
                    {"field": "amount", "op": ">", "value": 10000},
                    {"field": "location_mismatch", "op": "==", "value": True},
                ]
            },
            {"field": "source_risk_score", "op": ">=", "value": 90},
        ]
    )
    assert evaluate_condition(
        cond, make_event(amount=15000, location_mismatch=True, source_risk_score=0)
    ).matched is True
    assert evaluate_condition(
        cond, make_event(amount=100, location_mismatch=False, source_risk_score=95)
    ).matched is True
    assert evaluate_condition(
        cond, make_event(amount=15000, location_mismatch=False, source_risk_score=0)
    ).matched is False


# ---------------------------------------------------------------------------
# 12-13: missing / null fields never crash
# ---------------------------------------------------------------------------

def test_missing_field_is_safe_and_recorded() -> None:
    cond = condition(field="amount", op=">", value=10000)
    event = make_event()
    del event["amount"]
    result = evaluate_condition(cond, event)
    assert result.matched is False
    assert "amount" in result.missing_fields
    assert "unavailable" in result.detail


def test_null_field_is_safe_and_recorded() -> None:
    cond = condition(field="amount", op=">", value=10000)
    result = evaluate_condition(cond, make_event(amount=None))
    assert result.matched is False
    assert "amount" in result.missing_fields


def test_missing_field_inside_and_does_not_crash() -> None:
    cond = condition(
        AND=[
            {"field": "amount", "op": ">", "value": 10000},
            {"field": "location_mismatch", "op": "==", "value": True},
        ]
    )
    event = make_event(location_mismatch=True)
    del event["amount"]
    result = evaluate_condition(cond, event)
    assert result.matched is False
    assert "amount" in result.missing_fields


# ---------------------------------------------------------------------------
# 14-17: engine-level scoring, matching, and risk levels (demo rules.json)
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine() -> RuleEngine:
    return RuleEngine(CONFIG_RULES_PATH, auto_reload=False)


def test_multiple_matching_rules(engine: RuleEngine) -> None:
    result = engine.evaluate_event(make_event(amount=15000, location_mismatch=True))
    matched_ids = {r.rule_id for r in result.matched_rules}
    assert {"HIGH_AMOUNT", "LOCATION_MISMATCH", "HIGH_AMOUNT_LOCATION_MISMATCH"} <= matched_ids
    assert len(result.matched_rules) >= 3


def test_no_matching_rules(engine: RuleEngine) -> None:
    result = engine.evaluate_event(make_event(amount=100, location_mismatch=False, source_risk_score=10))
    assert result.matched_rules == []
    assert result.risk_score == 0
    assert result.risk_level == "LOW"


def test_risk_score_calculation(engine: RuleEngine) -> None:
    # HIGH_AMOUNT (40) + LOCATION_MISMATCH (40) + HIGH_AMOUNT_LOCATION_MISMATCH (80) = 160
    result = engine.evaluate_event(make_event(amount=15000, location_mismatch=True, source_risk_score=0))
    assert result.risk_score == 160


@pytest.mark.parametrize(
    ("score", "expected_level"),
    [(0, "LOW"), (29, "LOW"), (30, "MEDIUM"), (59, "MEDIUM"), (60, "HIGH"), (79, "HIGH"), (80, "CRITICAL"), (500, "CRITICAL")],
)
def test_risk_level_calculation(engine: RuleEngine, score: float, expected_level: str) -> None:
    assert engine._resolve_level(score) == expected_level


def test_risk_result_event_id_extraction(engine: RuleEngine) -> None:
    result = engine.evaluate_event(make_event(event_id="evt-777"))
    assert result.event_id == "evt-777"


def test_engine_accepts_plain_object_not_just_dict(engine: RuleEngine) -> None:
    class FakeReconciledEvent:
        def __init__(self) -> None:
            self.event_id = "evt-obj"
            self.amount = 15000
            self.location_mismatch = True
            self.source_risk_score = 0

    result = engine.evaluate_event(FakeReconciledEvent())
    assert result.event_id == "evt-obj"
    assert result.risk_score > 0


# ---------------------------------------------------------------------------
# 18: hot reload
# ---------------------------------------------------------------------------

def _write_ruleset(path: Path, score: float) -> None:
    payload = {
        "ruleset_version": 1,
        "rules": [
            {
                "id": "TEST_RULE",
                "version": 1,
                "score": score,
                "condition": {"field": "amount", "op": ">", "value": 100},
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_rule_reload_picks_up_file_changes(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    _write_ruleset(rules_path, score=10)

    live_engine = RuleEngine(rules_path, auto_reload=True)
    result_before = live_engine.evaluate_event(make_event(amount=1000))
    assert result_before.risk_score == 10

    _write_ruleset(rules_path, score=50)
    # Force a distinct mtime regardless of filesystem timestamp resolution.
    new_mtime = rules_path.stat().st_mtime + 5
    import os

    os.utime(rules_path, (new_mtime, new_mtime))

    result_after = live_engine.evaluate_event(make_event(amount=1000))
    assert result_after.risk_score == 50


def test_reload_if_modified_returns_false_when_unchanged(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    _write_ruleset(rules_path, score=10)
    live_engine = RuleEngine(rules_path, auto_reload=False)
    assert live_engine.reload_if_modified() is False


# ---------------------------------------------------------------------------
# 19: malformed rule configuration
# ---------------------------------------------------------------------------

def test_malformed_json_raises_rule_config_error(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(RuleConfigError):
        load_ruleset(rules_path)


def test_unsupported_operator_raises_rule_config_error(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    payload = {
        "rules": [
            {
                "id": "BAD_RULE",
                "score": 10,
                "condition": {"field": "amount", "op": "BETWEEN", "value": 10},
            }
        ]
    }
    rules_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuleConfigError):
        load_ruleset(rules_path)


def test_duplicate_rule_ids_raise_rule_config_error(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    payload = {
        "rules": [
            {"id": "DUP", "score": 10, "condition": {"field": "amount", "op": ">", "value": 1}},
            {"id": "DUP", "score": 20, "condition": {"field": "amount", "op": ">", "value": 2}},
        ]
    }
    rules_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuleConfigError):
        load_ruleset(rules_path)


def test_reload_keeps_previous_ruleset_on_malformed_edit(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    _write_ruleset(rules_path, score=10)
    live_engine = RuleEngine(rules_path, auto_reload=True)

    rules_path.write_text("{ broken", encoding="utf-8")
    new_mtime = rules_path.stat().st_mtime + 5
    import os

    os.utime(rules_path, (new_mtime, new_mtime))

    result = live_engine.evaluate_event(make_event(amount=1000))
    assert result.risk_score == 10  # previous valid ruleset still in effect
    assert live_engine.last_reload_error is not None


# ---------------------------------------------------------------------------
# 20: deterministic repeated evaluation
# ---------------------------------------------------------------------------

def test_deterministic_repeated_evaluation(engine: RuleEngine) -> None:
    event = make_event(amount=15000, location_mismatch=True, source_risk_score=80)
    first = engine.evaluate_event(event)
    second = engine.evaluate_event(event)
    assert first.model_dump() == second.model_dump()


def test_demo_rules_file_is_loadable_and_valid() -> None:
    ruleset: RuleSet = load_ruleset(CONFIG_RULES_PATH)
    rule_ids = {rule.id for rule in ruleset.rules}
    assert {
        "HIGH_AMOUNT",
        "LOCATION_MISMATCH",
        "HIGH_AMOUNT_LOCATION_MISMATCH",
        "HIGH_SOURCE_RISK",
    } <= rule_ids
