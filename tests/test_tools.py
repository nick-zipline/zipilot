"""Tests for tool registry and built-in tools."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from zipilot.tools.registry import Tool, ToolOutput, ToolRegistry, create_default_registry
from zipilot.tools.run_command import RunCommandTool
from zipilot.tools.grep_codebase import GrepCodebaseTool
from zipilot.tools.wait_for_ci import WaitForCITool
from zipilot.tools.playwright_qa import PlaywrightQATool
from zipilot.tools.docker_tool import DockerTool


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = RunCommandTool()
        registry.register(tool)
        assert registry.get("run_command") is tool
        assert "run_command" in registry
        assert len(registry) == 1

    def test_get_missing(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_list_tools(self):
        registry = create_default_registry()
        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert names == {"run_command", "wait_for_ci", "grep_codebase", "playwright_qa", "docker"}

    def test_find_recovery_tool_test_error(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("test suite failed with 3 errors")
        assert tool is not None
        assert tool.name == "run_command"

    def test_find_recovery_tool_import_error(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("ImportError: cannot import name 'Foo'")
        assert tool is not None
        assert tool.name == "grep_codebase"

    def test_find_recovery_tool_ui_error(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("render failed in browser")
        assert tool is not None
        assert tool.name == "playwright_qa"

    def test_find_recovery_tool_docker_error(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("container unhealthy, connection refused")
        assert tool is not None
        assert tool.name == "docker"

    def test_find_recovery_tool_ci_error(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("CI pipeline check failed")
        assert tool is not None
        assert tool.name == "wait_for_ci"

    def test_find_recovery_tool_unknown(self):
        registry = create_default_registry()
        tool = registry.find_recovery_tool("some completely unknown error xyz")
        assert tool is None


class TestRunCommandTool:
    def test_successful_command(self):
        tool = RunCommandTool()
        result = tool.run({"command": "echo hello", "working_directory": "/tmp"})
        assert result.success is True
        assert "hello" in result.message

    def test_failing_command(self):
        tool = RunCommandTool()
        result = tool.run({"command": "false", "working_directory": "/tmp"})
        assert result.success is False
        assert result.data["exit_code"] != 0

    def test_no_command(self):
        tool = RunCommandTool()
        result = tool.run({})
        assert result.success is False
        assert "No command" in result.message

    def test_can_handle(self):
        tool = RunCommandTool()
        assert tool.can_handle("test failed") is True
        assert tool.can_handle("build error") is True
        assert tool.can_handle("random stuff") is False


class TestGrepCodebaseTool:
    def test_no_pattern(self):
        tool = GrepCodebaseTool()
        result = tool.run({"working_directory": "/tmp"})
        assert result.success is False
        assert "No search pattern" in result.message

    def test_can_handle(self):
        tool = GrepCodebaseTool()
        assert tool.can_handle("undefined variable") is True
        assert tool.can_handle("import error") is True
        assert tool.can_handle("random stuff") is False

    @patch("zipilot.tools.grep_codebase.subprocess.run")
    def test_successful_search(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file.py:10:match here\nfile.py:20:another match\n",
            stderr="",
        )
        tool = GrepCodebaseTool()
        result = tool.run({"pattern": "match", "working_directory": "/tmp"})
        assert result.success is True
        assert result.data["match_count"] == 2

    @patch("zipilot.tools.grep_codebase.subprocess.run")
    def test_no_matches(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        tool = GrepCodebaseTool()
        result = tool.run({"pattern": "nonexistent", "working_directory": "/tmp"})
        assert result.success is True
        assert result.data["match_count"] == 0


class TestPlaywrightQATool:
    def test_no_url(self):
        tool = PlaywrightQATool()
        result = tool.run({"assertions": ["check"]})
        assert result.success is False
        assert "No URL" in result.message

    def test_no_assertions(self):
        tool = PlaywrightQATool()
        result = tool.run({"url": "http://localhost"})
        assert result.success is False
        assert "No assertions" in result.message

    def test_can_handle(self):
        tool = PlaywrightQATool()
        assert tool.can_handle("render error in browser") is True
        assert tool.can_handle("ui broken") is True
        assert tool.can_handle("database timeout") is False
        # "page" alone should NOT match (too broad)
        assert tool.can_handle("The page showed an error state") is False

    def test_extract_result_valid(self):
        tool = PlaywrightQATool()
        result = tool._find_json_object(
            'some text {"all_passed": true, "results": []} more text'
        )
        assert result is not None
        assert result["all_passed"] is True

    def test_extract_result_no_match(self):
        tool = PlaywrightQATool()
        result = tool._find_json_object("no json here")
        assert result is None


class TestWaitForCITool:
    def test_can_handle(self):
        tool = WaitForCITool()
        assert tool.can_handle("CI check failed") is True
        assert tool.can_handle("pipeline error") is True
        assert tool.can_handle("random stuff") is False

    @patch("zipilot.tools.wait_for_ci.subprocess.run")
    def test_ci_passes_immediately(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All checks passed\n",
            stderr="",
        )
        tool = WaitForCITool()
        result = tool.run({
            "working_directory": "/tmp",
            "poll_interval": 1,
            "max_wait": 5,
        })
        assert result.success is True
        assert "passed" in result.message

    @patch("zipilot.tools.wait_for_ci.subprocess.run")
    def test_ci_fails(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="check fail\n",
            stderr="",
        )
        tool = WaitForCITool()
        result = tool.run({
            "working_directory": "/tmp",
            "poll_interval": 1,
            "max_wait": 2,
        })
        assert result.success is False


class TestDockerTool:
    def test_can_handle(self):
        tool = DockerTool()
        assert tool.can_handle("container unhealthy") is True
        assert tool.can_handle("docker restart needed") is True
        assert tool.can_handle("connection refused on port 5432") is True
        assert tool.can_handle("503 service unavailable") is True
        assert tool.can_handle("random stuff") is False

    @patch("zipilot.tools.docker_tool.subprocess.run")
    def test_preflight_all_healthy(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="web\tUp 2 hours (healthy)\ndb\tUp 2 hours (healthy)\n",
            stderr="",
        )
        tool = DockerTool()
        result = tool.run({"preflight": True, "working_directory": "/tmp"})
        assert result.success is True
        assert "healthy" in result.message.lower()

    @patch("zipilot.tools.docker_tool.subprocess.run")
    def test_preflight_restarts_unhealthy(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="web\tUp 2 hours (healthy)\ndb\tUp 2 hours (unhealthy)\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="db\n", stderr=""),
        ]
        tool = DockerTool()
        result = tool.run({"preflight": True, "working_directory": "/tmp"})
        assert result.success is True
        assert result.data is not None
        assert "db" in result.data["restarted"]

    @patch("zipilot.tools.docker_tool.subprocess.run")
    def test_preflight_restarts_exited(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="web\tUp 2 hours (healthy)\ndb\tExited (1) 5 minutes ago\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="db\n", stderr=""),
        ]
        tool = DockerTool()
        result = tool.run({"preflight": True, "working_directory": "/tmp"})
        assert result.success is True
        assert result.data is not None
        assert "db" in result.data["restarted"]

    @patch("zipilot.tools.docker_tool.subprocess.run")
    def test_docker_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("docker not found")
        tool = DockerTool()
        result = tool.run({"preflight": True, "working_directory": "/tmp"})
        assert result.success is True
        assert "not found" in result.message.lower()

    @patch("zipilot.tools.docker_tool.subprocess.run")
    def test_direct_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="container list\n",
            stderr="",
        )
        tool = DockerTool()
        result = tool.run({
            "command": "docker ps",
            "working_directory": "/tmp",
        })
        assert result.success is True
        assert "container list" in result.message
