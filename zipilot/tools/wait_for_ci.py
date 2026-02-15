"""Built-in tool: poll CI status, inspired by cloud repo's wait_for_ci.py."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)


class WaitForCITool(Tool):
    @property
    def name(self) -> str:
        return "wait_for_ci"

    @property
    def description(self) -> str:
        return "Poll CI checks on the current branch until they pass or fail"

    def can_handle(self, error_info: str) -> bool:
        keywords = ["ci", "pipeline", "check", "workflow", "github actions"]
        return any(kw in error_info.lower() for kw in keywords)

    def run(self, context: dict[str, Any]) -> ToolOutput:
        cwd = context.get("working_directory", ".")
        poll_interval = context.get("poll_interval", 30)
        max_wait = context.get("max_wait", 600)
        branch = context.get("branch", "")

        cmd_base = ["gh", "pr", "checks"]
        if branch:
            cmd_base.extend(["--branch", branch])

        elapsed = 0
        last_output = ""

        while elapsed < max_wait:
            try:
                result = subprocess.run(
                    cmd_base,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=30,
                )
                last_output = result.stdout + result.stderr

                if result.returncode == 0:
                    return ToolOutput(
                        success=True,
                        message=f"CI checks passed after {elapsed}s\n{last_output}",
                    )

                # Check for definitive failure (not just "pending")
                if "fail" in last_output.lower() and "pending" not in last_output.lower():
                    return ToolOutput(
                        success=False,
                        message=f"CI checks failed after {elapsed}s\n{last_output}",
                    )

            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return ToolOutput(success=False, message=f"CI polling error: {e}")

            log.info("CI still pending, waiting %ds... (%d/%ds)", poll_interval, elapsed, max_wait)
            time.sleep(poll_interval)
            elapsed += poll_interval

        return ToolOutput(
            success=False,
            message=f"CI timed out after {max_wait}s\n{last_output}",
        )
