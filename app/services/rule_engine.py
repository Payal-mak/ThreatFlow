"""Dynamic rule engine: ReconciledEvent-like mapping -> RiskResult.

Decoupled from any specific ReconciledEvent class on purpose: the
correlation layer's final schema is still in progress on another branch,
so this engine accepts anything mapping-shaped (dict, Pydantic model,
or plain object) and adapts it internally. Swapping in the real
ReconciledEvent later requires no changes here.
"""

from pathlib import Path
from typing import Any, Mapping

from app.rules.conditions import evaluate_condition
from app.rules.loader import RuleConfigError, load_ruleset
from app.rules.models import RuleSet
from app.schemas.risk_result import RiskResult, RuleEvaluationTrace

_EVENT_ID_FIELDS = ("event_id", "correlation_key", "group_id", "id")


def _coerce_to_mapping(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    if hasattr(event, "model_dump"):
        return event.model_dump()
    if hasattr(event, "dict"):
        return event.dict()
    if hasattr(event, "__dict__"):
        return dict(vars(event))
    raise TypeError(f"unsupported event type for rule evaluation: {type(event)!r}")


def _extract_event_id(mapping: dict[str, Any]) -> str:
    for key in _EVENT_ID_FIELDS:
        value = mapping.get(key)
        if value is not None:
            return str(value)
    return "UNKNOWN"


class RuleEngine:
    """Loads a RuleSet from disk and evaluates events against it.

    auto_reload=True (default) means every evaluate_event() call first checks
    whether the rules file changed on disk and reloads it if so — this is
    the "hot reload without redeployment" mechanism. A malformed file on
    reload is logged (last_reload_error) and the previous valid ruleset is
    kept, so a bad edit never takes down evaluation.
    """

    def __init__(self, rules_path: str | Path, auto_reload: bool = True) -> None:
        self._path = Path(rules_path)
        self._auto_reload = auto_reload
        self._ruleset: RuleSet = load_ruleset(self._path)
        self._mtime = self._path.stat().st_mtime
        self.last_reload_error: str | None = None

    @property
    def ruleset(self) -> RuleSet:
        return self._ruleset

    def reload(self) -> None:
        """Force a reload; raises RuleConfigError if the file is invalid."""
        self._ruleset = load_ruleset(self._path)
        self._mtime = self._path.stat().st_mtime
        self.last_reload_error = None

    def reload_if_modified(self) -> bool:
        """Reload only if the file's mtime changed. Returns True if reloaded.

        A malformed file is reported via last_reload_error rather than raised,
        so callers evaluating events in a hot path never crash on a bad edit.
        """
        try:
            current_mtime = self._path.stat().st_mtime
        except OSError:
            return False
        if current_mtime == self._mtime:
            return False
        try:
            self.reload()
            return True
        except RuleConfigError as exc:
            self.last_reload_error = str(exc)
            self._mtime = current_mtime
            return False

    def evaluate_event(self, event: Any) -> RiskResult:
        if self._auto_reload:
            self.reload_if_modified()

        mapping = _coerce_to_mapping(event)
        event_id = _extract_event_id(mapping)

        trace: list[RuleEvaluationTrace] = []
        matched_rules: list[RuleEvaluationTrace] = []
        total_score = 0.0

        for rule in self._ruleset.rules:
            if not rule.enabled:
                continue
            result = evaluate_condition(rule.condition, mapping)
            entry = RuleEvaluationTrace(
                rule_id=rule.id,
                version=rule.version,
                matched=result.matched,
                score=rule.score if result.matched else 0.0,
                reason=result.detail,
                missing_fields=result.missing_fields,
            )
            trace.append(entry)
            if result.matched:
                matched_rules.append(entry)
                total_score += rule.score

        return RiskResult(
            event_id=event_id,
            risk_score=total_score,
            risk_level=self._resolve_level(total_score),
            matched_rules=matched_rules,
            evaluation_trace=trace,
            rule_version=self._ruleset.ruleset_version,
        )

    def _resolve_level(self, score: float) -> str:
        for band in sorted(self._ruleset.risk_levels, key=lambda b: b.min):
            if score >= band.min and (band.max is None or score <= band.max):
                return band.level
        return "UNKNOWN"
