"""Loads and validates a RuleSet from a JSON or YAML configuration file."""

import json
from pathlib import Path

from pydantic import ValidationError

from app.rules.models import RuleSet


class RuleConfigError(Exception):
    """Raised when a rules configuration file is missing, malformed, or invalid."""


def load_ruleset(path: str | Path) -> RuleSet:
    file_path = Path(path)

    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleConfigError(f"cannot read rules file '{file_path}': {exc}") from exc

    try:
        if file_path.suffix.lower() in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(raw_text)
        else:
            data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuleConfigError(f"invalid JSON in rules file '{file_path}': {exc}") from exc
    except Exception as exc:  # covers yaml.YAMLError without importing yaml eagerly
        raise RuleConfigError(f"invalid rules file '{file_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise RuleConfigError(
            f"rules file '{file_path}' must contain a JSON/YAML object at the top level"
        )

    try:
        return RuleSet.model_validate(data)
    except ValidationError as exc:
        raise RuleConfigError(
            f"invalid rule configuration in '{file_path}': {exc}"
        ) from exc
