"""Codex CLI wrapper — runs ``codex exec`` and parses JSONL output."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from zipilot.context import ContextTracker

log = logging.getLogger(__name__)


@dataclass
class SessionRecord:
    session_id: str = ""
    exit_code: int = -1
    output_lines: list[str] = field(default_factory=list)
    token_estimate: int = 0
    raw_jsonl: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Last non-empty output line, or empty string."""
        for line in reversed(self.output_lines):
            if line.strip():
                return line.strip()
        return ""

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class CodexRunner:
    """Wraps ``codex exec --json`` invocations."""

    def __init__(
        self,
        working_directory: str | Path = "~/github/cloud",
        model: str = "gpt-5.3-codex",
        context_tracker: ContextTracker | None = None,
    ) -> None:
        self.working_directory = Path(working_directory).expanduser()
        self.model = model
        self.tracker = context_tracker or ContextTracker()

    def run(
        self,
        prompt: str,
        continuation_context: str = "",
        timeout: int = 600,
    ) -> SessionRecord:
        """Execute a Codex CLI session and return parsed results.

        Args:
            prompt: The task prompt for Codex.
            continuation_context: Optional prior-session context to prepend.
            timeout: Max seconds before killing the process.
        """
        full_prompt = prompt
        if continuation_context:
            full_prompt = (
                f"CONTINUATION FROM PRIOR SESSION:\n{continuation_context}\n\n"
                f"CURRENT TASK:\n{prompt}"
            )

        cmd = [
            "codex", "exec",
            "--json",
            "--cd", str(self.working_directory),
            "-m", self.model,
            "-s", "workspace-write",
            full_prompt,
        ]

        log.info("Running: %s", " ".join(cmd[:6]) + " ...")
        self.tracker.reset()

        record = SessionRecord()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            record.exit_code = result.returncode
            record = self._parse_output(result.stdout, record)
        except subprocess.TimeoutExpired:
            log.warning("Codex session timed out after %ds", timeout)
            record.exit_code = -1
            record.output_lines.append(f"[TIMEOUT after {timeout}s]")
        except FileNotFoundError:
            log.error("codex CLI not found on PATH")
            record.exit_code = -1
            record.output_lines.append("[ERROR: codex CLI not found]")

        record.token_estimate = self.tracker.estimated_tokens
        return record

    def _parse_output(self, stdout: str, record: SessionRecord) -> SessionRecord:
        """Parse JSONL lines from codex exec --json output."""
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            self.tracker.add_text(line)
            try:
                obj = json.loads(line)
                record.raw_jsonl.append(obj)
                if obj.get("session_id"):
                    record.session_id = obj["session_id"]
                # Format 1 (current): item.completed
                if obj.get("type") == "item.completed":
                    item = obj.get("item", {})
                    if item.get("type") == "agent_message":
                        text = item.get("text", "")
                        if text:
                            record.output_lines.append(text)
                # Format 2 (legacy): message
                elif obj.get("type") == "message" and obj.get("role") == "assistant":
                    content = obj.get("content", "")
                    if isinstance(content, str):
                        record.output_lines.append(content)
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("text"):
                                record.output_lines.append(part["text"])
            except json.JSONDecodeError:
                # Not JSON — treat as plain text output
                record.output_lines.append(line)
        return record
