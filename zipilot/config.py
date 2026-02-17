"""YAML config loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Config:
    working_directories: list[str] = field(default_factory=lambda: ["~/github/cloud"])
    model: str = "gpt-5.3-codex"
    context_window_tokens: int = 192_000
    context_warn_pct: int = 80
    context_handoff_pct: int = 90
    max_retries: int = 3
    sessions_dir: str = "sessions"
    max_sessions: int = 10

    @property
    def working_directory(self) -> str:
        """Primary working directory (first in the list)."""
        return self.working_directories[0]

    @property
    def sessions_path(self) -> Path:
        path = Path(self.sessions_dir).expanduser()
        if path.is_absolute():
            return path
        return _DEFAULT_CONFIG.parent / path

    @property
    def work_dir(self) -> Path:
        return Path(self.working_directory).expanduser()


def load_config(path: str | Path | None = None) -> Config:
    """Load config from YAML, falling back to defaults."""
    if path is None:
        path = _DEFAULT_CONFIG
    path = Path(path)
    if not path.exists():
        return Config()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    # Normalize working_directory (str or list) → working_directories
    if "working_directory" in raw:
        wd = raw.pop("working_directory")
        raw["working_directories"] = [wd] if isinstance(wd, str) else wd
    return Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
