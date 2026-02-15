"""Built-in tool: run arbitrary shell commands (tests, builds, lint)."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)


class RunCommandTool(Tool):
    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output and exit code"

    def can_handle(self, error_info: str) -> bool:
        keywords = ["test", "build", "lint", "compile", "bazel", "npm", "make"]
        return any(kw in error_info.lower() for kw in keywords)

    def run(self, context: dict[str, Any]) -> ToolOutput:
        command = context.get("command", "")
        cwd = context.get("working_directory", ".")
        timeout = context.get("timeout", 300)

        if not command:
            return ToolOutput(success=False, message="No command provided")

        log.info("run_command: %s (cwd=%s)", command, cwd)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            # Truncate long output
            if len(output) > 10_000:
                output = output[:5_000] + "\n...[truncated]...\n" + output[-5_000:]
            return ToolOutput(
                success=result.returncode == 0,
                message=output or "(no output)",
                data={"exit_code": result.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolOutput(success=False, message=f"Command failed: {e}")
