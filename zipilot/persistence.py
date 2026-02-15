"""State persistence to ~/.zipilot/state.json."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from zipilot.states import State

DEFAULT_STATE_FILE = Path.home() / ".zipilot" / "state.json"


@dataclass
class SessionEntry:
    session_id: str
    step_id: str
    exit_code: int
    token_estimate: int
    summary: str


@dataclass
class PersistedState:
    state: str  # State.name
    spec_path: str
    step_index: int = 0
    retry_count: int = 0
    session_history: list[dict] = field(default_factory=list)
    continuation_context: str = ""

    def to_state_enum(self) -> State:
        return State[self.state]


def save_state(ps: PersistedState, path: Path = DEFAULT_STATE_FILE) -> None:
    """Atomically write state to JSON (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(ps)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=2)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_state(path: Path = DEFAULT_STATE_FILE) -> PersistedState | None:
    """Load persisted state, or None if no state file exists."""
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return PersistedState(**data)


def clear_state(path: Path = DEFAULT_STATE_FILE) -> None:
    """Remove the state file."""
    path.unlink(missing_ok=True)
