"""Built-in tool: diagnose and restart unhealthy Docker containers."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)


class DockerTool(Tool):
    @property
    def name(self) -> str:
        return "docker"

    @property
    def description(self) -> str:
        return "Check Docker container health and restart unhealthy/exited containers"

    def can_handle(self, error_info: str) -> bool:
        keywords = [
            "docker",
            "container",
            "unhealthy",
            "compose",
            "connection refused",
            "503",
            "502",
            "econnrefused",
        ]
        return any(kw in error_info.lower() for kw in keywords)

    def run(self, context: dict[str, Any]) -> ToolOutput:
        cwd = context.get("working_directory", ".")
        timeout = context.get("timeout", 30)

        # Direct command mode
        command = context.get("command")
        if command:
            return self._run_command(command, cwd, timeout)

        # Pre-flight or auto-recover mode
        return self._check_and_restart(cwd, timeout)

    def _check_and_restart(self, cwd: str, timeout: int) -> ToolOutput:
        """Check for unhealthy/exited containers and restart them."""
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
        except FileNotFoundError:
            return ToolOutput(success=True, message="Docker not found on PATH, skipping")
        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message="docker ps timed out")

        if result.returncode != 0:
            return ToolOutput(
                success=False,
                message=f"docker ps failed: {result.stderr.strip()}",
            )

        lines = result.stdout.strip().splitlines()
        if not lines:
            return ToolOutput(success=True, message="No containers running")

        unhealthy: list[str] = []
        for line in lines:
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            name, status = parts
            status_lower = status.lower()
            if "unhealthy" in status_lower or status_lower.startswith("exited"):
                unhealthy.append(name)

        if not unhealthy:
            return ToolOutput(success=True, message="All containers healthy")

        # Restart unhealthy containers
        restarted: list[str] = []
        failed: list[str] = []
        for name in unhealthy:
            try:
                restart = subprocess.run(
                    ["docker", "restart", name],
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout,
                )
                if restart.returncode == 0:
                    restarted.append(name)
                else:
                    failed.append(name)
            except (subprocess.TimeoutExpired, Exception) as exc:
                log.warning("Failed to restart container %s: %s", name, exc)
                failed.append(name)

        if failed:
            return ToolOutput(
                success=False,
                message=f"Restarted {restarted}, failed to restart {failed}",
                data={"restarted": restarted, "failed": failed},
            )

        return ToolOutput(
            success=True,
            message=f"Restarted {len(restarted)} container(s): {', '.join(restarted)}",
            data={"restarted": restarted},
        )

    def _run_command(self, command: str, cwd: str, timeout: int) -> ToolOutput:
        """Run an arbitrary docker command."""
        log.info("docker command: %s (cwd=%s)", command, cwd)
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
            if len(output) > 10_000:
                output = output[:5_000] + "\n...[truncated]...\n" + output[-5_000:]
            return ToolOutput(
                success=result.returncode == 0,
                message=output or "(no output)",
                data={"exit_code": result.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message=f"Docker command timed out after {timeout}s")
        except Exception as e:
            return ToolOutput(success=False, message=f"Docker command failed: {e}")
