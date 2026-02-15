"""CLI entry point for zipilot."""

from __future__ import annotations

import argparse
import logging
import sys

from zipilot.config import load_config
from zipilot.fsm import FSMEngine
from zipilot.persistence import load_state
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
    ps = load_state(config.state_path)

    if ps is None:
        print("No persisted state found. Nothing to resume.")
        return 1

    spec = load_spec(ps.spec_path) if ps.spec_path else None
    if spec is None:
        print("Cannot resume: spec path not found in persisted state.")
        return 1

    registry = create_default_registry()
    engine = FSMEngine(spec=spec, config=config, registry=registry)
    final = engine.resume(ps)

    if final == State.COMPLETED:
        print("\nResumed and completed.")
        return 0
    else:
        print(f"\nStopped in state: {final.name}")
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ps = load_state(config.state_path)

    if ps is None:
        print("No active session.")
        return 0

    print(f"State:      {ps.state}")
    print(f"Spec:       {ps.spec_path}")
    print(f"Step:       {ps.step_index + 1}")
    print(f"Retries:    {ps.retry_count}")
    print(f"Sessions:   {len(ps.session_history)}")
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
    }

    if args.command is None:
        parser.print_help()
        return 0

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)
