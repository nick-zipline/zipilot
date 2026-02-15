"""Built-in tool: search codebase with ripgrep."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)


class GrepCodebaseTool(Tool):
    @property
    def name(self) -> str:
        return "grep_codebase"

    @property
    def description(self) -> str:
        return "Search codebase with ripgrep for patterns, error messages, or symbols"

    def can_handle(self, error_info: str) -> bool:
        keywords = ["undefined", "import", "not found", "missing", "symbol", "reference"]
        return any(kw in error_info.lower() for kw in keywords)

    def run(self, context: dict[str, Any]) -> ToolOutput:
        pattern = context.get("pattern", "")
        cwd = context.get("working_directory", ".")
        file_glob = context.get("glob", "")
        max_results = context.get("max_results", 50)

        if not pattern:
            return ToolOutput(success=False, message="No search pattern provided")

        cmd = ["rg", "--no-heading", "--line-number", f"--max-count={max_results}", pattern]
        if file_glob:
            cmd.extend(["--glob", file_glob])

        log.info("grep_codebase: rg %s (cwd=%s)", pattern, cwd)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=30,
            )
            output = result.stdout
            if len(output) > 10_000:
                output = output[:10_000] + "\n...[truncated]..."

            if result.returncode == 0:
                lines = output.strip().splitlines()
                return ToolOutput(
                    success=True,
                    message=output,
                    data={"match_count": len(lines)},
                )
            elif result.returncode == 1:
                return ToolOutput(success=True, message="No matches found", data={"match_count": 0})
            else:
                return ToolOutput(success=False, message=f"rg error: {result.stderr}")
        except FileNotFoundError:
            return ToolOutput(success=False, message="ripgrep (rg) not found on PATH")
        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message="Search timed out")
