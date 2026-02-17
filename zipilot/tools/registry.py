"""Tool base class and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ToolOutput:
    success: bool
    message: str
    data: dict[str, Any] | None = None


class Tool(ABC):
    """Abstract base class for zipilot tools.

    Tools serve dual purpose: recovery (unblocking stuck states) AND
    verification (checking exit conditions).
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def run(self, context: dict[str, Any]) -> ToolOutput:
        """Execute the tool with the given context dict."""
        ...

    def can_handle(self, error_info: str) -> bool:
        """Return True if this tool can attempt to recover from the error."""
        return False


class ToolRegistry:
    """Registry of available tools, keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        log.debug("Registered tool: %s", tool.name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def find_recovery_tool(self, error_info: str) -> Tool | None:
        """Find the first tool that can handle the given error."""
        for tool in self._tools.values():
            if tool.can_handle(error_info):
                return tool
        return None

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def create_default_registry() -> ToolRegistry:
    """Create a registry pre-loaded with all built-in tools."""
    from zipilot.tools.docker_tool import DockerTool
    from zipilot.tools.grep_codebase import GrepCodebaseTool
    from zipilot.tools.playwright_qa import PlaywrightQATool
    from zipilot.tools.run_command import RunCommandTool
    from zipilot.tools.wait_for_ci import WaitForCITool

    registry = ToolRegistry()
    registry.register(RunCommandTool())
    registry.register(WaitForCITool())
    registry.register(GrepCodebaseTool())
    registry.register(DockerTool())
    registry.register(PlaywrightQATool())
    return registry
