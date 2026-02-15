"""CLI entry point for zipilot."""

from __future__ import annotations

import argparse
import json
import logging
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


def _suggest_steps_with_codex(
    goal: str,
    working_directory: str,
    model: str,
    step_count: int = 3,
) -> list[str]:
    prompt = (
        "Create a concise implementation plan.\n"
        f"Goal: {goal}\n"
        f"Return exactly {step_count} one-line steps.\n"
        "Output format: each line starts with '- ' and contains only the step text."
    )
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

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    assistant_lines: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "message" and obj.get("role") == "assistant":
            content = obj.get("content", "")
            if isinstance(content, str):
                assistant_lines.extend(content.splitlines())
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        assistant_lines.extend(part["text"].splitlines())

    steps: list[str] = []
    for line in assistant_lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            steps.append(stripped[2:].strip())
        elif stripped.startswith("* "):
            steps.append(stripped[2:].strip())

    return [s for s in steps if s][:step_count]


def cmd_create_spec(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    output = Path(args.output)
    goal = args.goal or _prompt("Goal")
    working_directory = args.working_directory or _prompt(
        "Working directory", config.working_directory
    )
    model = args.model or _prompt("Model", config.model)
    max_retries = args.max_retries
    if max_retries is None:
        max_retries = int(_prompt("Max retries", str(config.max_retries)))

    use_codex = args.with_codex
    if not use_codex:
        use_codex = _prompt("Use Codex to draft steps? (y/N)", "n").lower() in ("y", "yes")

    steps: list[dict] = []
    if use_codex:
        drafted = _suggest_steps_with_codex(
            goal=goal,
            working_directory=working_directory,
            model=model,
        )
        if drafted:
            print("\nDrafted steps:")
            for i, step in enumerate(drafted, start=1):
                print(f"  {i}. {step}")
            accept = _prompt("Use drafted steps? (Y/n)", "y").lower() in ("y", "yes")
            if accept:
                steps = [{"id": f"step{i}", "description": s} for i, s in enumerate(drafted, start=1)]

    if not steps:
        print("\nEnter steps (blank description to finish):")
        i = 1
        while True:
            desc = _prompt(f"Step {i} description")
            if not desc:
                break
            step_id = _prompt(f"Step {i} id", f"step{i}")
            steps.append({"id": step_id, "description": desc})
            i += 1

    if not steps:
        print("At least one step is required.", file=sys.stderr)
        return 1

    ec_type = _prompt("Exit condition type (command/playwright)", "command").lower()
    if ec_type == "playwright":
        url = _prompt("Playwright URL", "http://localhost:3000")
        assertions_text = _prompt(
            "Assertions (semicolon-separated)",
            "Page loads;No visible errors",
        )
        assertions = [a.strip() for a in assertions_text.split(";") if a.strip()]
        exit_condition: dict = {
            "type": "playwright",
            "url": url,
            "assertions": assertions,
        }
    else:
        command = _prompt("Verification command", "pytest -q")
        expect_exit_code = int(_prompt("Expected exit code", "0"))
        exit_condition = {
            "type": "command",
            "command": command,
            "expect_exit_code": expect_exit_code,
        }

    raw = {
        "version": 1,
        "goal": goal,
        "context": {
            "working_directory": working_directory,
            "model": model,
        },
        "steps": steps,
        "exit_conditions": [exit_condition],
        "max_retries": max_retries,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)

    print(f"Created spec: {output}")
    print(f"Validate with: zipilot validate {output}")
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
    p_create.add_argument("output", nargs="?", default="specs/new-spec.yaml", help="Output spec path")
    p_create.add_argument("--goal", default=None, help="Goal text")
    p_create.add_argument("--working-directory", default=None, help="Working directory for the spec context")
    p_create.add_argument("--model", default=None, help="Model for the spec context")
    p_create.add_argument("--max-retries", type=int, default=None, help="Max retries")
    p_create.add_argument("--with-codex", action="store_true", help="Use Codex CLI to draft initial steps")

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
    }

    if args.command is None:
        parser.print_help()
        return 0

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)
