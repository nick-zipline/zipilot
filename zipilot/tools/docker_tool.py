"""Built-in tool: diagnose and restart unhealthy Docker containers."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)

DEFAULT_SOCKET_PATHS = [
    "/var/run/docker.sock",
    os.path.expanduser("~/.docker/run/docker.sock"),
]


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
        socket_paths = context.get("socket_paths", [])
        health_check = context.get("health_check")
        recovery_command = context.get("recovery_command")
        return self._check_and_restart(
            cwd, timeout,
            socket_paths=socket_paths,
            health_check=health_check,
            recovery_command=recovery_command,
        )

    def _find_docker_env(self, custom_paths: list[str] | None = None) -> dict[str, str] | None:
        """Find a working Docker socket and return env dict with DOCKER_HOST set.

        Search order: custom_paths, DOCKER_HOST env var, default paths.
        Returns None if no socket is found.
        """
        candidates: list[str] = []

        # Custom paths first
        if custom_paths:
            for p in custom_paths:
                # Strip unix:// prefix for filesystem check
                path = p.removeprefix("unix://")
                candidates.append(os.path.expandvars(os.path.expanduser(path)))

        # DOCKER_HOST env var
        docker_host = os.environ.get("DOCKER_HOST")
        if docker_host:
            path = docker_host.removeprefix("unix://")
            expanded = os.path.expandvars(os.path.expanduser(path))
            if expanded not in candidates:
                candidates.append(expanded)

        # Default paths
        for path in DEFAULT_SOCKET_PATHS:
            expanded = os.path.expandvars(os.path.expanduser(path))
            if expanded not in candidates:
                candidates.append(expanded)

        for path in candidates:
            try:
                st = os.stat(path)
                if stat.S_ISSOCK(st.st_mode):
                    log.info("Found Docker socket at %s", path)
                    return {"DOCKER_HOST": f"unix://{path}"}
            except (OSError, FileNotFoundError):
                continue

        return None

    def _check_and_restart(
        self,
        cwd: str,
        timeout: int,
        socket_paths: list[str] | None = None,
        health_check: str | None = None,
        recovery_command: str | None = None,
    ) -> ToolOutput:
        """Check for unhealthy/exited containers and restart them."""
        docker_env = self._find_docker_env(socket_paths)
        if docker_env is None:
            return ToolOutput(success=True, message="No Docker socket found, skipping preflight")

        # Merge docker env into current env for subprocess calls
        env = {**os.environ, **docker_env}

        # Health check: custom command or default docker ps
        if health_check:
            try:
                result = subprocess.run(
                    health_check,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout,
                    env=env,
                )
            except FileNotFoundError:
                return ToolOutput(success=True, message="Docker not found on PATH, skipping")
            except subprocess.TimeoutExpired:
                return ToolOutput(success=False, message=f"Health check timed out: {health_check}")

            if result.returncode == 0:
                return ToolOutput(success=True, message="Health check passed")

            # Health check failed — try recovery
            if recovery_command:
                return self._run_recovery(recovery_command, cwd, timeout, env)
            return ToolOutput(
                success=False,
                message=f"Health check failed: {result.stderr.strip() or result.stdout.strip()}",
            )

        # Default: docker ps to find unhealthy containers
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
                env=env,
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

        # Recovery: custom command or per-container restart
        if recovery_command:
            return self._run_recovery(recovery_command, cwd, timeout, env)

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
                    env=env,
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

    def _run_recovery(
        self, command: str, cwd: str, timeout: int, env: dict[str, str]
    ) -> ToolOutput:
        """Run a custom recovery command."""
        log.info("Running recovery command: %s", command)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
                env=env,
            )
            if result.returncode == 0:
                return ToolOutput(
                    success=True,
                    message=f"Recovery command succeeded: {result.stdout.strip()[:500]}",
                )
            return ToolOutput(
                success=False,
                message=f"Recovery command failed: {result.stderr.strip() or result.stdout.strip()}",
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message=f"Recovery command timed out: {command}")
        except Exception as e:
            return ToolOutput(success=False, message=f"Recovery command error: {e}")

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
