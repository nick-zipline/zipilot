"""Tests for the smart ``spec`` command and its helpers."""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipilot.cli import (
    _explore_codebase_with_codex,
    _generate_plan_with_codex,
    _parse_plan_output,
    _run_codex_prompt,
    _write_plan_markdown,
    cmd_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_codex_jsonl(*messages: str) -> str:
    """Build fake ``codex exec --json`` JSONL stdout from assistant messages."""
    lines = []
    for msg in messages:
        obj = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": msg},
        }
        lines.append(json.dumps(obj))
    return "\n".join(lines)


def _completed_process(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a fake subprocess.CompletedProcess."""
    return MagicMock(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def _mock_popen(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a mock Popen that yields *stdout* lines for streaming mode."""
    mock_proc = MagicMock()
    mock_proc.stdout = iter(stdout.splitlines(keepends=True))
    mock_proc.stderr.read.return_value = stderr
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = None
    return mock_proc


# ---------------------------------------------------------------------------
# _run_codex_prompt
# ---------------------------------------------------------------------------

class TestRunCodexPrompt:
    def test_parses_agent_message(self):
        stdout = _make_codex_jsonl("Hello from codex")
        with patch("zipilot.cli.subprocess.run", return_value=_completed_process(stdout)):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert err == ""
        assert "Hello from codex" in text

    def test_joins_multiple_messages(self):
        stdout = _make_codex_jsonl("Part 1", "Part 2")
        with patch("zipilot.cli.subprocess.run", return_value=_completed_process(stdout)):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert "Part 1" in text
        assert "Part 2" in text

    def test_ignores_non_agent_events(self):
        other = json.dumps({"type": "session.start", "session_id": "abc"})
        agent = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "useful"},
        })
        stdout = f"{other}\n{agent}\n"
        with patch("zipilot.cli.subprocess.run", return_value=_completed_process(stdout)):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert text.strip() == "useful"

    def test_returns_error_on_nonzero_exit(self):
        with patch("zipilot.cli.subprocess.run", return_value=_completed_process("", returncode=1, stderr="boom")):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert text == ""
        assert "boom" in err

    def test_returns_error_on_codex_not_found(self):
        with patch("zipilot.cli.subprocess.run", side_effect=FileNotFoundError):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert "not found" in err

    def test_returns_error_on_timeout(self):
        import subprocess
        with patch("zipilot.cli.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 120)):
            text, err = _run_codex_prompt("test", "/tmp", "model", timeout=120)
        assert "timed out" in err

    def test_returns_error_on_empty_output(self):
        stdout = json.dumps({"type": "session.start"}) + "\n"
        with patch("zipilot.cli.subprocess.run", return_value=_completed_process(stdout)):
            text, err = _run_codex_prompt("test", "/tmp", "model")
        assert "no output" in err


class TestRunCodexPromptStreaming:
    def test_streams_agent_messages(self, capsys):
        """Streaming mode prints each agent message and collects them."""
        lines = [
            json.dumps({"type": "session.start", "session_id": "s1"}) + "\n",
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Exploring..."},
            }) + "\n",
            json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Found files"},
            }) + "\n",
        ]

        mock_proc = MagicMock()
        mock_proc.stdout = iter(lines)
        mock_proc.stderr.read.return_value = ""
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None

        with patch("zipilot.cli.subprocess.Popen", return_value=mock_proc):
            text, err = _run_codex_prompt("test", "/tmp", "model", stream=True)

        assert err == ""
        assert "Exploring..." in text
        assert "Found files" in text
        captured = capsys.readouterr()
        assert "Exploring..." in captured.out
        assert "Found files" in captured.out

    def test_stream_codex_not_found(self):
        with patch("zipilot.cli.subprocess.Popen", side_effect=FileNotFoundError):
            text, err = _run_codex_prompt("test", "/tmp", "model", stream=True)
        assert "not found" in err

    def test_stream_timeout(self):
        import subprocess as _subprocess

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.stderr.read.return_value = ""
        # First call (with timeout) raises; second call (after kill) succeeds
        mock_proc.wait.side_effect = [
            _subprocess.TimeoutExpired("codex", 120),
            None,
        ]
        mock_proc.kill.return_value = None

        with patch("zipilot.cli.subprocess.Popen", return_value=mock_proc):
            text, err = _run_codex_prompt("test", "/tmp", "model", stream=True)
        assert "timed out" in err
        mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# _parse_plan_output
# ---------------------------------------------------------------------------

class TestParsePlanOutput:
    def test_parses_full_plan(self):
        text = textwrap.dedent("""\
            SUMMARY: Refactor the auth module to use JWT tokens.

            STEP 1: Add JWT dependency
            FILES: requirements.txt, setup.cfg
            PROMPT: Add PyJWT to requirements.txt and setup.cfg dependencies

            STEP 2: Create token service
            FILES: src/auth/tokens.py
            PROMPT: Create a new token service that generates and validates JWT tokens

            STEP 3: Update login endpoint
            FILES: src/api/login.py, src/api/middleware.py
            PROMPT: Update the login endpoint to return JWT tokens and add middleware
        """)
        steps, summary = _parse_plan_output(text, step_count=5)
        assert summary == "Refactor the auth module to use JWT tokens."
        assert len(steps) == 3
        assert steps[0]["description"] == "Add JWT dependency"
        assert "requirements.txt" in steps[0]["files"]
        assert "PyJWT" in steps[0]["codex_prompt"]
        assert steps[2]["description"] == "Update login endpoint"

    def test_respects_step_count_limit(self):
        text = textwrap.dedent("""\
            STEP 1: First
            FILES: a.py
            PROMPT: Do first thing

            STEP 2: Second
            FILES: b.py
            PROMPT: Do second thing

            STEP 3: Third
            FILES: c.py
            PROMPT: Do third thing
        """)
        steps, _ = _parse_plan_output(text, step_count=2)
        assert len(steps) == 2

    def test_handles_missing_summary(self):
        text = textwrap.dedent("""\
            STEP 1: Only step
            FILES: main.py
            PROMPT: Do the thing
        """)
        steps, summary = _parse_plan_output(text, step_count=5)
        assert summary == ""
        assert len(steps) == 1

    def test_empty_input_returns_empty(self):
        steps, summary = _parse_plan_output("", step_count=5)
        assert steps == []
        assert summary == ""


# ---------------------------------------------------------------------------
# _generate_plan_with_codex
# ---------------------------------------------------------------------------

class TestGeneratePlanWithCodex:
    def test_returns_parsed_steps(self):
        plan_text = textwrap.dedent("""\
            SUMMARY: Add health endpoint.

            STEP 1: Create route
            FILES: src/routes.py
            PROMPT: Add GET /health route returning 200

            STEP 2: Add test
            FILES: tests/test_routes.py
            PROMPT: Write test for health endpoint
        """)
        stdout = _make_codex_jsonl(plan_text)
        with patch("zipilot.cli.subprocess.Popen", return_value=_mock_popen(stdout)):
            steps, summary, err = _generate_plan_with_codex(
                "Add health endpoint", "project uses Flask", "/tmp", "model",
            )
        assert err == ""
        assert summary == "Add health endpoint."
        assert len(steps) == 2
        assert steps[0]["description"] == "Create route"

    def test_returns_error_when_codex_fails(self):
        with patch("zipilot.cli.subprocess.Popen", return_value=_mock_popen("", returncode=1, stderr="fail")):
            steps, summary, err = _generate_plan_with_codex(
                "goal", "context", "/tmp", "model",
            )
        assert steps == []
        assert "fail" in err

    def test_truncates_long_exploration_context(self):
        long_context = "x" * 10000
        plan_text = "STEP 1: Do it\nFILES: a.py\nPROMPT: Just do it"
        stdout = _make_codex_jsonl(plan_text)
        with patch("zipilot.cli.subprocess.Popen", return_value=_mock_popen(stdout)) as mock_popen_call:
            _generate_plan_with_codex("goal", long_context, "/tmp", "model")
            # Verify the prompt passed to codex has truncated context
            prompt_arg = mock_popen_call.call_args[0][0][-1]  # last arg is the prompt
            assert "truncated" in prompt_arg


# ---------------------------------------------------------------------------
# _write_plan_markdown
# ---------------------------------------------------------------------------

class TestWritePlanMarkdown:
    def test_creates_plan_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        steps = [
            {"description": "First step", "files": ["a.py"], "codex_prompt": "Do A"},
            {"description": "Second step", "files": [], "codex_prompt": None},
        ]
        exit_conds = [{"type": "command", "command": "pytest -q"}]

        path = _write_plan_markdown(
            goal="Test goal",
            summary="Test summary",
            exploration_text="Found stuff",
            steps=steps,
            exit_conditions=exit_conds,
        )

        assert path.exists()
        content = path.read_text()
        assert "# Plan: Test goal" in content
        assert "Test summary" in content
        assert "Found stuff" in content
        assert "First step" in content
        assert "a.py" in content
        assert "Do A" in content
        assert "Second step" in content
        assert "pytest -q" in content

    def test_plan_in_docs_plans_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_plan_markdown(
            goal="My goal",
            summary="",
            exploration_text="",
            steps=[{"description": "step", "files": [], "codex_prompt": None}],
            exit_conditions=[],
        )
        assert "docs/plans" in str(path)


# ---------------------------------------------------------------------------
# cmd_spec — integration-style tests with mocked I/O
# ---------------------------------------------------------------------------

class TestCmdSpec:
    def _make_args(self, **overrides):
        defaults = {
            "config": None,
            "prompt": "Add health endpoint",
            "output": None,
            "exit_condition": "pytest -q",
            "playwright_url": None,
            "assertions": None,
            "working_directory": None,
            "model": None,
            "max_retries": 3,
            "run": False,
            "steps": 5,
            "no_explore": False,
            "no_plan_file": False,
            "verbose": False,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_full_flow_explore_and_plan(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        explore_result = "Project uses Flask with blueprints in src/routes/"
        plan_text = textwrap.dedent("""\
            SUMMARY: Add /health endpoint.

            STEP 1: Create route
            FILES: src/routes.py
            PROMPT: Add GET /health route

            STEP 2: Add test
            FILES: tests/test_routes.py
            PROMPT: Write test for health endpoint
        """)

        # First call: explore. Second call: plan.
        mock_codex.side_effect = [
            (explore_result, ""),
            (plan_text, ""),
        ]
        # Accept plan, output path, don't run
        mock_prompt.side_effect = ["y", str(tmp_path / "specs/test.yaml"), "3", "n"]

        args = self._make_args()
        result = cmd_spec(args)

        assert result == 0
        assert (tmp_path / "specs/test.yaml").exists()
        # Plan markdown should also be written
        plans = list((tmp_path / "docs/plans").glob("*.md"))
        assert len(plans) == 1

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_explore_fails_falls_back_to_simple(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        simple_steps = "- Create the endpoint\n- Add tests\n- Update docs"
        # Explore fails, then simple planning succeeds
        mock_codex.side_effect = [
            ("", "codex timed out"),   # explore fails
            (simple_steps, ""),         # simple planning succeeds
        ]
        mock_prompt.side_effect = ["y", str(tmp_path / "specs/test.yaml"), "3", "n"]

        args = self._make_args()
        result = cmd_spec(args)

        assert result == 0
        assert (tmp_path / "specs/test.yaml").exists()
        # No markdown plan since exploration failed
        assert not (tmp_path / "docs/plans").exists()

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_no_explore_flag_skips_exploration(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        simple_steps = "- Step one\n- Step two"
        mock_codex.return_value = (simple_steps, "")
        mock_prompt.side_effect = ["y", str(tmp_path / "specs/test.yaml"), "3", "n"]

        args = self._make_args(no_explore=True)
        result = cmd_spec(args)

        assert result == 0
        # Should only call codex once (simple planning, no explore)
        assert mock_codex.call_count == 1

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_all_codex_fails_falls_back_to_manual(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mock_codex.return_value = ("", "codex error")
        # Manual steps, then accept, output, retries, don't run
        mock_prompt.side_effect = [
            "Manual step one",   # Step 1 description
            "",                  # End manual entry
            "y",                 # Accept plan
            str(tmp_path / "specs/test.yaml"),  # Output
            "3",                 # Max retries
            "n",                 # Don't run
        ]

        args = self._make_args()
        result = cmd_spec(args)

        assert result == 0
        assert (tmp_path / "specs/test.yaml").exists()

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_abort_on_reject(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        simple_steps = "- Step one\n- Step two"
        mock_codex.return_value = (simple_steps, "")
        mock_prompt.side_effect = ["n"]  # Reject plan

        args = self._make_args(no_explore=True)
        result = cmd_spec(args)

        assert result == 1

    @patch("zipilot.cli._drain_stdin")
    def test_no_prompt_returns_error(self, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("zipilot.cli._prompt", return_value=""):
            args = self._make_args(prompt=None)
            result = cmd_spec(args)
        assert result == 1

    @patch("zipilot.cli._drain_stdin")
    @patch("zipilot.cli._prompt")
    @patch("zipilot.cli._run_codex_prompt")
    def test_comma_separated_working_dirs(self, mock_codex, mock_prompt, _drain, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        simple_steps = "- Step one\n- Step two"
        mock_codex.return_value = (simple_steps, "")
        output_path = str(tmp_path / "specs/test.yaml")
        mock_prompt.side_effect = ["y", output_path, "3", "n"]

        args = self._make_args(no_explore=True, working_directory="~/a,~/b")
        result = cmd_spec(args)

        assert result == 0
        import yaml
        with open(output_path) as f:
            raw = yaml.safe_load(f)
        assert raw["context"]["working_directory"] == ["~/a", "~/b"]
