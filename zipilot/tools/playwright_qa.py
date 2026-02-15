"""Built-in tool: visual QA via Codex + Playwright MCP.

Leverages the globally-configured Playwright MCP server
(``npx @playwright/mcp@latest`` in ``~/.codex/config.toml``).
Runs a ``codex exec`` session whose prompt instructs Codex to use
Playwright to navigate to a URL, interact with the page, and verify
expected behavior.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from zipilot.tools.registry import Tool, ToolOutput

log = logging.getLogger(__name__)


class PlaywrightQATool(Tool):
    @property
    def name(self) -> str:
        return "playwright_qa"

    @property
    def description(self) -> str:
        return "Run visual QA via Codex + Playwright MCP to verify a URL meets assertions"

    def can_handle(self, error_info: str) -> bool:
        keywords = ["ui", "page", "render", "visual", "browser", "playwright", "frontend"]
        return any(kw in error_info.lower() for kw in keywords)

    def run(self, context: dict[str, Any]) -> ToolOutput:
        url = context.get("url", "")
        assertions = context.get("assertions", [])
        model = context.get("model", "gpt-5.3-codex")
        working_directory = context.get("working_directory", "~/github/cloud")

        if not url:
            return ToolOutput(success=False, message="No URL provided for Playwright QA")
        if not assertions:
            return ToolOutput(success=False, message="No assertions provided for Playwright QA")

        assertion_text = "\n".join(f"  - {a}" for a in assertions)
        prompt = (
            f"Use Playwright (via the MCP server) to navigate to {url} and verify "
            f"the following assertions. For each assertion, report PASS or FAIL with "
            f"a brief explanation of what you observed.\n\n"
            f"Assertions:\n{assertion_text}\n\n"
            f"After checking all assertions, output a single JSON object with:\n"
            f'  {{"all_passed": true/false, "results": [{{"assertion": "...", '
            f'"status": "PASS"/"FAIL", "observation": "..."}}]}}'
        )

        cmd = [
            "codex", "exec",
            "--json",
            "--cd", str(working_directory),
            "-m", model,
            "-s", "workspace-write",
            prompt,
        ]

        log.info("playwright_qa: checking %s with %d assertions", url, len(assertions))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Try to extract JSON result from output
            qa_result = self._extract_result(result.stdout)
            if qa_result is not None:
                all_passed = qa_result.get("all_passed", False)
                results = qa_result.get("results", [])
                summary_parts = []
                for r in results:
                    summary_parts.append(
                        f"  [{r.get('status', '?')}] {r.get('assertion', '?')}: "
                        f"{r.get('observation', '')}"
                    )
                summary = "\n".join(summary_parts)
                return ToolOutput(
                    success=all_passed,
                    message=f"Playwright QA {'PASSED' if all_passed else 'FAILED'}:\n{summary}",
                    data=qa_result,
                )

            # Fallback: couldn't parse structured output
            output = result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout
            return ToolOutput(
                success=result.returncode == 0,
                message=f"Playwright QA completed (unstructured):\n{output}",
            )

        except subprocess.TimeoutExpired:
            return ToolOutput(success=False, message="Playwright QA timed out after 120s")
        except FileNotFoundError:
            return ToolOutput(success=False, message="codex CLI not found on PATH")

    def _extract_result(self, stdout: str) -> dict | None:
        """Try to find and parse the JSON result from Codex JSONL output."""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            # Try parsing as JSONL message with content
            try:
                obj = json.loads(line)
                content = ""
                if isinstance(obj, dict):
                    if obj.get("type") == "message" and obj.get("role") == "assistant":
                        content = obj.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(
                                p.get("text", "") for p in content if isinstance(p, dict)
                            )
                if not content:
                    continue
                # Look for JSON object in content
                return self._find_json_object(content)
            except json.JSONDecodeError:
                # Try as plain text
                result = self._find_json_object(line)
                if result is not None:
                    return result
        return None

    def _find_json_object(self, text: str) -> dict | None:
        """Find and parse a JSON object containing 'all_passed' in text."""
        start = text.find("{")
        while start != -1:
            # Find matching closing brace
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : i + 1])
                            if "all_passed" in obj:
                                return obj
                        except json.JSONDecodeError:
                            pass
                        break
            start = text.find("{", start + 1)
        return None
