"""FSM engine — run loop and state hooks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zipilot.config import Config
from zipilot.console import (
    get_console,
    print_error,
    print_muted,
    print_phase,
    print_step,
    print_success,
    print_warning,
)
from zipilot.context import ContextTracker
from zipilot.persistence import (
    PersistedState,
    cleanup_sessions,
    complete_session,
    create_session,
    save_state,
)
from zipilot.session import CodexRunner, SessionRecord
from zipilot.spec import ExitCondition, Spec
from zipilot.states import Event, State, transition
from zipilot.tools.registry import ToolOutput, ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class FSMContext:
    """Mutable runtime context passed through state handlers."""

    spec: Spec
    config: Config
    registry: ToolRegistry
    session_id: str
    spec_path: str = ""
    state: State = State.IDLE
    step_index: int = 0
    retry_count: int = 0
    session_history: list[SessionRecord] = field(default_factory=list)
    continuation_context: str = ""
    last_error: str = ""
    last_session: SessionRecord | None = None
    user_input_callback: Any = None  # Callable[str, str] | None


class FSMEngine:
    """Drives the zipilot FSM from IDLE through COMPLETED (or NEEDS_INPUT)."""

    def __init__(
        self,
        spec: Spec,
        config: Config,
        registry: ToolRegistry,
        auto_approve: bool = False,
        user_input_callback: Any = None,
        spec_path: str | None = None,
        session_id: str | None = None,
    ) -> None:
        current_session_id = session_id or create_session(spec.goal, config.sessions_path)
        self.ctx = FSMContext(
            spec=spec,
            config=config,
            registry=registry,
            session_id=current_session_id,
            spec_path=str(Path(spec_path).resolve()) if spec_path else "",
            user_input_callback=user_input_callback,
        )
        self.auto_approve = auto_approve
        self.runner = CodexRunner(
            working_directory=spec.context.working_directory,
            model=spec.context.model,
            context_tracker=ContextTracker(
                window_tokens=config.context_window_tokens,
                warn_pct=config.context_warn_pct,
                handoff_pct=config.context_handoff_pct,
            ),
        )

        self._handlers: dict[State, Any] = {
            State.IDLE: self._handle_idle,
            State.SPEC_CREATION: self._handle_spec_creation,
            State.EXECUTING: self._handle_executing,
            State.VERIFYING: self._handle_verifying,
            State.BLOCKED: self._handle_blocked,
            State.RECOVERING: self._handle_recovering,
            State.NEEDS_INPUT: self._handle_needs_input,
            State.CONTEXT_HANDOFF: self._handle_context_handoff,
            State.COMPLETED: self._handle_completed,
        }

    def run(self) -> State:
        """Run the FSM loop until COMPLETED or NEEDS_INPUT."""
        log.info("FSM starting in state %s", self.ctx.state.name)
        self._emit(Event.SPEC_LOADED)

        while self.ctx.state not in (State.COMPLETED, State.NEEDS_INPUT):
            handler = self._handlers.get(self.ctx.state)
            if handler is None:
                log.error("No handler for state %s", self.ctx.state.name)
                break
            handler()
            self._persist()

        log.info("FSM finished in state %s", self.ctx.state.name)
        if self.ctx.state == State.COMPLETED:
            complete_session(self.ctx.config.sessions_path)
            cleanup_sessions(self.ctx.config.sessions_path, self.ctx.config.max_sessions)
        return self.ctx.state

    def resume(self, ps: PersistedState) -> State:
        """Resume FSM from persisted state."""
        self.ctx.state = ps.to_state_enum()
        self.ctx.step_index = ps.step_index
        self.ctx.retry_count = ps.retry_count
        self.ctx.continuation_context = ps.continuation_context
        self.ctx.session_id = ps.session_id
        self.ctx.spec_path = ps.spec_path
        log.info("Resuming FSM from state %s, step %d", self.ctx.state.name, self.ctx.step_index)

        # If we were in a transient state, move back to EXECUTING
        if self.ctx.state in (State.RECOVERING, State.CONTEXT_HANDOFF):
            self.ctx.state = State.EXECUTING

        return self.run_from_current()

    def run_from_current(self) -> State:
        """Run from the current state (used by resume)."""
        while self.ctx.state not in (State.COMPLETED, State.NEEDS_INPUT):
            handler = self._handlers.get(self.ctx.state)
            if handler is None:
                break
            handler()
            self._persist()

        if self.ctx.state == State.COMPLETED:
            complete_session(self.ctx.config.sessions_path)
            cleanup_sessions(self.ctx.config.sessions_path, self.ctx.config.max_sessions)
        return self.ctx.state

    # -- State handlers --

    def _handle_idle(self) -> None:
        # Already emitted SPEC_LOADED in run(), now in SPEC_CREATION
        pass

    def _handle_spec_creation(self) -> None:
        """Show spec to user and get approval."""
        spec = self.ctx.spec
        console = get_console()
        console.rule(f"[bold]SPEC: {spec.goal}[/bold]")
        console.print(f"[bold]Steps ({len(spec.steps)}):[/bold]")
        for i, step in enumerate(spec.steps):
            console.print(f"  {i+1}. [muted][{step.id}][/muted] {step.description}")
        console.print(f"[bold]Exit conditions ({len(spec.exit_conditions)}):[/bold]")
        for ec in spec.exit_conditions:
            if ec.type == "command":
                console.print(f"  - command: {ec.command} (expect exit {ec.expect_exit_code})")
            elif ec.type == "playwright":
                console.print(f"  - playwright: {ec.url}")
                for a in ec.assertions:
                    console.print(f"      - {a}")
        print_muted(f"Max retries: {spec.max_retries}")
        dirs = spec.context.working_directories
        if len(dirs) == 1:
            print_muted(f"Working dir: {dirs[0]}")
        else:
            print_muted(f"Working dirs: {', '.join(dirs)}")
        print_muted(f"Model: {spec.context.model}")
        console.rule()

        if self.auto_approve:
            log.info("Auto-approving spec")
            self._emit(Event.APPROVED)
        else:
            answer = input("\nApprove and start execution? [y/N] ").strip().lower()
            if answer in ("y", "yes"):
                self._emit(Event.APPROVED)
            else:
                print("Spec not approved. Exiting.")
                self.ctx.state = State.COMPLETED

    def _handle_executing(self) -> None:
        """Run the current step via Codex."""
        if self.ctx.step_index >= len(self.ctx.spec.steps):
            log.info("All steps completed, moving to verification")
            self._emit(Event.STEP_DONE)
            return

        step = self.ctx.spec.steps[self.ctx.step_index]
        prompt = step.codex_prompt or step.description

        print_step(
            self.ctx.step_index + 1,
            len(self.ctx.spec.steps),
            f"[{step.id}] {step.description}",
        )

        # Check context before starting
        if self.runner.tracker.should_handoff:
            log.info("Context usage too high, handing off")
            self._emit(Event.CONTEXT_HIGH)
            return

        record = self.runner.run(
            prompt=prompt,
            continuation_context=self.ctx.continuation_context,
        )
        self.ctx.last_session = record
        self.ctx.session_history.append(record)

        if record.succeeded:
            log.info("Step [%s] completed successfully (session=%s)",
                     step.id, record.session_id)
            self.ctx.continuation_context = record.summary
            self.ctx.step_index += 1
            self.ctx.retry_count = 0

            if self.ctx.step_index >= len(self.ctx.spec.steps):
                self._emit(Event.STEP_DONE)
            # else: stay in EXECUTING for next step
        else:
            log.warning("Step [%s] failed (exit_code=%d)", step.id, record.exit_code)
            self.ctx.last_error = record.summary or f"Exit code {record.exit_code}"
            self._emit(Event.ERROR)

    def _handle_verifying(self) -> None:
        """Run all exit conditions."""
        print_phase("Verifying exit conditions...")
        all_passed = True
        working_dir = str(Path(self.ctx.spec.context.working_directory).expanduser())
        console = get_console()

        # Pre-flight: check docker container health
        preflight_cfg = self.ctx.spec.preflight
        if preflight_cfg.docker.enabled:
            docker_tool = self.ctx.registry.get("docker")
            if docker_tool is not None:
                docker_result = docker_tool.run({
                    "preflight": True,
                    "working_directory": working_dir,
                    "socket_paths": preflight_cfg.docker.socket_paths,
                    "health_check": preflight_cfg.docker.health_check,
                    "recovery_command": preflight_cfg.docker.recovery_command,
                })
                if docker_result.data and docker_result.data.get("restarted"):
                    log.info("Pre-flight: restarted containers: %s", docker_result.data["restarted"])
                    console.print(
                        f"  [warning]Pre-flight:[/warning] Restarted "
                        f"{len(docker_result.data['restarted'])} container(s)"
                    )
                elif not docker_result.success:
                    log.warning("Pre-flight docker check failed: %s", docker_result.message)
                    console.print(f"  [warning]Pre-flight:[/warning] {docker_result.message[:200]}")

        # Pre-flight: run custom commands
        run_command_tool = self.ctx.registry.get("run_command")
        for cmd_cfg in preflight_cfg.commands:
            if run_command_tool is None:
                log.warning("run_command tool not registered, skipping preflight command")
                break
            cmd_cwd = cmd_cfg.working_directory or working_dir
            cmd_cwd = str(Path(cmd_cwd).expanduser())
            cmd_result = run_command_tool.run({
                "command": cmd_cfg.command,
                "working_directory": cmd_cwd,
                "timeout": cmd_cfg.timeout,
            })
            if not cmd_result.success:
                if cmd_cfg.fail_on_error:
                    log.error("Pre-flight command failed: %s", cmd_cfg.command)
                    console.print(f"  [error]Pre-flight:[/error] {cmd_cfg.command} failed")
                    self.ctx.last_error = f"Preflight command failed: {cmd_result.message}"
                    self._emit(Event.SOME_FAILED)
                    return
                else:
                    log.warning("Pre-flight command failed (non-fatal): %s", cmd_cfg.command)
                    console.print(
                        f"  [warning]Pre-flight:[/warning] {cmd_cfg.command} failed (skipping)"
                    )

        for i, ec in enumerate(self.ctx.spec.exit_conditions):
            result = self._run_exit_condition(ec, working_dir)
            if result.success:
                console.print(f"  [success][PASS][/success] Exit condition {i+1} ({ec.type}): {result.message[:200]}")
            else:
                console.print(f"  [error][FAIL][/error] Exit condition {i+1} ({ec.type}): {result.message[:200]}")
            if not result.success:
                all_passed = False
                self.ctx.last_error = result.message

        if all_passed:
            self._emit(Event.ALL_PASSED)
        else:
            self._emit(Event.SOME_FAILED)

    def _handle_blocked(self) -> None:
        """Attempt recovery via tools or escalate."""
        self.ctx.retry_count += 1
        log.info("Blocked (retry %d/%d): %s",
                 self.ctx.retry_count, self.ctx.spec.max_retries,
                 self.ctx.last_error[:200])

        if self.ctx.retry_count > self.ctx.spec.max_retries:
            log.warning("Max retries exceeded, needs user input")
            self._emit(Event.MAX_RETRIES)
            return

        # Try to find a recovery tool
        tool = self.ctx.registry.find_recovery_tool(self.ctx.last_error)
        if tool is not None:
            log.info("Found recovery tool: %s", tool.name)
            self._emit(Event.TOOL)
        else:
            log.warning("No recovery tool found, escalating")
            self._emit(Event.MAX_RETRIES)

    def _handle_recovering(self) -> None:
        """Run the selected recovery tool."""
        tool = self.ctx.registry.find_recovery_tool(self.ctx.last_error)
        if tool is None:
            self.ctx.last_error = "No recovery tool available"
            self._emit(Event.ERROR)
            return

        working_dir = str(Path(self.ctx.spec.context.working_directory).expanduser())
        print_phase(f"Recovering with tool: {tool.name}")

        result = tool.run({
            "error_info": self.ctx.last_error,
            "working_directory": working_dir,
            "pattern": self.ctx.last_error[:100],
            "command": "",  # Tools inspect error_info to decide
        })

        if result.success:
            log.info("Recovery succeeded: %s", result.message[:200])
            self.ctx.continuation_context += f"\nRecovery ({tool.name}): {result.message[:500]}"
            self._emit(Event.RECOVERED)
        else:
            log.warning("Recovery failed: %s", result.message[:200])
            self.ctx.last_error = result.message
            self._emit(Event.ERROR)

    def _handle_needs_input(self) -> None:
        """Prompt user for input to unblock."""
        from rich.panel import Panel

        content = (
            f"Current step: {self.ctx.step_index + 1}/{len(self.ctx.spec.steps)}\n"
            f"Last error: {self.ctx.last_error[:500]}\n"
            f"Retry count: {self.ctx.retry_count}"
        )
        get_console().print(Panel(content, title="NEEDS USER INPUT", border_style="warning"))

        if self.ctx.user_input_callback:
            user_input = self.ctx.user_input_callback(
                "How should we proceed?", self.ctx.last_error
            )
        else:
            user_input = input("\nProvide guidance (or 'abort' to stop): ").strip()

        if user_input.lower() == "abort":
            print("Aborting.")
            self.ctx.state = State.COMPLETED
            return

        self.ctx.continuation_context += f"\nUser guidance: {user_input}"
        self.ctx.retry_count = 0
        self._emit(Event.INPUT_RECEIVED)

    def _handle_context_handoff(self) -> None:
        """Save continuation context and start fresh session."""
        print_phase("Context window high — performing handoff to new session")
        if self.ctx.last_session:
            self.ctx.continuation_context = (
                f"Previous session summary (step {self.ctx.step_index}): "
                f"{self.ctx.last_session.summary[:1000]}"
            )
        self.runner.tracker.reset()
        self._emit(Event.HANDOFF_COMPLETE)

    def _handle_completed(self) -> None:
        """Terminal state."""
        console = get_console()
        console.rule("[success]COMPLETED[/success]")
        console.print(f"[bold]Goal:[/bold] {self.ctx.spec.goal}")
        console.print(f"Sessions run: {len(self.ctx.session_history)}")

    # -- Helpers --

    def _emit(self, event: Event) -> None:
        """Transition to the next state via the given event."""
        old = self.ctx.state
        self.ctx.state = transition(old, event)
        log.info("Transition: %s + %s -> %s", old.name, event.name, self.ctx.state.name)

    def _persist(self) -> None:
        """Save current state to disk."""
        ps = PersistedState(
            session_id=self.ctx.session_id,
            state=self.ctx.state.name,
            spec_path=self.ctx.spec_path,
            step_index=self.ctx.step_index,
            retry_count=self.ctx.retry_count,
            session_history=[
                {
                    "session_id": s.session_id,
                    "exit_code": s.exit_code,
                    "summary": s.summary[:500],
                    "token_estimate": s.token_estimate,
                }
                for s in self.ctx.session_history
            ],
            continuation_context=self.ctx.continuation_context[:2000],
        )
        try:
            save_state(ps, self.ctx.config.sessions_path)
        except Exception as e:
            log.warning("Failed to persist state: %s", e)

    def _run_exit_condition(self, ec: ExitCondition, working_dir: str) -> ToolOutput:
        """Run a single exit condition check."""
        if ec.type == "command":
            tool = self.ctx.registry.get("run_command")
            if tool is None:
                return ToolOutput(success=False, message="run_command tool not registered")
            return tool.run({
                "command": ec.command,
                "working_directory": working_dir,
            })
        elif ec.type == "playwright":
            tool = self.ctx.registry.get("playwright_qa")
            if tool is None:
                return ToolOutput(success=False, message="playwright_qa tool not registered")
            return tool.run({
                "url": ec.url,
                "assertions": ec.assertions,
                "working_directory": working_dir,
                "model": self.ctx.spec.context.model,
            })
        else:
            return ToolOutput(success=False, message=f"Unknown exit condition type: {ec.type}")
