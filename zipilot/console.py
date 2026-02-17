"""Thin wrapper around rich.Console with project theme and helper functions."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.theme import Theme

_THEME = Theme(
    {
        "phase": "bold cyan",
        "success": "bold green",
        "error": "bold red",
        "warning": "bold yellow",
        "muted": "dim",
        "step": "bold white",
    }
)

_console: Console | None = None


def get_console() -> Console:
    """Return the singleton Console instance."""
    global _console
    if _console is None:
        _console = Console(theme=_THEME)
    return _console


def set_console(console: Console) -> None:
    """Replace the singleton Console (test seam)."""
    global _console
    _console = console


def print_phase(label: str) -> None:
    """Print a bold cyan phase header."""
    get_console().print(f"\n[phase]{label}[/phase]\n")


def print_success(msg: str) -> None:
    """Print a bold green success message."""
    get_console().print(f"[success]{msg}[/success]")


def print_error(msg: str) -> None:
    """Print a bold red error message."""
    get_console().print(f"[error]{msg}[/error]")


def print_warning(msg: str) -> None:
    """Print a bold yellow warning message."""
    get_console().print(f"[warning]{msg}[/warning]")


def print_markdown(text: str) -> None:
    """Render markdown text via rich."""
    get_console().print(Markdown(text))


def print_step(index: int, total: int, desc: str) -> None:
    """Print a styled step indicator like '>>> Step 1/3: ...'."""
    get_console().print(f"\n[step]>>> Step {index}/{total}:[/step] {desc}")


def print_muted(text: str) -> None:
    """Print dim text for secondary info."""
    get_console().print(f"[muted]{text}[/muted]")
