"""Tests for spec parsing and validation."""

import pytest

from zipilot.spec import ExitCondition, Spec, Step, load_spec_str


VALID_SPEC = """\
version: 1
goal: "Test goal"
steps:
  - id: step1
    description: "First step"
    codex_prompt: "Do something"
  - id: step2
    description: "Second step"
exit_conditions:
  - type: command
    command: "echo ok"
    expect_exit_code: 0
max_retries: 2
context:
  working_directory: ~/github/cloud
  model: gpt-5.3-codex
"""


def test_load_valid_spec():
    spec = load_spec_str(VALID_SPEC)
    assert isinstance(spec, Spec)
    assert spec.version == 1
    assert spec.goal == "Test goal"
    assert len(spec.steps) == 2
    assert spec.steps[0].id == "step1"
    assert spec.steps[0].codex_prompt == "Do something"
    assert spec.steps[1].codex_prompt is None
    assert spec.max_retries == 2
    assert spec.context.working_directory == "~/github/cloud"
    assert spec.context.model == "gpt-5.3-codex"


def test_step_ids():
    spec = load_spec_str(VALID_SPEC)
    assert spec.step_ids == ["step1", "step2"]


def test_exit_condition_command():
    spec = load_spec_str(VALID_SPEC)
    assert len(spec.exit_conditions) == 1
    ec = spec.exit_conditions[0]
    assert ec.type == "command"
    assert ec.command == "echo ok"
    assert ec.expect_exit_code == 0


def test_playwright_exit_condition():
    yaml_str = """\
version: 1
goal: "UI test"
steps:
  - id: check
    description: "Check UI"
exit_conditions:
  - type: playwright
    url: "http://localhost:3000"
    assertions:
      - "Page loads"
      - "No errors"
"""
    spec = load_spec_str(yaml_str)
    ec = spec.exit_conditions[0]
    assert ec.type == "playwright"
    assert ec.url == "http://localhost:3000"
    assert len(ec.assertions) == 2


def test_missing_version():
    with pytest.raises(ValueError, match="Missing required field: version"):
        load_spec_str("goal: x\nsteps: []\nexit_conditions: []")


def test_missing_goal():
    with pytest.raises(ValueError, match="Missing required field: goal"):
        load_spec_str("version: 1\nsteps: []\nexit_conditions: []")


def test_empty_steps():
    with pytest.raises(ValueError, match="non-empty list"):
        load_spec_str("""\
version: 1
goal: "x"
steps: []
exit_conditions:
  - type: command
    command: "echo"
""")


def test_duplicate_step_ids():
    with pytest.raises(ValueError, match="Duplicate step id"):
        load_spec_str("""\
version: 1
goal: "x"
steps:
  - id: dup
    description: "first"
  - id: dup
    description: "second"
exit_conditions:
  - type: command
    command: "echo"
""")


def test_invalid_exit_condition_type():
    with pytest.raises(ValueError, match="unsupported type"):
        load_spec_str("""\
version: 1
goal: "x"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: unknown
""")


def test_playwright_missing_url():
    with pytest.raises(ValueError, match="requires 'url'"):
        load_spec_str("""\
version: 1
goal: "x"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: playwright
    assertions:
      - "check"
""")


def test_playwright_missing_assertions():
    with pytest.raises(ValueError, match="requires non-empty 'assertions'"):
        load_spec_str("""\
version: 1
goal: "x"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: playwright
    url: "http://localhost"
""")


def test_defaults():
    yaml_str = """\
version: 1
goal: "minimal"
steps:
  - id: s1
    description: "only step"
exit_conditions:
  - type: command
    command: "true"
"""
    spec = load_spec_str(yaml_str)
    assert spec.max_retries == 3  # default
    assert spec.context.working_directory == "~/github/cloud"
    assert spec.context.working_directories == ["~/github/cloud"]
    assert spec.context.model == "gpt-5.3-codex"


def test_working_directory_string_backward_compat():
    """A single string working_directory in YAML is normalized to a list."""
    yaml_str = """\
version: 1
goal: "compat"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: command
    command: "true"
context:
  working_directory: ~/my/project
"""
    spec = load_spec_str(yaml_str)
    assert spec.context.working_directories == ["~/my/project"]
    assert spec.context.working_directory == "~/my/project"


def test_working_directory_list():
    """A list working_directory in YAML is loaded as-is."""
    yaml_str = """\
version: 1
goal: "multi-dir"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: command
    command: "true"
context:
  working_directory:
    - ~/repo-a
    - ~/repo-b
"""
    spec = load_spec_str(yaml_str)
    assert spec.context.working_directories == ["~/repo-a", "~/repo-b"]
    assert spec.context.working_directory == "~/repo-a"


def test_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported spec version"):
        load_spec_str("""\
version: 99
goal: "x"
steps:
  - id: s1
    description: "step"
exit_conditions:
  - type: command
    command: "echo"
""")
