"""CLI entry point for zipilot."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from zipilot.config import load_config
from zipilot.fsm import FSMEngine
from zipilot.persistence import list_sessions, load_state
from zipilot.spec import load_spec
from zipilot.states import State
from zipilot.tools.registry import create_default_registry


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    spec = load_spec(args.spec)
    registry = create_default_registry()

    engine = FSMEngine(
        spec=spec,
        config=config,
        registry=registry,
        auto_approve=args.approve,
        spec_path=args.spec,
    )
    final = engine.run()

    if final == State.COMPLETED:
        print("\nAll done.")
        return 0
    elif final == State.NEEDS_INPUT:
        print("\nBlocked — resume with: zipilot resume")
        return 1
    else:
        print(f"\nStopped in state: {final.name}")
        return 1


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ps = load_state(config.sessions_path)

    if ps is None:
        print("No persisted state found. Nothing to resume.")
        return 1

    spec = load_spec(ps.spec_path) if ps.spec_path else None
    if spec is None:
        print("Cannot resume: spec path not found in persisted state.")
        return 1

    registry = create_default_registry()
    engine = FSMEngine(
        spec=spec,
        config=config,
        registry=registry,
        spec_path=ps.spec_path,
        session_id=ps.session_id,
    )
    final = engine.resume(ps)

    if final == State.COMPLETED:
        print("\nResumed and completed.")
        return 0
    else:
        print(f"\nStopped in state: {final.name}")
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    active = load_state(config.sessions_path)
    sessions = list_sessions(config.sessions_path)

    if not sessions:
        print("No sessions found.")
        return 0

    if active is None:
        print("No active session.")
    else:
        print(f"Active session: {active.session_id} ({active.state})")

    print(f"Stored sessions: {len(sessions)}")
    for session_id, ps in sessions:
        status = "active" if active and session_id == active.session_id else "completed" if ps.completed else "in-progress"
        print(
            f"- {session_id} [{status}] "
            f"state={ps.state} step={ps.step_index + 1} retries={ps.retry_count}"
        )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
        print(f"Valid spec: {spec.goal}")
        print(f"  Version:    {spec.version}")
        print(f"  Steps:      {len(spec.steps)}")
        for s in spec.steps:
            print(f"    - [{s.id}] {s.description}")
        print(f"  Exit conds: {len(spec.exit_conditions)}")
        print(f"  Max retries: {spec.max_retries}")
        return 0
    except (ValueError, FileNotFoundError) as e:
        print(f"Invalid spec: {e}", file=sys.stderr)
        return 1


def cmd_tools(args: argparse.Namespace) -> int:
    registry = create_default_registry()
    tools = registry.list_tools()
    print(f"Registered tools ({len(tools)}):")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description}")
    return 0


def _prompt(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _drain_stdin() -> None:
    """Drain any buffered stdin data (e.g. leftover lines from multiline paste).

    input() pulls the entire paste into Python's internal buffer in one read,
    so select() on the raw fd sees nothing.  We must drain at the Python
    buffer level by temporarily switching the fd to non-blocking mode.
    """
    import fcntl

    fd = sys.stdin.fileno()
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
        try:
            while sys.stdin.buffer.read1(4096):
                pass
        except (BlockingIOError, IOError):
            pass
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)


def _slugify_filename(goal: str) -> str:
    """Turn a goal string into a spec filename like specs/fix-flaky-tests.yaml."""
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")
    slug = slug[:50].rstrip("-")
    return f"specs/{slug or 'new-spec'}.yaml"


def _parse_codex_line(line: str) -> str | None:
    """Extract assistant message text from a single JSONL line, or ``None``."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if obj.get("type") == "item.completed":
        item = obj.get("item", {})
        if item.get("type") == "agent_message":
            text = item.get("text", "")
            if text:
                return text
    return None


def _run_codex_prompt(
    prompt: str,
    working_directory: str,
    model: str,
    timeout: int = 120,
    stream: bool = False,
) -> tuple[str, str]:
    """Run a prompt through ``codex exec --json`` and return assistant text.

    Returns (text, error). *error* is empty on success.

    When *stream* is ``True``, agent messages are printed to the terminal as
    they arrive so the user can follow progress in real-time.
    """
    log = logging.getLogger(__name__)
    cmd = [
        "codex",
        "exec",
        "--json",
        "--cd",
        str(Path(working_directory).expanduser()),
        "-m",
        model,
        "-s",
        "workspace-write",
        prompt,
    ]

    log.debug("Running: %s", " ".join(cmd))

    if stream:
        return _run_codex_prompt_streaming(cmd, timeout, log)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return "", "'codex' not found on PATH — install with: npm i -g @openai/codex"
    except subprocess.TimeoutExpired:
        return "", f"codex timed out after {timeout}s"

    log.debug("codex exit code: %d", result.returncode)
    if result.stderr.strip():
        log.debug("codex stderr: %s", result.stderr.strip())
    log.debug("codex stdout:\n%s", result.stdout)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return "", f"codex exited with code {result.returncode}{detail}"

    assistant_parts: list[str] = []
    for line in result.stdout.splitlines():
        text = _parse_codex_line(line)
        if text:
            assistant_parts.append(text)

    text = "\n".join(assistant_parts)
    log.debug("Extracted assistant text: %s", text[:500])

    if not text.strip():
        return "", "codex returned no output"
    return text, ""


def _run_codex_prompt_streaming(
    cmd: list[str],
    timeout: int,
    log: logging.Logger,
) -> tuple[str, str]:
    """Stream codex JSONL output, printing agent messages as they arrive."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return "", "'codex' not found on PATH — install with: npm i -g @openai/codex"

    assistant_parts: list[str] = []
    assert proc.stdout is not None  # mypy
    for line in proc.stdout:
        text = _parse_codex_line(line)
        if text:
            assistant_parts.append(text)
            print(text)

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return "", f"codex timed out after {timeout}s"

    assert proc.stderr is not None  # mypy
    stderr = proc.stderr.read().strip()
    log.debug("codex exit code: %d", proc.returncode)
    if stderr:
        log.debug("codex stderr: %s", stderr)

    if proc.returncode != 0:
        detail = f": {stderr}" if stderr else ""
        return "", f"codex exited with code {proc.returncode}{detail}"

    combined = "\n".join(assistant_parts)
    log.debug("Extracted assistant text: %s", combined[:500])

    if not combined.strip():
        return "", "codex returned no output"
    return combined, ""


def _suggest_steps_with_codex(
    goal: str,
    working_directory: str,
    model: str,
    step_count: int = 3,
) -> tuple[list[str], str]:
    """Return (steps, error_reason). error_reason is empty on success."""
    prompt = (
        "Create a concise implementation plan.\n"
        f"Goal: {goal}\n"
        f"Return exactly {step_count} one-line steps.\n"
        "Output format: each line starts with '- ' and contains only the step text."
    )
    text, err = _run_codex_prompt(prompt, working_directory, model, stream=True)
    if err:
        return [], err

    steps: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            steps.append(stripped[2:].strip())
        elif stripped.startswith("* "):
            steps.append(stripped[2:].strip())

    parsed = [s for s in steps if s][:step_count]
    if not parsed:
        return [], "codex returned no parseable steps"
    return parsed, ""


def _explore_codebase_with_codex(
    goal: str,
    working_directory: str,
    model: str,
) -> tuple[str, str]:
    """Explore the codebase for context relevant to *goal*.

    Returns (exploration_text, error). *error* is empty on success.
    """
    prompt = (
        "You are exploring a codebase to prepare for an implementation task.\n"
        f"Goal: {goal}\n\n"
        "Do the following:\n"
        "1. Identify the project structure and key directories.\n"
        "2. Find files most relevant to the goal.\n"
        "3. Note patterns, frameworks, and conventions used.\n"
        "4. Summarise your findings so a planner can create detailed steps.\n\n"
        "Output your findings as free-form text with clear headings."
    )
    return _run_codex_prompt(prompt, working_directory, model, timeout=180, stream=True)


def _parse_plan_output(text: str, step_count: int) -> tuple[list[dict], str]:
    """Parse structured plan output into (steps, summary).

    Expected format per step:
        STEP <n>: <description>
        FILES: <paths>
        PROMPT: <detailed codex instruction>

    Also looks for:
        SUMMARY: <one-line approach description>
    """
    summary = ""
    summary_match = re.search(r"SUMMARY:\s*(.+)", text)
    if summary_match:
        summary = summary_match.group(1).strip()

    steps: list[dict] = []
    # Match STEP blocks — greedy capture up to next STEP or end
    step_pattern = re.compile(
        r"STEP\s+\d+:\s*(.+?)(?:\n|$)"
        r"(?:.*?FILES:\s*(.+?)(?:\n|$))?"
        r"(?:.*?PROMPT:\s*(.+?)(?=\nSTEP\s+\d+:|\Z))",
        re.DOTALL | re.IGNORECASE,
    )
    for m in step_pattern.finditer(text):
        description = m.group(1).strip()
        files_raw = (m.group(2) or "").strip()
        codex_prompt = (m.group(3) or "").strip()

        files = [f.strip() for f in re.split(r"[,\n]+", files_raw) if f.strip()] if files_raw else []
        step = {
            "description": description,
            "codex_prompt": codex_prompt or None,
            "files": files,
        }
        steps.append(step)

    return steps[:step_count], summary


def _generate_plan_with_codex(
    goal: str,
    exploration_context: str,
    working_directory: str,
    model: str,
    step_count: int = 5,
) -> tuple[list[dict], str, str]:
    """Generate a structured plan using exploration context.

    Returns (steps, summary, error). *error* is empty on success.
    Each step dict has: description, codex_prompt, files.
    """
    # Truncate exploration context to avoid token limits
    truncated_context = exploration_context[:4000]
    if len(exploration_context) > 4000:
        truncated_context += "\n... (truncated)"

    prompt = (
        "You are a senior engineer creating an implementation plan.\n\n"
        f"GOAL: {goal}\n\n"
        f"CODEBASE EXPLORATION:\n{truncated_context}\n\n"
        f"Create exactly {step_count} implementation steps. "
        "For each step, output in this exact format:\n\n"
        "SUMMARY: <one-line description of the overall approach>\n\n"
        "STEP 1: <short description>\n"
        "FILES: <comma-separated file paths to modify>\n"
        "PROMPT: <detailed instruction for codex to execute this step>\n\n"
        "STEP 2: <short description>\n"
        "FILES: <comma-separated file paths>\n"
        "PROMPT: <detailed instruction>\n\n"
        "... and so on.\n\n"
        "Make each PROMPT detailed enough that codex can execute it autonomously. "
        "Reference specific files, functions, and patterns from the exploration."
    )
    text, err = _run_codex_prompt(prompt, working_directory, model, stream=True)
    if err:
        return [], "", err

    steps, summary = _parse_plan_output(text, step_count)
    if not steps:
        return [], "", "codex returned no parseable plan steps"
    return steps, summary, ""


def _write_plan_markdown(
    goal: str,
    summary: str,
    exploration_text: str,
    steps: list[dict],
    exit_conditions: list[dict],
) -> Path:
    """Write a human-readable plan to ``docs/plans/YYYY-MM-DD-<slug>.md``.

    Returns the path to the written file.
    """
    from datetime import datetime

    now = datetime.now()
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:40].rstrip("-")
    filename = f"{now.strftime('%Y-%m-%d')}-{slug}.md"
    plan_dir = Path("docs/plans")
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / filename

    lines: list[str] = []
    lines.append(f"# Plan: {goal}")
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    if summary:
        lines.append("## Summary")
        lines.append(summary)
        lines.append("")

    if exploration_text:
        lines.append("## Exploration Findings")
        # Condense to first 2000 chars
        condensed = exploration_text[:2000]
        if len(exploration_text) > 2000:
            condensed += "\n\n... (truncated)"
        lines.append(condensed)
        lines.append("")

    lines.append("## Steps")
    for i, step in enumerate(steps, 1):
        lines.append(f"### Step {i}: {step['description']}")
        if step.get("files"):
            lines.append(f"- Files: {', '.join(step['files'])}")
        if step.get("codex_prompt"):
            lines.append(f"- Codex prompt: {step['codex_prompt']}")
        lines.append("")

    if exit_conditions:
        lines.append("## Exit Conditions")
        for ec in exit_conditions:
            if ec.get("type") == "command":
                lines.append(f"- Command: `{ec.get('command', '')}`")
            elif ec.get("type") == "playwright":
                lines.append(f"- Playwright: {ec.get('url', '')}")
                for a in ec.get("assertions", []):
                    lines.append(f"  - {a}")
        lines.append("")

    plan_path.write_text("\n".join(lines))
    return plan_path


def cmd_spec(args: argparse.Namespace) -> int:
    """Smart spec command: explore → plan → output."""
    config = load_config(args.config)

    # 1. Get goal
    goal = args.prompt or _prompt("What do you want to build/fix?")
    if not goal:
        print("A prompt is required.", file=sys.stderr)
        return 1
    _drain_stdin()

    # 2. Get exit condition
    if args.playwright_url:
        assertions_raw = args.assertions or "Page loads;No visible errors"
        assertions = [a.strip() for a in assertions_raw.split(";") if a.strip()]
        exit_condition: dict = {
            "type": "playwright",
            "url": args.playwright_url,
            "assertions": assertions,
        }
    else:
        command = args.exit_condition or _prompt(
            "Verification command", "pytest -q"
        )
        _drain_stdin()
        exit_condition = {
            "type": "command",
            "command": command,
            "expect_exit_code": 0,
        }

    # 3. Resolve working_dir and model
    if args.working_directory:
        working_directories = [d.strip() for d in args.working_directory.split(",") if d.strip()]
    else:
        working_directories = list(config.working_directories)
    model = args.model or config.model
    step_count = args.steps

    # 4. EXPLORE phase (skipped with --no-explore)
    exploration_text = ""
    if not args.no_explore:
        print("\nExploring codebase with Codex...\n")
        exploration_text, explore_err = _explore_codebase_with_codex(
            goal, working_directories[0], model,
        )
        if explore_err:
            print(f"Exploration failed: {explore_err}")
            print("Falling back to simple planning.\n")
        else:
            print("Exploration complete.\n")

    # 5. PLAN phase
    steps: list[dict] = []
    summary = ""

    if exploration_text:
        print("Generating detailed plan...\n")
        steps, summary, plan_err = _generate_plan_with_codex(
            goal, exploration_text, working_directories[0], model, step_count,
        )
        if plan_err:
            print(f"Smart planning failed: {plan_err}")
            print("Falling back to simple planning.\n")
            steps = []

    # Fallback to simple codex planning
    if not steps:
        print("Generating plan with Codex...\n")
        simple_steps, codex_err = _suggest_steps_with_codex(
            goal, working_directories[0], model, step_count,
        )
        if simple_steps:
            steps = [
                {"description": s, "codex_prompt": None, "files": []}
                for s in simple_steps
            ]
        else:
            print(f"Codex failed: {codex_err}\nEntering steps manually.\n")

    # Fallback to manual entry
    if not steps:
        print("Enter steps (blank description to finish):")
        i = 1
        while True:
            desc = _prompt(f"Step {i} description")
            if not desc:
                break
            steps.append({"description": desc, "codex_prompt": None, "files": []})
            i += 1

    if not steps:
        print("At least one step is required.", file=sys.stderr)
        return 1

    # 6. REVIEW phase
    if summary:
        print(f"Summary: {summary}\n")
    for i, step in enumerate(steps, 1):
        line = f"  {i}. {step['description']}"
        if step.get("files"):
            line += f"  [{', '.join(step['files'])}]"
        print(line)
        if step.get("codex_prompt"):
            prompt_preview = step["codex_prompt"][:80]
            if len(step["codex_prompt"]) > 80:
                prompt_preview += "..."
            print(f"     Codex: {prompt_preview}")
    print()

    choice = _prompt("Accept plan? [Y/n/r=regenerate]", "y").lower()
    if choice in ("r", "regenerate"):
        # Re-run planning without exploration (already have context)
        if exploration_text:
            steps, summary, plan_err = _generate_plan_with_codex(
                goal, exploration_text, working_directories[0], model, step_count,
            )
            if plan_err or not steps:
                print("Regeneration failed. Keeping original plan.")
        else:
            simple_steps, _ = _suggest_steps_with_codex(
                goal, working_directories[0], model, step_count,
            )
            if simple_steps:
                steps = [
                    {"description": s, "codex_prompt": None, "files": []}
                    for s in simple_steps
                ]
    elif choice not in ("y", "yes", ""):
        print("Aborted.")
        return 1

    # 7. OUTPUT phase
    # Build spec YAML steps
    spec_steps = []
    for i, step in enumerate(steps, 1):
        entry: dict = {"id": f"step{i}", "description": step["description"]}
        if step.get("codex_prompt"):
            entry["codex_prompt"] = step["codex_prompt"]
        spec_steps.append(entry)

    default_output = args.output or _slugify_filename(goal)
    output_path = _prompt("Output", default_output)
    output = Path(output_path)

    max_retries = args.max_retries
    if max_retries is None:
        max_retries = int(_prompt("Max retries", str(config.max_retries)))

    raw = {
        "version": 1,
        "goal": goal,
        "context": {
            "working_directory": working_directories,
            "model": model,
        },
        "steps": spec_steps,
        "exit_conditions": [exit_condition],
        "max_retries": max_retries,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)
    print(f"\nCreated spec: {output}")

    # Write markdown plan file (unless --no-plan-file or no exploration)
    if exploration_text and not args.no_plan_file:
        plan_path = _write_plan_markdown(
            goal, summary, exploration_text, steps, [exit_condition],
        )
        print(f"Created plan: {plan_path}")

    # 8. Optional auto-run
    should_run = args.run
    if not should_run:
        run_choice = _prompt("Run spec now? [y/N]", "n").lower()
        should_run = run_choice in ("y", "yes")

    if should_run:
        print(f"\nRunning: zipilot run {output}\n")
        run_args = argparse.Namespace(
            config=args.config,
            spec=str(output),
            approve=True,
            verbose=getattr(args, "verbose", False),
        )
        return cmd_run(run_args)

    return 0


def cmd_create_spec(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    # 1. Get prompt (goal)
    goal = args.prompt or _prompt("Prompt")
    if not goal:
        print("A prompt is required.", file=sys.stderr)
        return 1
    _drain_stdin()

    # 2. Get verification command / exit condition
    if args.playwright_url:
        assertions_raw = args.assertions or "Page loads;No visible errors"
        assertions = [a.strip() for a in assertions_raw.split(";") if a.strip()]
        exit_condition: dict = {
            "type": "playwright",
            "url": args.playwright_url,
            "assertions": assertions,
        }
    else:
        command = args.exit_condition or _prompt(
            "Verification command", "pytest -q"
        )
        _drain_stdin()
        exit_condition = {
            "type": "command",
            "command": command,
            "expect_exit_code": 0,
        }

    # 3. Resolve working_dir and model from config (or flag overrides)
    if args.working_directory:
        working_directories = [d.strip() for d in args.working_directory.split(",") if d.strip()]
    else:
        working_directories = list(config.working_directories)
    model = args.model or config.model

    # 4. Always draft steps with Codex
    print("\nGenerating plan with Codex...\n")
    steps: list[dict] = []
    drafted, codex_err = _suggest_steps_with_codex(
        goal=goal,
        working_directory=working_directories[0],
        model=model,
    )

    if drafted:
        for i, step in enumerate(drafted, start=1):
            print(f"  {i}. {step}")
        print()
        accept = _prompt("Accept plan? [Y/n]", "y").lower() in ("y", "yes", "")
        if accept:
            steps = [
                {"id": f"step{i}", "description": s}
                for i, s in enumerate(drafted, start=1)
            ]
    else:
        print(f"Codex failed: {codex_err}\nEntering steps manually.\n")

    # 5. Fall back to manual entry if no steps accepted
    if not steps:
        print("Enter steps (blank description to finish):")
        i = 1
        while True:
            desc = _prompt(f"Step {i} description")
            if not desc:
                break
            steps.append({"id": f"step{i}", "description": desc})
            i += 1

    if not steps:
        print("At least one step is required.", file=sys.stderr)
        return 1

    # 6. Output path (auto-suggested from prompt)
    default_output = args.output or _slugify_filename(goal)
    output_path = _prompt("Output", default_output)
    output = Path(output_path)

    # 7. Max retries
    max_retries = args.max_retries
    if max_retries is None:
        max_retries = int(_prompt("Max retries", str(config.max_retries)))

    # 8. Write YAML
    raw = {
        "version": 1,
        "goal": goal,
        "context": {
            "working_directory": working_directories,
            "model": model,
        },
        "steps": steps,
        "exit_conditions": [exit_condition],
        "max_retries": max_retries,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)

    print(f"\nCreated spec: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zipilot",
        description="Autonomous control plane for Codex CLI sessions",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to config.yaml (default: bundled config)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Load spec and execute FSM loop")
    p_run.add_argument("spec", help="Path to spec YAML file")
    p_run.add_argument("--approve", action="store_true", help="Auto-approve spec")

    # resume
    sub.add_parser("resume", help="Resume from persisted state")

    # status
    sub.add_parser("status", help="Show current FSM state")

    # validate
    p_val = sub.add_parser("validate", help="Validate a spec without running")
    p_val.add_argument("spec", help="Path to spec YAML file")

    # tools
    sub.add_parser("tools", help="List registered tools")

    # create-spec
    p_create = sub.add_parser("create-spec", help="Interactively create a spec YAML")
    p_create.add_argument("prompt", nargs="?", default=None, help="Goal / prompt text")
    p_create.add_argument("--output", "-o", default=None, help="Output spec path (default: auto-generated from prompt)")
    p_create.add_argument("--exit-condition", "-e", default=None, help="Verification command (default: pytest -q)")
    p_create.add_argument("--playwright-url", default=None, help="Use Playwright exit condition with this URL")
    p_create.add_argument("--assertions", default=None, help="Playwright assertions (semicolon-separated)")
    p_create.add_argument("--working-directory", default=None, help="Working directory override")
    p_create.add_argument("--model", default=None, help="Model override")
    p_create.add_argument("--max-retries", type=int, default=None, help="Max retries")

    # spec (smart planning)
    p_spec = sub.add_parser("spec", help="Smart spec: explore codebase, plan, and generate spec")
    p_spec.add_argument("prompt", nargs="?", default=None, help="Goal / prompt text")
    p_spec.add_argument("--output", "-o", default=None, help="Output spec path")
    p_spec.add_argument("--exit-condition", "-e", default=None, help="Verification command (default: pytest -q)")
    p_spec.add_argument("--playwright-url", default=None, help="Use Playwright exit condition with this URL")
    p_spec.add_argument("--assertions", default=None, help="Playwright assertions (semicolon-separated)")
    p_spec.add_argument("--working-directory", default=None, help="Working directory override")
    p_spec.add_argument("--model", default=None, help="Model override")
    p_spec.add_argument("--max-retries", type=int, default=None, help="Max retries")
    p_spec.add_argument("--run", action="store_true", help="Auto-run spec after creation")
    p_spec.add_argument("--steps", type=int, default=5, help="Number of steps (default: 5)")
    p_spec.add_argument("--no-explore", action="store_true", help="Skip codebase exploration")
    p_spec.add_argument("--no-plan-file", action="store_true", help="Skip writing markdown plan file")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    commands = {
        "run": cmd_run,
        "resume": cmd_resume,
        "status": cmd_status,
        "validate": cmd_validate,
        "tools": cmd_tools,
        "create-spec": cmd_create_spec,
        "spec": cmd_spec,
    }

    if args.command is None:
        parser.print_help()
        return 0

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)
