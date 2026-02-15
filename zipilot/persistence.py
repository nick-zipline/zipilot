"""Session-scoped state persistence under sessions/<session_id>/state.json."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from zipilot.states import State

DEFAULT_SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


@dataclass
class SessionEntry:
    session_id: str
    step_id: str
    exit_code: int
    token_estimate: int
    summary: str


@dataclass
class PersistedState:
    session_id: str
    state: str  # State.name
    spec_path: str
    step_index: int = 0
    retry_count: int = 0
    session_history: list[dict] = field(default_factory=list)
    continuation_context: str = ""
    completed: bool = False

    def to_state_enum(self) -> State:
        return State[self.state]


def _slugify(goal: str) -> str:
    """Slugify a goal string for session directory names."""
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")
    slug = slug[:30].rstrip("-")
    return slug or "session"


def _state_path(session_id: str, sessions_dir: Path) -> Path:
    return sessions_dir / session_id / "state.json"


def create_session(goal: str, sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> str:
    """Create a new session directory and mark it active."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    base_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_slugify(goal)}"
    session_id = base_id
    suffix = 1
    while (sessions_dir / session_id).exists():
        session_id = f"{base_id}-{suffix}"
        suffix += 1

    (sessions_dir / session_id).mkdir(parents=True, exist_ok=False)
    (sessions_dir / ".active").write_text(session_id, encoding="utf-8")
    return session_id


def save_state(ps: PersistedState, sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> None:
    """Atomically write session state to JSON (tmp + rename)."""
    if not ps.session_id:
        raise ValueError("PersistedState.session_id is required")

    path = _state_path(ps.session_id, sessions_dir)
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


def load_session(
    session_id: str, sessions_dir: Path = DEFAULT_SESSIONS_DIR
) -> PersistedState | None:
    """Load state for a specific session ID."""
    path = _state_path(session_id, sessions_dir)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return PersistedState(**data)


def load_state(sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> PersistedState | None:
    """Load state for the active session, if any."""
    active_path = sessions_dir / ".active"
    if not active_path.exists():
        return None

    session_id = active_path.read_text(encoding="utf-8").strip()
    if not session_id:
        return None

    return load_session(session_id, sessions_dir)


def complete_session(sessions_dir: Path = DEFAULT_SESSIONS_DIR) -> None:
    """Mark active session as completed and clear active pointer."""
    active = load_state(sessions_dir)
    if active is None:
        return

    active.completed = True
    save_state(active, sessions_dir)
    (sessions_dir / ".active").write_text("", encoding="utf-8")


def cleanup_sessions(
    sessions_dir: Path = DEFAULT_SESSIONS_DIR, max_sessions: int = 10
) -> None:
    """Prune oldest completed sessions beyond max_sessions."""
    completed_ids: list[str] = []
    for child in sessions_dir.iterdir() if sessions_dir.exists() else []:
        if not child.is_dir():
            continue
        ps = load_session(child.name, sessions_dir)
        if ps is not None and ps.completed:
            completed_ids.append(child.name)

    completed_ids.sort()
    if max_sessions < 0:
        to_delete = completed_ids
    elif max_sessions == 0:
        to_delete = completed_ids
    else:
        to_delete = completed_ids[:-max_sessions]

    for session_id in to_delete:
        shutil.rmtree(sessions_dir / session_id, ignore_errors=True)


def list_sessions(
    sessions_dir: Path = DEFAULT_SESSIONS_DIR,
) -> list[tuple[str, PersistedState]]:
    """List all persisted sessions sorted by session_id."""
    results: list[tuple[str, PersistedState]] = []
    if not sessions_dir.exists():
        return results

    for child in sessions_dir.iterdir():
        if not child.is_dir():
            continue
        ps = load_session(child.name, sessions_dir)
        if ps is not None:
            results.append((child.name, ps))

    results.sort(key=lambda item: item[0])
    return results
