"""Spec YAML parser and validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ExitCondition:
    type: str  # "command" or "playwright"
    command: str | None = None
    expect_exit_code: int = 0
    url: str | None = None
    assertions: list[str] = field(default_factory=list)


@dataclass
class Step:
    id: str
    description: str
    codex_prompt: str | None = None


@dataclass
class SpecContext:
    working_directories: list[str] = field(default_factory=lambda: ["~/github/cloud"])
    model: str = "gpt-5.3-codex"

    @property
    def working_directory(self) -> str:
        """Primary working directory (first in the list)."""
        return self.working_directories[0]


@dataclass
class Spec:
    version: int
    goal: str
    steps: list[Step]
    exit_conditions: list[ExitCondition]
    max_retries: int = 3
    context: SpecContext = field(default_factory=SpecContext)

    @property
    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]


def _parse_exit_condition(raw: dict) -> ExitCondition:
    return ExitCondition(
        type=raw["type"],
        command=raw.get("command"),
        expect_exit_code=raw.get("expect_exit_code", 0),
        url=raw.get("url"),
        assertions=raw.get("assertions", []),
    )


def _parse_step(raw: dict) -> Step:
    return Step(
        id=raw["id"],
        description=raw["description"],
        codex_prompt=raw.get("codex_prompt"),
    )


def _parse_context(raw: dict | None) -> SpecContext:
    if raw is None:
        return SpecContext()
    wd = raw.get("working_directory", ["~/github/cloud"])
    if isinstance(wd, str):
        wd = [wd]
    return SpecContext(
        working_directories=wd,
        model=raw.get("model", "gpt-5.3-codex"),
    )


def load_spec(path: str | Path) -> Spec:
    """Load and parse a spec YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Spec file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _build_spec(raw)


def load_spec_str(text: str) -> Spec:
    """Parse a spec from a YAML string."""
    raw = yaml.safe_load(text)
    return _build_spec(raw)


def _build_spec(raw: dict) -> Spec:
    validate_raw(raw)
    return Spec(
        version=raw["version"],
        goal=raw["goal"],
        steps=[_parse_step(s) for s in raw["steps"]],
        exit_conditions=[_parse_exit_condition(e) for e in raw["exit_conditions"]],
        max_retries=raw.get("max_retries", 3),
        context=_parse_context(raw.get("context")),
    )


def validate_raw(raw: dict) -> None:
    """Validate raw YAML dict. Raises ValueError on problems."""
    errors: list[str] = []

    if not isinstance(raw, dict):
        raise ValueError("Spec must be a YAML mapping")

    for required in ("version", "goal", "steps", "exit_conditions"):
        if required not in raw:
            errors.append(f"Missing required field: {required}")

    if errors:
        raise ValueError("Spec validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    if raw.get("version") != 1:
        errors.append(f"Unsupported spec version: {raw.get('version')} (expected 1)")

    steps = raw.get("steps", [])
    if not isinstance(steps, list) or len(steps) == 0:
        errors.append("'steps' must be a non-empty list")
    else:
        step_ids = set()
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"Step {i} must be a mapping")
                continue
            if "id" not in step:
                errors.append(f"Step {i} missing 'id'")
            elif step["id"] in step_ids:
                errors.append(f"Duplicate step id: {step['id']}")
            else:
                step_ids.add(step["id"])
            if "description" not in step:
                errors.append(f"Step {i} missing 'description'")

    exit_conditions = raw.get("exit_conditions", [])
    if not isinstance(exit_conditions, list) or len(exit_conditions) == 0:
        errors.append("'exit_conditions' must be a non-empty list")
    else:
        for i, ec in enumerate(exit_conditions):
            if not isinstance(ec, dict):
                errors.append(f"Exit condition {i} must be a mapping")
                continue
            ec_type = ec.get("type")
            if ec_type not in ("command", "playwright"):
                errors.append(f"Exit condition {i}: unsupported type '{ec_type}'")
            if ec_type == "command" and "command" not in ec:
                errors.append(f"Exit condition {i}: 'command' type requires 'command' field")
            if ec_type == "playwright":
                if "url" not in ec:
                    errors.append(f"Exit condition {i}: 'playwright' type requires 'url' field")
                if not ec.get("assertions"):
                    errors.append(f"Exit condition {i}: 'playwright' type requires non-empty 'assertions'")

    if errors:
        raise ValueError("Spec validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
