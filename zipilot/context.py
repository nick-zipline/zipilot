"""Heuristic context window tracker for Codex sessions."""

from __future__ import annotations

from dataclasses import dataclass

CHARS_PER_TOKEN = 4  # rough heuristic


@dataclass
class ContextTracker:
    """Track estimated token usage within a Codex session.

    Each ``codex exec`` invocation gets a fresh context window, so the tracker
    resets per invocation.  Call :meth:`add_chars` as JSONL conversation turns
    are streamed.
    """

    window_tokens: int = 192_000
    warn_pct: int = 80
    handoff_pct: int = 90

    _char_count: int = 0

    @property
    def estimated_tokens(self) -> int:
        return self._char_count // CHARS_PER_TOKEN

    @property
    def usage_pct(self) -> float:
        if self.window_tokens == 0:
            return 100.0
        return (self.estimated_tokens / self.window_tokens) * 100

    @property
    def should_warn(self) -> bool:
        """True when usage >= warn threshold (no new phases)."""
        return self.usage_pct >= self.warn_pct

    @property
    def should_handoff(self) -> bool:
        """True when usage >= handoff threshold (start new session)."""
        return self.usage_pct >= self.handoff_pct

    def add_chars(self, n: int) -> None:
        self._char_count += n

    def add_text(self, text: str) -> None:
        self._char_count += len(text)

    def reset(self) -> None:
        self._char_count = 0
